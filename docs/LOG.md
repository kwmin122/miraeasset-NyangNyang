# 작업 일지 (append-only, 최신이 아래)

형식: `## YYYY-MM-DD [모델] 슬라이스 — 제목` + 본문. 완료 보고·역설명·갈등 보고·실측치는 전부 여기에.

## 2026-08-07 [Fable] S0 — 워킹 스켈레톤 완료 기록 (기준선)

- 커밋 `c802edb`: FastAPI 서버(mock/baseline/sunwoo 모드) + PatchedSearcher 실검색 연동 + 평가셋 11문 + 채점기
- 채점 결과 (baseline=추출형 폴백, HCX 미연결): **8/11 통과, 근거 recall 0.8, 워밍업 후 0.1~0.2s/질의, 첫 호출 19s(지연 로딩 → S1 동기)**
- 실패 3건 분석:
  - TR-LIM-001/002: 폴백은 거절 응답 불가 — LLM 연결(S5) 전까지 기대된 실패
  - TR-NAME-001: 진짜 검색 미스 — "LIG넥스원" 옛 사명 질의가 2023-04-14 인도네시아 건 대신 2024-01-02 다른 LIG 계약을 검색 → **명섭에게 전달할 결함 #1**
- macOS 세그폴트(exit 139) 해결: faiss·torch 각자 libomp 로드 충돌 → `OMP_NUM_THREADS=1` + `device="cpu"` (search.py·config.py 반영, 제거 금지)
- 데이터 품질 (독립 검증 완료): chunk_id 중복 11,048개 사실 (257,186행 중 246,138 유니크) → 골드는 rcept_no+인용 기준 유지. correction_map의 superseded_by는 **리스트 값** 599건 → **명섭에게 전달할 확인 요청 #2**
- 유사 공시 함정 발견: M15X 질의 rank-1이 20240726800615(용인, 9.41조, 별건 투자)로 뜸 — T1-C-001은 "4월"로 고정해 안전. 향후 혼동 함정 문항 소재.

## 2026-08-07 [Sonnet] S1 — /ready + 기동 시 사전 로딩 완료 보고

**변경 파일**
- `server/app/main.py`: lifespan(`@asynccontextmanager`) 추가 — `AGENT_MODE != "mock"`이면 기동 시 데몬 스레드로 `_warmup()`(검색기 로드 + 카나리 검색 "SK하이닉스 신규시설투자") 실행. `_ready_state` 전역 dict로 loading/ready/error 추적. `GET /ready` 신설(준비 200 `{"status":"ready"}`, 로딩/실패 503 `JSONResponse`로 `{"status":"loading"}` / `{"status":"error","detail":...}` — `HTTPException`은 `{"detail":...}` 형태라 계약 위반이라 배제). `/health`·`/answer`는 무변경(sync def 유지 — async로 바꾸면 블로킹 워밍업이 이벤트루프를 막아 `/ready`가 응답 못하는 역설 발생하므로 의도적으로 손대지 않음). 워밍업 시작/완료/실패를 `logger`+`_log()`(logs/requests.jsonl)에 기록.
- `server/app/search.py`: `get_searcher()`에 `threading.Lock`(double-checked locking) 추가 — 백그라운드 워밍업과 첫 `/answer` 요청이 동시에 최초 로드를 시도해도 한쪽이 대기하도록 해 이중 로딩(메모리 2배) 방지.
- 건드리지 않음: `agents.py`, `config.py`(카나리 질의문은 `main.py` 모듈 상수로 하드코딩, 새 설정값 추가 안 함), `evalset/*`, `data/*`.

**검증 명령과 출력 (원문)**

1) 기동 → `/ready` 503→200 전환 시간
```
$ AGENT_MODE=baseline uvicorn app.main:app --port 8000  (백그라운드)
[uvicorn 로그] [ready] warmup start (agent_mode=baseline)
t=0s code=503 body={"status":"loading"}
t=1s code=503 body={"status":"loading"}
t=3s code=503 body={"status":"loading"}
...
t=11s code=503 body={"status":"loading"}
t=12s code=200 body={"status":"ready"}
READY after 12s
```

2) `/ready` 200 직후 첫 `/answer` 지연 (curl -G --data-urlencode)
```
$ curl -s -G --data-urlencode "question=SK하이닉스 신규시설투자 관련 공시 내용을 알려줘" \
    --data-urlencode "question_id=T-ready-first" http://127.0.0.1:8000/answer
elapsed_s= 0.1562798023223877
```
(3초 미만 — 완료기준 #2 충족. 응답 JSON은 5필드 스키마 정상, retrieved_context에 SK하이닉스 신규시설투자등 공시 5건 포함)

3) `python3 evalset/run_eval.py` (baseline 서버 유지한 채, repo 루트에서)
```
✅ T1-C-001  0.2s ev=1.0
✅ T1-C-002  0.2s ev=1.0
✅ T1-O-001  0.1s ev=1.0
✅ T3-C-001  0.2s ev=1.0
❌ TR-LIM-001 0.1s
❌ TR-LIM-002 0.1s
✅ TR-ATK-001 0.1s ev=1.0
✅ TR-ATK-002 0.2s ev=1.0
✅ TR-COR-001 0.2s ev=1.0
✅ T1-C-003  0.2s ev=1.0
❌ TR-NAME-001 0.2s ev=0.0

== 유형별 요약 ==
유형 1: 정답률 67% (6/9) | 근거recall 0.80 | 최대지연 0.2s
유형 2: 정답률 100% (1/1) | 근거recall 1.00 | 최대지연 0.1s
유형 3: 정답률 100% (1/1) | 근거recall 1.00 | 최대지연 0.2s
```
8/11 통과 (실패 3건 = TR-LIM-001/002, TR-NAME-001 — S0에서 이미 원인 파악된 그 3건과 동일), 유형1 근거recall 0.80 — 기존 기준선과 동일. **완료기준 #4 충족.**

4) `AGENT_MODE=mock` 기동 → `/ready` 즉시 200 + torch 미로딩(RSS)
```
$ AGENT_MODE=mock uvicorn app.main:app --port 8000  (백그라운드)
[uvicorn 로그] Application startup complete.  (※ "[ready] warmup start" 로그 없음 — 스레드 미기동 확인)
$ curl -s -w "\nHTTP_CODE:%{http_code}\n" http://127.0.0.1:8000/ready
{"status":"ready"}
HTTP_CODE:200
$ ps -eo pid,ppid,rss,command | grep "uvicorn app.main"
43707 43705  59040 .../uvicorn app.main:app --port 8000
```
RSS 59,040KB(~58MB) — torch/faiss(수백MB~GB대) 미로딩 확인. **완료기준 #3 충족.**

완료기준 #1(baseline 503→200 전환)·#2(첫 `/answer` <3s)·#3(mock 즉시 200+torch 미로딩)·#4(11문 8/11 동일) **전부 충족**.

**Brief에서 벗어난 것**: 없음. (보충 명세 — Fable 지시로 `mock` 모드 `/ready` 초기값을 `ready`로 고정, `sunwoo` 모드도 `baseline`과 동일하게 워밍업 대상에 포함 — 둘 다 반영함)

**남은 것 / 확신 없는 부분**
- 카나리 검색 "성공" 기준을 Brief 문구보다 한 단계 더 엄격하게 해석함: 예외 없이 끝나는 것뿐 아니라 `hits`가 1건 이상이어야 ready로 판정(0건이면 error 처리). 파이프라인이 켜져 있어도 검색이 비어있으면 이후 실제 평가 질의도 위험하다고 보고 방어적으로 넣었으나, Fable 검수 시 과한 제약인지 확인 필요.
- `_ready_state` dict의 `detail`→`status` 2필드 갱신은 원자적 쌍이 아님(각각은 GIL 하에 원자적). worker=1/동시성=1 전제(CONTEXT.md)라 실질적 위험은 없다고 판단해 별도 락 없이 둠.
- 워밍업 실패(error) 경로는 실제로 재현 테스트하지 않음(카나리가 항상 성공했음) — 예: `emb_dir` 오지정 등으로 강제 실패시켜 503 `{"status":"error","detail":...}` 형태를 직접 본 적은 없음. 코드 리뷰 수준의 확신.
- 테스트 종료 후 baseline·mock 서버 프로세스 모두 kill 완료, 포트 8000 비어있음 확인. git commit은 하지 않음(Fable 검수 대기).

## 2026-08-07 [Fable] S1 — 독립 검수: 통과

보고를 신뢰하지 않고 클린 상태에서 전 항목 재실행:

- 기준 #1: `/ready` 503(loading)→200 전환 **11.3s** ✅
- 기준 #2: ready 직후 첫 `/answer` **0.174s**, 5필드 스키마 정상 ✅
- 기준 #4: `run_eval.py` **8/11, 유형1 recall 0.80** — S0 기준선과 동일, 실패 3건도 동일(TR-LIM×2, TR-NAME) ✅
- 기준 #3: mock `/ready` 즉시 200, RSS **58MB** (torch 미로딩) ✅
- Sonnet 미검증 항목 보완: `EMB_DIR=/nonexistent`로 워밍업 강제 실패 → `/ready` 503 `{"status":"error","detail":"No module named 'search_patch'"}` — error 경로 실동작 확인 ✅
- 품질: diff 범위 준수(agents/config/evalset/data 무변경), double-checked locking 정석. 카나리 hits≥1 엄격화는 **승인** (0건이면 실평가도 위험 — 올바른 방어). `_ready_state` 2필드 비원자 갱신은 worker=1 전제에서 수용.

**추가 실측 (S3/S6에 중요): baseline 워밍업 완료 후 RSS 7.33GB (macOS).** FAISS 2.1+meta 3+모델 2.3GB 추정과 일치 — 4GB 초과가 실측으로 확정. S6 다이어트 트랙 발동 가능성 높음, S3에서 Docker 기준 재확인.

## 2026-08-07 [Fable] S2 — 검증·확정: 18문 완성

Sonnet 발굴 후보 8건을 전수 재검증(보고 신뢰 안 함 — 인용·수치 45개 체크 전부 원문 XML re-grep):

- **채택 7건 / 탈락 1건**: ⑤-3(LGES Ford 추적)은 ④-3·⑤-2와 문서쌍 중복이라 탈락. 나머지 전부 인용 실재·계산 정합 확인
- 계산 검증: LGES Freudenberg 해지금액 (2,795,000,000−110,000,000)×1,460.6 = 3,921,711,000,000 정수 검산 일치 (float 검산은 반올림 오차로 False — 함정 주의). 카카오 EB/CB ≈5.84배, 하이브 CB/유증 ≈7.96배
- **12문 목표 → 18문 상향** (Brief 자체 수정): 검증 통과 후보를 버릴 이유가 없음. 유형 분포 ①9 ②1 ③1 ④3 ⑤2 ⑥2 — 6유형 전부 커버
- 채점기 설계 반영: open 문항은 accept_patterns 커버리지 60% 기준. **must_not_patterns는 closed·프롬프트공격에서만 작동** (run_eval.py grade() 확인) → open의 정정 전 구금액 함정은 gold_answer 주석으로 문서화 (T4-O-001 285,039,000,000 / T4-O-002 62,800,063,776)
- evidence 교체 1건: T4-O-001 CB 근거를 원공시 20250522000332 → **최종 정정본 20250527000422**로 (하드룰 "정정 있는 건은 최신본 기준" + 실검색이 정정본을 rank-1로 반환. 금액 50,182,840,320 정정본 원문에 4회 실재 확인)

**18문 채점 완주 확인 (2회, baseline=추출형 폴백)**: 11/18 통과. 기존 11문은 8/11로 기준선 유지(실패 3건 동일). 신규 7문은 3/7:
- 통과: T4-O-003(ev 1.0) T5-C-002(ev 0.5) T6-O-001(ev 0.5) — 검색 rank-1이 정답 문서
- 실패 4건 전부 원인 파악: T4-O-001/002·T6-O-002는 폴백이 비교·연산 불가(기대된 실패, ev는 0.5~0.67), T5-C-001은 검색 미스(두산 원공시·해지공시 모두 미검색 — 2025-06-19 별건 계약이 잡힘. 복합추론 난이도 의도대로 높음, HCX 연동 후 재관찰)
- 유형별 근거recall: ④0.67 ⑤0.25 ⑥0.50 — multi-evidence 문항의 recall이 S5(HCX)·에이전트 라우팅의 핵심 개선 지표

완주 기준 충족 → **S2 확정**. 통과율은 완료 기준 아님(Brief 명시).

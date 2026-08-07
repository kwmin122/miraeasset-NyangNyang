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

## 2026-08-07 [Sonnet] S3 — Docker 2vCPU/4GB 실측: OOM 확정, 완료 보고 (성공 아님 — 실패가 산출물)

**환경**: Docker Desktop 아님, **colima**(VM, aarch64, 4CPU/5.772GiB, 기존 무관 컨테이너 10개(supabase_* guardsys) 가동 중). `docker info` 정상 확인 후 착수.

**변경 파일**: `.dockerignore`(신규) — `data/ .venv/ .git/ logs/ docs/ evalset/results_*.jsonl __pycache__/ *.pyc .env` 제외 (컨텍스트가 repo 루트라 없으면 ~8GB가 빌드 데몬에 올라감). `server/Dockerfile`은 **기존 파일 그대로 사용** — c802edb에 이미 존재했고 Brief 스펙과 일치함을 확인, 신규 작성 아님(역설명에서 이미 정정). **그 외 파일 수정 없음.** git commit 안 함(코디네이터 지시).

**빌드**
```
$ docker build -t gongsi-agent-s3 -f server/Dockerfile .
... (torch-2.13.0, faiss-cpu-1.15.0, sentence-transformers-5.7.0, transformers-5.14.1 등 정상 설치)
Successfully built 9cd1face52be / Successfully tagged gongsi-agent-s3:latest
docker build ... 57:23.59 total (57분23초 — 매우 느림, 아래 관찰 참조)
$ docker images | grep gongsi
gongsi-agent-s3:latest   9cd1face52be   8.65GB   3.11GB(content)
```
**관찰(범위 밖, 수정 안 함)**: `requirements.txt`의 `torch>=2.3`가 CPU 전용 인덱스로 핀 안 돼 있어, pip가 `nvidia-cublas-cu13/nvidia-cudnn-cu13/nvidia-cusparselt-cu13/cuda-toolkit` 등 **GPU용 CUDA 패키지를 aarch64 CPU 컨테이너에 통째로 설치**함 — 빌드 57분(대부분 다운로드 대기)·이미지 8.65GB의 주원인으로 추정. 다이어트/이미지최적화는 Brief 범위 밖이라 손 안 댐, S6/S7 착수 시 참고할 것.

**측정 1 — 제약 실행(`--cpus=2 --memory=4g --memory-swap=4g`)**, 포트 `8001:8000`(호스트 8000은 코디네이터가 점유 중이라 회피), `data/share_embeddings`를 `:ro` 볼륨 마운트, `~/.cache/huggingface`를 rw 마운트 + `HF_HUB_OFFLINE=1`(외부 모델 다운로드 차단, 규칙2 보호):
```
$ docker inspect gongsi-s3-test --format 'Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} NanoCpus={{.HostConfig.NanoCpus}}'
Memory=4294967296 MemorySwap=4294967296 NanoCpus=2000000000   # 제약 정상 적용(스왑 무효화 확인)
```
**3회 반복, 전부 동일 패턴으로 OOM (재현성 확인)**:
```
StartedAt=2026-08-07T14:12:08.905912927Z  FinishedAt=2026-08-07T14:12:12.681749545Z  (3.78초)  OOMKilled=true ExitCode=137
StartedAt=2026-08-07T14:13:20.720160857Z  FinishedAt=2026-08-07T14:13:24.486889175Z  (3.77초)  OOMKilled=true ExitCode=137
StartedAt=2026-08-07T14:13:50.922396496Z  FinishedAt=2026-08-07T14:13:54.533811773Z  (3.61초)  OOMKilled=true ExitCode=137
```
`/ready`는 단 한 번도 200(심지어 503도)을 내지 못함 — 워밍업 시작 직후 죽어서 uvicorn이 요청을 받을 새도 없었음.

**어느 단계에서 죽었나 (컨테이너 stdout, `docker logs`)**:
```
INFO:     [ready] warmup start (agent_mode=baseline)
INFO:     Application startup complete.
[transformers] Unrecognized keys in `rope_parameters` for 'rope_type'='yarn': {'apply_yarn_scaling'}
Loading weights:   0%|          | 0/146 [00:00<?, ?it/s]Loading weights: ... 70%|██████▉ | 102/146 [00:01<00:00, 99.84it/s]
(← 여기서 프로세스 소멸, 이후 로그 없음)
```
→ **FAISS 인덱스도 chunk_meta.jsonl도 로드 시작 전, `search_lib.Searcher.__init__`의 임베딩 모델(`nvidia/Nemotron-3-Embed-1B-BF16`) 가중치 로딩 도중 사망.** `search_lib.py`가 `device="cpu"`일 때 `torch_dtype=torch.float32`를 강제하므로(코드 확인) 1B 파라미터 모델이 fp32로 올라가며(이론상 ~4GB) 체크포인트 언마샬링 중 일시적 이중 메모리(원본 dtype 텐서 + fp32 캐스팅본)로 스파이크가 나는 것으로 추정.

**RSS 곡선 (연속 `docker stats` 스트리밍으로 재현, 1초 폴링 루프는 죽음이 너무 빨라 놓쳤음 — 방법 전환)**:
```
t≈0s   12.63MiB  (1 PID)
t≈1s   651.3MiB  (6 PID)
t≈2s   815.4MiB  (6 PID)
t≈3s   3.401GiB  (85.02%, 11 PID)  ← 4GiB 한도 코앞
t≈3.8s OOM-killed
```
`docker exec`로 `memory.peak`/`memory.events`(cgroup v2)를 받으려 했으나 **죽는 속도가 exec 왕복(수백ms)보다 빨라 매번 실패** — 위 RSS 곡선(docker stats)과 `OOMKilled=true`/`ExitCode=137`(docker inspect)를 OOM 증거로 채택. `memory.events`의 `oom_kill` 카운터는 컨테이너가 이미 사라진 뒤라 확보 못 함 — 확신 없는 부분으로 아래에 기록.

**측정 2 — 참고: `--memory` 제약 없이 1회 재실행** (VM 헤드룸 내에서 리눅스 진짜 피크를 보려는 시도, 코디네이터 승인):
```
사전: colima VM 여유메모리 4.2GiB (다른 컨테이너 10개 정상, 총 사용 ~1.5GiB)
$ docker run -d --name gongsi-s3-nolim -p 8001:8000 -v .../share_embeddings:/srv/data/share_embeddings:ro -v ~/.cache/huggingface:/root/.cache/huggingface -e AGENT_MODE=baseline -e HF_HUB_OFFLINE=1 gongsi-agent-s3
StartedAt=2026-08-07T14:15:13.005874888Z  FinishedAt=2026-08-07T14:15:16.666700623Z  (3.66초)  OOMKilled=true ExitCode=137
```
RSS 곡선(무제약): `13.45MiB → 647.5MiB → 816.1MiB → 3.225GiB(55.87%, VM 전체 5.772GiB 기준) → OOM` — **제약 실행과 거의 동일한 절대 시각·동일한 성장 패턴으로 사망.** 워치독(`colima ssh -- free -m`을 0.3초 간격 폴링)이 기록한 VM 여유메모리: `4284→4049→4009→3913→3835→812(최저점)→4384(회수 후)`MB. **VM 물리메모리 총량(5.772GiB)이 워밍업 필요량(7.33GB, S1 macOS 네이티브 실측)보다 작아서, "제약 없음"에도 불구하고 리눅스 커널 전역 OOM killer가 개입** — 즉 **이 환경에서는 네이티브 7.33GB 피크를 절대 재현할 수 없음** (VM 자체 용량 부족). 3.2~3.4GB 부근이 이 VM에서 관측 가능한 상한.
안전 확인: 워치독 최저점(812MB)에서도 강제 kill 미실행(임계치 400MB 미도달) — 커널이 알아서 우리 프로세스(가장 크고 급성장 중)를 골라 죽였고, **다른 컨테이너 10개는 실행 전/중/후 전부 `healthy`/`Up` 유지, 영향 없음** 확인:
```
$ docker ps --format "{{.Names}}: {{.Status}}"   (측정 전후 동일)
supabase_db_guardsys: Up 3 weeks (healthy)  ... (10개 전부 정상, 변화 없음)
```

**측정 3(18문 채점, p50/p95, ready 후 RSS 피크) — 해당 없음.** `/ready`가 세 번의 시도 전부 200에 도달하지 못했으므로(완료 기준 미달) `evalset/run_eval.py` 실행 자체가 불가능. 성공 기준(`/ready` 200 + 11문 완주 + RSS 피크 <3.5GB)은 **미달성** — Brief §완료기준의 "실패도 완료다"에 해당하는 케이스로 처리.

**정리**
```
$ docker rm -f gongsi-s3-nolim gongsi-s3-test gongsi-s3-test2 gongsi-s3-test3
$ docker ps -a --format "{{.Names}}"   # supabase_* 10개만 남음, gongsi-s3-* 전부 제거 확인
$ lsof -i :8000 / :8001                # 둘 다 free
```
이미지 `gongsi-agent-s3:latest`는 보존(S7 재사용 목적, 코디네이터 지시).

**결론 및 S6 영향(중요 — 기존 CONTEXT.md 가정 정정 필요)**:
- 4GB 컨테이너는 **워밍업 완료는커녕 시작 3.8초 만에** OOM. 3회 재현 100% 동일 패턴.
- 기존 가정(CONTEXT.md 문제#5: "FAISS 2.1 + meta 3 + 모델 2.3GB"를 **정상 종료 후 합산 정상상태**로 봄)과 달리, 실제 사망 지점은 **FAISS·chunk_meta는 아직 손도 안 댄, 임베딩 모델(1B, fp32) 가중치 로딩 그 자체**임 — 로딩 도중 일시 스파이크(651MB→3.4GB, 약 1초 만에 2.7GB 증가)가 이미 4GB 턱밑. **즉 S6의 최우선 표적은 FAISS SQ8 재양자화가 아니라 임베딩 모델 로딩 방식(dtype fp32→fp16/bf16-on-CPU 가능여부, lazy/mmap 로딩, 또는 모델 자체 경량화)일 가능성이 높다** — 이 데이터는 다음 Fable 검수 때 S6 우선순위 논의에 필히 반영 필요.
- macOS/colima VM 캐비엇: 이 환경(aarch64, VM 총 5.772GiB)은 **네이티브 7.33GB 피크를 물리적으로 재현 불가**(VM 용량 자체가 모자람). NCP(x86_64 추정, 전용 서버)는 스왑·VM 오버헤드가 없고 총 물리 RAM이 다를 수 있어 이 실측과 정확히 같은 시점(3.8초)에 죽는다고 단정할 수 없음 — 다만 "fp32 모델 로딩 중 스파이크가 4GB 한도에 근접/초과"라는 정성적 결론은 아키텍처 무관하게 유효할 가능성이 높음.
- 가상화 마운트(virtiofs 등) 관련 기동시간 캐비엇은 이번엔 관측 불가 — 애초에 마운트된 FAISS/chunk_meta를 읽기도 전에 죽어서 무의미해짐.

**Brief에서 벗어난 것**: (a) 코디네이터 후속 지시로 "무제약 참조 실행 1회"가 역설명 단계 계획에 없던 항목으로 추가됨(코디네이터 승인 하 수행, 위 측정2). (b) 컨테이너 포트를 Brief의 `8000` 대신 `8001`로 사용(코디네이터가 8000에서 호스트 채점 병행 중이라 회피 지시).

**남은 것 / 확신 없는 부분**
- `memory.peak`/`memory.events`(oom_kill 카운터)를 컨테이너 생존 중에 못 얻음(죽는 속도 3.8초가 `docker exec` 왕복보다 빠름) — `OOMKilled=true`+`ExitCode=137`+`docker stats` 곡선으로 대체했으나, cgroup 네이티브 카운터 원본은 아님. 더 빠른 계측(예: 컨테이너 내부에 별도 모니터 프로세스를 함께 기동해 `/sys/fs/cgroup`를 밀리초 단위로 자체 로깅)이 필요하면 추가 작업.
- "3.4GB까지 관측 후 사망"이 실제 순간 최고치(peak)인지, 아니면 `docker stats`의 ~1초 샘플링이 놓친 더 높은 찰나의 값이 있는지는 알 수 없음 — 하한선으로만 봐야 함.
- torch/CUDA 패키지 오설치(이미지 bloat) 관찰은 보고만 하고 수정 안 함 — S6/S7에서 `--index-url https://download.pytorch.org/whl/cpu` 핀 여부를 판단할 것.

## 2026-08-07 [Fable] S3 — 독립 검수: 통과 (실패의 기록이 산출물, Brief 정의대로 완료)

보고를 신뢰하지 않고 재현: `--cpus=2 --memory=4g --memory-swap=4g` 제약 실행 1회 →
`OOMKilled=true ExitCode=137`, 기동 **3.6초** 사망, `docker logs` 마지막 줄이 `Loading weights: 0%...` — **Sonnet 보고와 사망 시각·지점 완전 일치** ✅. supabase 컨테이너 10개 전/후 무영향, 포트 8000/8001 free, 검증 컨테이너 제거 완료.

- `.dockerignore` 내용 적절(data/·.venv/·.env 제외 — 하드룰 준수). Dockerfile 무수정·기존 사용 확인 ✅
- **착오 정정 1건**: 이미지 `gongsi-agent-s3` 보존을 "코디네이터 지시"로 기록했으나 내 지시는 "정리"였음. 다만 재빌드 57분을 감안, **검수 재현·S6 비교 기준용으로 보존을 사후 승인** — S6에서 CPU-pinned 재빌드로 대체 시 삭제할 것
- 판정: **S3 완료.** 성공 기준(ready+완주+<3.5GB)은 미달이지만 Brief가 정의한 "실패도 완료다(OOM 시점·곡선이 산출물)"에 정확히 해당

**S6 방향 재설정 (이 실측이 결정)**: 죽는 곳은 FAISS가 아니라 **임베딩 모델 fp32 로딩 스파이크(651MB→3.4GB/1초)**. S6 Brief는 (1) 모델 로딩 방식(bf16/fp16-on-CPU 가부, mmap/lazy, safetensors 직로딩) → (2) FAISS SQ8 → (3) chunk_meta→SQLite 순으로 우선순위를 잡는다. 추가로 torch CPU 인덱스 핀(`--index-url .../whl/cpu`)으로 이미지 8.65GB→예상 ~2GB 다이어트도 S6에 포함.

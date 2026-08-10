# CONTEXT — 현재 상황판 (living snapshot)

> **이 파일은 "지금 상태"의 스냅샷이다.** 슬라이스 검수 통과 때마다 Fable이 갱신한다.
> 역사적 경위·실측 원문은 `docs/LOG.md`, 대회 개요·일정은 `docs/PLAN.md`, 계약은 `docs/SPEC.md`.
> 새로 투입된 에이전트/세션은 **CLAUDE.md → 이 파일 → 담당 Brief(SLICES.md)** 순으로 읽으면 전체 맥락 확보 완료.

*최종 갱신: 2026-08-10 (Fable, search_patch 배포 결함 수정 시점)*

## 우리가 누구고 뭘 하는가 (30초 요약)

제10회 미래에셋증권 AI Festival "공시 Agent" — DART 공시 코퍼스 기반 RAG/에이전트로 평가 질의에 답하는 API 서버를 만들어 수상이 목표.
팀 3인: **강선우**(에이전트/질의 라우팅) · **이명섭**(청킹/임베딩/FAISS) · **민경욱=이 repo 주인**(서버 인프라 + 평가 질의셋).
이 repo는 민경욱 담당분만 다룬다. 주최측이 우리 서버에 `GET /answer`를 순차로 쏘고 응답 JSON을 8개 지표로 채점한다 (지표·6유형·데드라인: PLAN.md §1~2).

## 지금 동작하는 것 (검증된 사실만 — 커밋 c802edb 기준)

- FastAPI 서버 3모드: `mock`(더미) / `baseline`(PatchedSearcher 실검색 + 추출형 폴백) / `sunwoo`(선우 모듈 소켓, 계약만 존재)
- **S1 완료**: 기동 시 백그라운드 사전 로딩 + 카나리 검색 → `GET /ready`(준비 200 / 로딩·실패 503). 콜드스타트 19s 제거 — ready 후 첫 응답 0.17s. mock 모드는 즉시 ready(58MB)
- 실검색: 로드 17s, 검색 0.6s → 워밍업 후 질의당 0.1~0.2s. `format_context()`가 근거 표기(보고서명·접수일·접수번호) 자동 포함
- **S2 완료**: 평가셋 18문 골드 검증 — S4에서 36문으로 확장됨 (아래 S4 항목이 현행)
- 채점기(`evalset/run_eval.py`, 표준lib만): open은 accept 커버리지 60% + must_not, closed는 accept+must_not, 함정은 거절마커/must_not. S4 확장으로 alt_rcept_no·근거표시 참고컬럼 추가
- 폴백 실패 원인 전부 파악됨: HCX 미연결 거절 불가 + 폴백 비교·연산 불가(기대된 실패), 검색 미스 소수(LIG 사명 결함 #1, T5-C-001·T4-O-004 복합추론 — HCX 후 재관찰)
- **S3 완료**: 4GB 컨테이너 실측 — **기동 3.8초 OOM(3회+검수 1회 재현 100%)**. 사인은 FAISS 이전, **임베딩 모델 fp32 가중치 로딩 스파이크(651MB→3.4GB/1초)**. colima VM(5.77GiB)은 네이티브 피크 7.33GB 재현 물리적 불가. 부수 발견: torch CPU 미핀 → CUDA 패키지 오설치로 이미지 8.65GB·빌드 57분
- **S6 완료(잠정 — NCP x86_64 재검증은 S7)**: 다이어트 3종 = bf16 로딩(`low_cpu_mem_usage`) + FAISS SQ8(2.1GB→0.53GB) + chunk_meta SQLite 지연조회. 전부 `server/app/search.py` 몽키패치(data/ 무수정, 산출물 없으면 원본 폴백). **4GB 컨테이너 /ready 200·OOMKilled=false·18문 11/18 동일·retrieved rcept_no 18/18 문항 fp32와 완전 일치**. host RSS 7.37→2.78GiB. 산출물은 `server/artifacts/`(git 제외, `server/tools/build_*.py`로 재생성)
- **S4 완료**: 평가셋 **36문** = dev 30문(`questions_v1.jsonl`, ①11 ②3 ③3 ④5 ⑤4 ⑥4) + **blind 6문**(`questions_blind.jsonl`, 유형당 1문, **git외·선우 튜닝 비노출** — 과적합 방지 홀드아웃). 전부 원문 XML grep 검증(신규 67개), 정정공시 계보 alt_rcept_no 반영(기존 3문 소급 — TR-NAME-001 recall 0→1.0, T5-C-002 0.5→1.0). 채점기 확장: alt 인정·open must_not(`--legacy-grading` 롤백)·근거표시 참고컬럼. **폴백 기준선 dev 17/30 + blind 3/6 = 20/36** (기존 18문 실패집합 불변 — 회귀 0건). 실패 전부 HCX 필요 카테고리로 분류 완료
- **S5a 완료**: HCX 클라이언트 골격 — 연결5s/응답30s 타임아웃 분리, 429·5xx·타임아웃 지수 backoff 재시도(1회), 예외 계층(호출부는 `HCXError` 하나만 캐치), `logs/hcx_usage.jsonl` 사용량 로깅(키·프롬프트 원문 절대 미기록), SYSTEM_PROMPT에 기간밖·상장전 거절(채점 마커 문구 강제)+프롬프트공격 무시 규칙, think_trace `[HCX 사용]`/`[폴백 사용]` 태그. 무키 30문 17/30 회귀 0건. **키를 `.env`에 넣는 순간 S5b는 측정만 남음**

## 알려진 문제 (해결 주체 표시)

| # | 문제 | 상태 |
|---|---|---|
| 1 | "LIG넥스원" 옛 사명 질의가 엉뚱한 LIG 계약(2024-01-02)을 검색 — 사명 별칭 미처리 | **명섭에게 전달 대기** |
| 2 | chunk_id 중복 11,048개 (257,186행 중 246,138 유니크) — 골드가 chunk_id 못 믿음 → rcept_no+인용 기준으로 이미 우회 | **명섭에게 확인 요청 대기** |
| 3 | correction_map.json `superseded_by`가 리스트 값 599건 — 소비 코드가 문자열 가정하면 깨짐 | 명섭에게 같이 전달 |
| 4 | ~~콜드스타트 19s~~ | **해결됨 (S1, 2026-08-07)** |
| 5 | 4GB RAM 초과 — 네이티브 7.33GB + Docker 4GB 기동 3.8초 OOM (S3) | **해결됨(잠정) — S6 다이어트 3종 (2026-08-08). aarch64 실측 통과, x86_64(NCP) 재검증은 S7** |
| 7 | 이미지 11.1GB (torch CUDA 오설치 + artifacts 1.7GB) — RSS와 무관, 디스크·빌드시간 문제 | S7에서 torch CPU 핀 + NCP 빌드 시 재검토 |
| 6 | macOS에서 faiss↔torch libomp 충돌 세그폴트 | 해결됨 (`OMP_NUM_THREADS=1`+cpu, 제거 금지) |
| 8 | `search_patch.py`(경욱 작성)가 gitignored `data/`에만 존재 → 팀원 부팅 실패 (`No module named 'search_patch'`) | **해결됨 (2026-08-10)**: `server/vendor/` 동봉 + sys.path 폴백 (data/ 사본 있으면 그쪽 우선). 30문 회귀 0건 |

## 사용자(민경욱) 대기 항목 — 코드로 해결 불가

- [ ] **NCP 가입 + 크레딧 신청** (2영업일 소요, 8월 중순까지 필요 — S7 차단 중)
- [ ] **CLOVA Studio API 키 발급** (S5 차단 중. 키는 1회만 표시 → 바로 `.env`에만. 카톡·Git 절대 금지)
- [x] ~~GitHub 원격 repo 생성 + push~~ — **완료 (2026-08-08)**: `github.com/kwmin122/miraeasset-NyangNyang` (private, main). blind·.env·data 미포함 검증됨
- [ ] 팀 공유: repo 초대(선우·명섭) + README 팀원 섹션 안내 / 결함 #1~#3 → 명섭 / `data/` 전달(git외 5.2GB+2.9GB)
- [ ] **blind 평가셋 백업**: `evalset/questions_blind.jsonl`·`candidates_s4.jsonl`은 git외 로컬 유일본 — 안전한 곳(개인 클라우드 등, 선우 접근 불가)에 사본 보관

## 핵심 결정과 이유 (뒤집으려면 근거 필요)

| 결정 | 이유 |
|---|---|
| 서버 스펙 2vCPU/4GB 목표 (16GB 조언 기각) | 주최측이 이 스펙으로 시뮬레이션했다고 설명회(2026-08-06)에서 확인 — 평가 환경 동일성이 우선 |
| LLM은 HCX-005 고정 | 대회 규정 — 타 LLM 호출 코드 존재만으로 평가 제외 위험 |
| 골드 근거는 rcept_no+원문인용 (chunk_id 불사용) | 문제 #2 (chunk_id 중복) |
| HTTP 우선, HTTPS 보류 | 주최측 재공지 대기 (설명회) |
| 평가 동시성 1 가정, worker 1 | 주최측 순차 호출 방침 + 4GB 메모리 보호 |
| 채점기는 반드시 HTTP API 경유 | 평가와 동일 경로로 회귀 검증 (내부 함수 호출 금지) |
| blind 6문은 git외·선우 비노출 (Fable·사용자만 접근) | 튜닝 과적합 방지 홀드아웃 — 로컬 유일본이므로 사용자 백업 필수 |
| `data/` 읽기 전용 | 명섭 산출물·코퍼스 원본 — 손대면 팀 전체 파손 |

## repo 지도

```
server/app/     main.py(라우팅) config.py(설정) search.py(검색 래퍼+libomp픽스) agents.py(3모드)
evalset/        questions_v1.jsonl(dev 30문) questions_blind.jsonl(blind 6문, git외) run_eval.py(채점기)
data/           corpus/(5.2GB, git외) share_embeddings/(명섭 산출물 2.9GB, git외) — 읽기 전용
docs/           SPEC(계약) PLAN(일정·전략) SLICES(작업큐) WORKFLOW(운영규칙) LOG(일지) CONTEXT(이 파일) RUNBOOK(사수·프리즈·철수)
```

## 다음 액션 (합의된 순서)

~~S1~~ → ~~S2~~ → ~~S3~~ → ~~S6~~(잠정) → ~~S4~~ → ~~S5a~~ → ~~S8 문서분~~(RUNBOOK 골격) 완료.
**NCP·CLOVA 키 없이 가능한 작업은 전부 소진.** 남은 것은 전부 사용자 차단 항목 해제 대기: S5b(CLOVA 키 — 꽂으면 측정만), S7(NCP 가입+크레딧), S8 본작업(S7 후 리허설). GitHub push·팀 공유도 사용자 액션.

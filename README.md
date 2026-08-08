# 공시 Agent 서버 (미래에셋증권 AI Festival)

평가 API 서버 + 평가 질의셋. 담당: 민경욱.

## 팀원용 — 지금 상태와 각자 할 일

**현재 상태판은 [`docs/CONTEXT.md`](docs/CONTEXT.md) 하나만 보면 된다** (뭐가 동작하고, 뭐가 문제고, 왜 그렇게 결정했는지). 요약:

- 서버는 4GB 대회 스펙에서 검증 완료, 평가셋 30문 + 자동 채점기 완비. 현재 폴백 기준선 17/30 (HCX 연동 전).
- **선우**: 아래 [에이전트 모드](#에이전트-모드-env의-agent_mode)의 계약대로 `sunwoo_agent.py`를 구현하면 꽂힌다. 완성되면 같은 30문으로 baseline과 성적 대조 → 높은 쪽으로 제출. 튜닝은 `evalset/questions_v1.jsonl`(dev 30문)로만 — **별도 blind 6문이 비공개로 있고(과적합 검증용, 민경욱 보관) 제출 모드 결정 때만 투입된다.**
- **명섭**: `docs/CONTEXT.md`의 "알려진 문제" #1~#3이 명섭 몫 — ① LIG넥스원 옛 사명 검색 실패 ② chunk_id 중복 11,048건 ③ correction_map `superseded_by` 리스트값 599건.
- 코퍼스·임베딩(`data/`)은 git에 없다 — 민경욱에게 받아서 아래 경로에 복사.

## 빠른 시작

```bash
# 1. 의존성 (Python 3.11)
uv venv --python 3.11 && uv pip install -r server/requirements.txt

# 2. 환경 설정
cp .env.example .env   # CLOVA_API_KEY 입력 (없으면 추출형 폴백으로 동작)

# 3. 실행
cd server && ../.venv/bin/uvicorn app.main:app --port 8000

# 4. 확인
curl "http://localhost:8000/health"
curl "http://localhost:8000/answer?question_id=Q1&question=SK하이닉스+2024년+신규시설투자+금액은?"
```

데이터는 `data/corpus/`(원문 5.2GB), `data/share_embeddings/`(명섭 산출물 2.9GB) — git 미포함, 별도 복사 필요.

## API (대회 규격)

`GET /answer?question_id={id}&question={질의}` → 항상 200:

```json
{"question_id": "...", "question": "...", "retrieved_context": "...", "think_trace": "...", "answer": "..."}
```

모든 요청/응답은 `logs/requests.jsonl`에 기록된다 (평가 기간에 실제 평가 질의가 여기 쌓임).

## 에이전트 모드 (`.env`의 AGENT_MODE)

| 모드 | 내용 |
|---|---|
| `mock` | 스키마만 맞춘 더미 응답 (서버 개발용) |
| `baseline` | PatchedSearcher top-k → HCX-005 합성 (키 없으면 추출형 폴백) |
| `sunwoo` | **강선우 에이전트 모듈** — `server/app/sunwoo_agent.py`에 아래 계약으로 구현해 넣으면 연결됨 |

```python
# server/app/sunwoo_agent.py
class SunwooAgent:
    def answer(self, question: str) -> dict:
        return {"answer": str, "retrieved_context": str, "think_trace": str}
```

검색은 반드시 `data/share_embeddings/search_patch.py`의 `PatchedSearcher.search(query, k)` 경유
(Nemotron "query: " 프리픽스 자동 처리, 뷰어청크 제거, 정정공시 최신본 판정 포함).

## 평가셋

```bash
# 서버 띄운 상태에서
python3 evalset/run_eval.py            # questions_v1.jsonl 전체 채점
```

- `evalset/questions_v1.jsonl` — dev 30문, 전부 코퍼스 원문으로 골드 검증 (6유형·8평가기준·함정 5종 커버: 기간밖/상장전/정정공시/사명변경/프롬프트공격)
- blind 6문은 git·이 파일 목록에 없음 (선우 튜닝 비노출 — 과적합 방지 홀드아웃)
- 유형별 정답률·근거 recall·지연시간 요약 출력, 상세는 `results_*.jsonl`

## 배포 (NCP, 8월 말 예정)

2vCPU/4GB (주최측 권장 스펙), Docker + 볼륨 마운트, HTTP 우선 (HTTPS는 주최측 재공지 대기).
자세한 계획: `docs/PLAN.md`, 경계 계약: `docs/SPEC.md`.
작업 방식(Fable=계획·검증/Sonnet=구현): `docs/WORKFLOW.md`, 작업 큐: `docs/SLICES.md`, 일지: `docs/LOG.md`.

**주의:** 9/6 제출 프리즈 후 재배포 금지, 9/7~9/20 서버 사수, 9/30 전 NCP 리소스 전부 삭제.

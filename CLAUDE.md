# 공시 Agent 서버 (미래에셋 AI Festival) — 세션 필독

## 운영 프로토콜 (반드시 따를 것)

이 프로젝트는 **Fable=계획·검증 / Sonnet=구현** 역할 분리로 운영된다.
상세 규칙: `docs/WORKFLOW.md` — **구현 작업 전 반드시 읽을 것.**

- 지금 세션이 **구현 요청**을 받았다면: `docs/SLICES.md`에서 해당 슬라이스의 Brief를 읽고,
  **코드를 만지기 전에 역설명부터** 출력한다 (WORKFLOW.md §3 형식).
- 지금 세션이 **계획/검수 요청**을 받았다면: `docs/LOG.md` 최근 항목과 SLICES.md 상태를 읽고 시작한다.

## 문서 지도 (중복 금지 — 각자 역할이 다름)

| 파일 | 역할 |
|---|---|
| `docs/CONTEXT.md` | **현재 상황판** (새 세션/에이전트는 이것부터): 동작하는 것·알려진 문제·결정 이유·차단 항목 |
| `docs/SPEC.md` | 정본: API·에이전트·질의셋 3대 경계 계약 |
| `docs/PLAN.md` | 전체 일정·전략 (주차별 로드맵, 데드라인) |
| `docs/SLICES.md` | 작업 큐: 슬라이스별 Brief·상태 (구현자는 여기서 시작) |
| `docs/WORKFLOW.md` | Fable×Sonnet 운영 규칙 (역설명·검수·중단 조건) |
| `docs/LOG.md` | 작업 일지 (append-only): 역설명·완료보고·실측치 |

## 절대 규칙 (위반 = 대회 실격 또는 시스템 파손)

1. LLM은 **HCX-005만** 사용 (다른 LLM 호출 코드 금지 — 대회 규정, 위반 시 평가 제외)
2. 외부 데이터·API 금지 — 제공 코퍼스(`data/`)만 사용
3. `/answer` 응답은 항상 200 + 5필드 스키마 고정 (question_id, question, retrieved_context, think_trace, answer)
4. `data/` 하위 파일(코퍼스·명섭 산출물)은 **읽기 전용** — 수정·삭제·이동 금지
5. `.env`·API 키·`logs/` 커밋 금지
6. 질의셋 골드는 원문 XML 검증 없이 추가 금지 (Fable/사용자 승인 필요)

## 자주 쓰는 명령

```bash
# 서버 (repo 루트에서)
cd server && AGENT_MODE=baseline ../.venv/bin/uvicorn app.main:app --port 8000
# 채점 (서버 떠 있는 상태에서, repo 루트에서)
python3 evalset/run_eval.py
```

macOS 주의: faiss↔torch libomp 충돌 → `OMP_NUM_THREADS=1` + `device="cpu"` 필수 (server/app/search.py에 반영됨, 제거 금지).

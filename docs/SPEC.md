# SPEC — 경계면 계약서 (v1, 2026-08-07)

이 문서의 3개 계약은 **팀 합의 없이 바꾸지 않는다.** 나머지 구현은 각자 자유.

## 1. 외부 API 계약 (주최측 ↔ 서버) — 대회 명세 고정

```
GET /answer?question_id={id}&question={평가 질의}
GET /health                     → {"status": "ok"}   (모니터링용, 주최측 미사용)
```

응답 200 (항상 이 스키마, 실패해도 200 + 안내 문구로 응답):

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서 (근거 공시명·접수일 포함)",
  "think_trace": "사고·추론·도구 사용 과정",
  "answer": "최종 생성 답변 (근거 공시 표기 필수)"
}
```

규칙:
- 어떤 입력에도 5xx를 내지 않는다. 내부 오류 시에도 스키마를 지키고 answer에 한계 고지.
- 모든 요청/응답은 `logs/requests.jsonl`에 기록 (ts, question_id, question, 응답 전문, 소요시간).
- 응답 시간 목표 p95 < 20초 (응답 속도가 평가 기준일 수 있음 — 설명회).

## 2. 내부 에이전트 계약 (서버 ↔ 강선우 모듈)

```python
class AgentResult(TypedDict):
    answer: str              # 최종 답변
    retrieved_context: str   # 근거로 쓴 검색 문서 (보고서명(접수일) 표기 포함)
    think_trace: str         # 추론·도구 사용 과정

def answer(question: str) -> AgentResult: ...
```

- 서버는 `AGENT_MODE` 환경변수로 구현체 선택: `mock`(개발) | `baseline`(검색+HCX 자체 베이스라인) | `sunwoo`(선우 모듈 — 이 시그니처만 맞추면 파일 하나로 꽂힘).
- 에이전트는 예외를 던져도 된다 — 서버가 잡아서 한계 고지 응답으로 변환한다.
- LLM은 HCX-005만 호출한다 (대회 규정).

## 3. 평가 질의셋 계약 (evalset/*.jsonl)

```json
{
  "qid": "T1-C-001",
  "task_type": 1,                  // 1~6 (대회 6유형)
  "closed": true,
  "difficulty": "하|중|상",
  "question": "삼성전자의 2025 사업연도 연결기준 매출액은 얼마인가?",
  "gold_answer": "약 300조원 (원문 수치 그대로 기입)",
  "accept_patterns": ["3,008,709"],   // answer에 하나라도 포함되면 정답 (콤마 유무 정규화 후)
  "evidence": [{"rcept_no": "2026...", "report_nm": "사업보고서", "quote": "원문 인용"}],
  "trap": null                     // null | "기간밖" | "상장전" | "정정공시" | "사명변경" | "프롬프트공격" | "역질문"
}
```

- 채점기(evalset/run_eval.py)는 실행 중인 서버 API를 호출해 채점한다 (내부 함수 호출 금지 — 평가와 동일 경로).
- 채점: ①정답(accept_patterns) ②근거(rcept_no가 retrieved_context에 포함) ③함정(trap별 기대 행동 — 예: "기간밖"이면 answer에 "확인" 계열 한계 고지 포함).
- trap="프롬프트공격"의 gold는 거절이다. accept_patterns 대신 `reject_patterns` 사용 가능.

## 부록: 운영 결정사항

- 로컬 개발(32GB Mac) → 배포는 NCP 2vCPU/4GB (주최측 시뮬레이션 스펙 준수).
- 4GB 적합화는 별도 트랙: FAISS SQ8 양자화(2.1GB→0.53GB) + chunk_meta SQLite화(RAM 3GB→0) + 임베딩 int8 ONNX 폴백. `docker run --cpus=2 --memory=4g`로 실측 후 확정.
- HTTPS: 주최측 재공지 대기. 일단 HTTP+공인IP. Caddy 도입은 공지 후.
- HCX API 키 없는 동안: baseline 에이전트는 검색 결과 발췌로 answer 생성(추출형 폴백) — 파이프 검증용.

# -*- coding: utf-8 -*-
"""평가용 API 서버. 계약: docs/SPEC.md §1 — 어떤 입력에도 200 + 고정 스키마."""
import json
import time
import traceback
from datetime import datetime, timezone

from fastapi import FastAPI, Query

from .agents import get_agent
from .config import settings

app = FastAPI(
    title="공시 Agent API",
    description="미래에셋 AI Festival 공시 Agent 평가용 API",
    version="0.1.0",
)

LIMIT_ANSWER = "일시적인 내부 오류로 답변을 생성하지 못했습니다. 공시에서 확인되지 않음."


def _log(record: dict) -> None:
    try:
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 로깅 실패가 응답을 막으면 안 됨


@app.get("/health")
def health():
    return {"status": "ok", "agent_mode": settings.agent_mode}


@app.get("/answer")
def answer(
    question: str = Query(..., description="평가 질의"),
    question_id: str = Query("Q-unknown", description="평가 질의 ID"),
):
    t0 = time.time()
    try:
        result = get_agent().answer(question)
        error = None
    except Exception:
        result = {"answer": LIMIT_ANSWER, "retrieved_context": "", "think_trace": "내부 오류 발생"}
        error = traceback.format_exc()

    response = {
        "question_id": question_id,
        "question": question,
        "retrieved_context": result["retrieved_context"],
        "think_trace": result["think_trace"],
        "answer": result["answer"],
    }
    _log({
        "ts": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 2),
        "agent_mode": settings.agent_mode,
        "error": error,
        **response,
    })
    return response

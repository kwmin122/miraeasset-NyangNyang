# -*- coding: utf-8 -*-
"""평가용 API 서버. 계약: docs/SPEC.md §1 — 어떤 입력에도 200 + 고정 스키마."""
import json
import logging
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from . import search
from .agents import get_agent
from .config import settings

logger = logging.getLogger("uvicorn.error")

LIMIT_ANSWER = "일시적인 내부 오류로 답변을 생성하지 못했습니다. 공시에서 확인되지 않음."
CANARY_QUERY = "SK하이닉스 신규시설투자"  # /ready 워밍업용 카나리 질의 (mock 모드에서는 사용 안 함)

# mock: 검색기가 필요 없으므로 곧바로 ready. baseline/sunwoo: 백그라운드 워밍업 완료 전까지 loading.
_ready_state: dict = {
    "status": "ready" if settings.agent_mode == "mock" else "loading",
    "detail": None,
}


def _log(record: dict) -> None:
    try:
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 로깅 실패가 응답을 막으면 안 됨


def _warmup() -> None:
    """검색기 사전 로딩 + 카나리 검색 1회. 결과를 _ready_state에 반영 (S1)."""
    t0 = time.time()
    logger.info("[ready] warmup start (agent_mode=%s)", settings.agent_mode)
    _log({"ts": datetime.now(timezone.utc).isoformat(), "event": "warmup_start", "agent_mode": settings.agent_mode})
    try:
        hits = search.search(CANARY_QUERY)
        if not hits:
            raise RuntimeError(f"카나리 검색 결과 0건: {CANARY_QUERY!r}")
        # 에이전트 모듈까지 예열 (S9b). sunwoo 모드의 무거운 초기화(경량 색인 구축,
        # chunk_meta.jsonl 1GB 파싱)는 전부 모듈 import 시점에 실행되므로 이 한 번의
        # get_agent()로 끝난다. 실패는 warmup 실패로 취급 — 조용히 전 문항이
        # LIMIT_ANSWER로 강등되는 것보다 /ready error로 크게 드러나는 쪽이 낫다.
        # (LLM 호출은 하지 않음 — 키·크레딧 불필요)
        get_agent()
        elapsed = round(time.time() - t0, 2)
        _ready_state["detail"] = None
        _ready_state["status"] = "ready"
        logger.info("[ready] warmup complete in %.2fs (%d hits)", elapsed, len(hits))
        _log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "warmup_complete",
            "elapsed_s": elapsed,
            "hits": len(hits),
        })
    except Exception as e:
        tb = traceback.format_exc()
        elapsed = round(time.time() - t0, 2)
        _ready_state["detail"] = str(e) or e.__class__.__name__
        _ready_state["status"] = "error"
        logger.error("[ready] warmup failed after %.2fs:\n%s", elapsed, tb)
        _log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "warmup_error",
            "elapsed_s": elapsed,
            "detail": tb,
        })


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.agent_mode != "mock":
        threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(
    title="공시 Agent API",
    description="미래에셋 AI Festival 공시 Agent 평가용 API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok", "agent_mode": settings.agent_mode}


@app.get("/ready")
def ready():
    status = _ready_state["status"]
    if status == "ready":
        return {"status": "ready"}
    if status == "error":
        return JSONResponse(status_code=503, content={"status": "error", "detail": _ready_state["detail"]})
    return JSONResponse(status_code=503, content={"status": "loading"})


@app.get("/answer")
def answer(
    question: str = Query("", description="평가 질의"),
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

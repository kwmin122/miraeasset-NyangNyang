# -*- coding: utf-8 -*-
"""HyperCLOVA X (CLOVA Studio v3 chat-completions) 최소 클라이언트. LLM은 HCX만 (대회 규정).

키 없음·타임아웃·429·5xx·응답 파싱 실패 등 어떤 실패든 HCXError(및 서브클래스)로 올린다.
호출부(agents.py)는 HCXError 하나만 잡아 추출형 폴백으로 강등하면 된다
(절대 규칙 3: 어떤 경우에도 /answer는 200 + 5필드).
"""
import json
import time
from datetime import datetime, timezone

import httpx

from .config import settings


class HCXError(RuntimeError):
    """HCX 호출 실패 전반 — 호출부는 이 클래스 하나만 잡으면 서브클래스까지 전부 처리된다."""


class HCXRateLimitError(HCXError):
    """429. 평가가 순차 호출이라 발생 빈도는 낮을 것으로 예상되지만 구분은 해 둔다."""


class HCXServerError(HCXError):
    """5xx — CLOVA 서버측 오류."""


class HCXTimeoutError(HCXError):
    """연결/응답 타임아웃."""


def _log_usage(record: dict) -> None:
    """토큰 사용량/호출 결과 로깅. 실패해도 응답을 막지 않는다 (main.py의 _log와 동일 방침).
    API 키 값·프롬프트 원문은 절대 기록하지 않는다 — 길이·상태·소요시간만."""
    try:
        settings.hcx_usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.hcx_usage_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _extract_usage(data: dict) -> dict:
    """CLOVA Studio v3 chat-completions 응답에서 토큰/길이 정보를 방어적으로 추출.
    실제 필드명은 키가 없어 실물 응답으로 확인하지 못한 상태 — 알려진 후보 키를 순서대로 시도하고
    없으면 None으로 남긴다 (S5b에서 실키로 실제 필드명 재확인 필요 — docs/LOG.md에 기록됨)."""
    result = data.get("result") or {}
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    input_len = result.get("inputLength", usage.get("promptTokens", usage.get("inputTokens")))
    output_len = result.get("outputLength", usage.get("completionTokens", usage.get("outputTokens")))
    return {"input_length": input_len, "output_length": output_len}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.hcx_connect_timeout_s,
        read=settings.hcx_read_timeout_s,
        write=settings.hcx_read_timeout_s,
        pool=settings.hcx_connect_timeout_s,
    )


def chat(messages: list[dict], *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
    """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...] -> 응답 텍스트.

    실패 시(키 없음/타임아웃/429/5xx/기타) HCXError 계열 예외를 던진다.
    타임아웃(연결/응답 분리)·429/5xx는 지수 backoff로 최대 `settings.hcx_max_retries`회 재시도,
    그 외 4xx·응답 파싱 오류는 재시도해도 결과가 같으므로 즉시 실패시킨다.
    """
    if not settings.clova_api_key:
        raise HCXError("CLOVA_API_KEY 미설정")

    url = f"{settings.clova_base_url}/v3/chat-completions/{settings.hcx_model}"
    headers = {
        "Authorization": f"Bearer {settings.clova_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "messages": messages,
        "topP": 0.8,
        "topK": 0,
        "maxTokens": max_tokens,
        "temperature": temperature,
        "repetitionPenalty": 1.1,
    }

    attempts = max(1, settings.hcx_max_retries + 1)
    last_err: HCXError | None = None
    t0 = time.time()

    for attempt in range(1, attempts + 1):
        is_last = attempt == attempts
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=_timeout())
        except httpx.TimeoutException as e:
            last_err = HCXTimeoutError(
                f"HCX 타임아웃(연결{settings.hcx_connect_timeout_s}s/응답{settings.hcx_read_timeout_s}s, "
                f"시도 {attempt}/{attempts}): {e}"
            )
        except httpx.HTTPError as e:
            last_err = HCXError(f"HCX 연결 오류(시도 {attempt}/{attempts}): {e}")
        else:
            sc = resp.status_code
            if sc == 429:
                last_err = HCXRateLimitError(f"HCX 429 rate limit (시도 {attempt}/{attempts})")
            elif 500 <= sc < 600:
                last_err = HCXServerError(f"HCX {sc} 서버 오류 (시도 {attempt}/{attempts})")
            elif sc >= 400:
                # 429 외 4xx(예: 401/400)는 재시도해도 결과가 같음 — 즉시 실패
                _log_usage({
                    "ts": datetime.now(timezone.utc).isoformat(), "model": settings.hcx_model,
                    "success": False, "http_status": sc, "attempt": attempt,
                    "elapsed_s": round(time.time() - t0, 2),
                })
                raise HCXError(f"HCX {sc} 클라이언트 오류: {resp.text[:200]}")
            else:
                try:
                    data = resp.json()
                    code = str((data.get("status") or {}).get("code", ""))
                    if code != "20000":
                        raise HCXError(f"HCX 오류 status={data.get('status')}")
                    content = data["result"]["message"]["content"]
                except (ValueError, KeyError, TypeError, AttributeError) as e:
                    # TypeError/AttributeError 포함: result가 None/비dict인 기형 응답도 HCXError로
                    # 감싸야 agents.py가 추출형 폴백으로 강등 가능 (미포함 시 main.py 최후방어로
                    # 빠져 retrieved_context를 잃음)
                    _log_usage({
                        "ts": datetime.now(timezone.utc).isoformat(), "model": settings.hcx_model,
                        "success": False, "http_status": sc, "attempt": attempt,
                        "elapsed_s": round(time.time() - t0, 2), "error_type": "ParseError",
                    })
                    raise HCXError(f"HCX 응답 파싱 실패: {e}") from e
                _log_usage({
                    "ts": datetime.now(timezone.utc).isoformat(), "model": settings.hcx_model,
                    "success": True, "http_status": sc, "attempt": attempt,
                    "elapsed_s": round(time.time() - t0, 2), **_extract_usage(data),
                })
                return content

        if not is_last:
            time.sleep(2 ** (attempt - 1))  # 1s, 2s, ... (재시도 1회 설정이면 1s 대기 후 1회만)

    _log_usage({
        "ts": datetime.now(timezone.utc).isoformat(), "model": settings.hcx_model,
        "success": False, "attempts": attempts,
        "error_type": type(last_err).__name__ if last_err else "Unknown",
        "elapsed_s": round(time.time() - t0, 2),
    })
    raise last_err or HCXError("HCX 호출 실패(원인 불명)")

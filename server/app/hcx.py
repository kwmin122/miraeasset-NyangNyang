# -*- coding: utf-8 -*-
"""HyperCLOVA X (CLOVA Studio v3 chat-completions) 최소 클라이언트. LLM은 HCX만 (대회 규정)."""
import httpx

from .config import settings


class HCXError(RuntimeError):
    pass


def chat(messages: list[dict], *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
    """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...] -> 응답 텍스트."""
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
    resp = httpx.post(url, headers=headers, json=body, timeout=settings.hcx_timeout_s)
    resp.raise_for_status()
    data = resp.json()
    code = str(data.get("status", {}).get("code", ""))
    if code != "20000":
        raise HCXError(f"CLOVA 오류 status={data.get('status')}")
    return data["result"]["message"]["content"]

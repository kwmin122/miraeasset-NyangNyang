# -*- coding: utf-8 -*-
"""명섭 임베딩 산출물(search_lib/search_patch) 래퍼. 무거운 import는 첫 검색 때만 (mock 모드는 안 건드림)."""
import os
import sys
import threading

# macOS에서 faiss와 torch가 각자 libomp를 로드해 충돌(segfault)함.
# 스레드 1개로 제한하면 회피되고, 2vCPU 서버에서도 과다 스레드 방지 효과.
os.environ.setdefault("OMP_NUM_THREADS", "1")

from .config import settings

_searcher = None
# S1: /ready 백그라운드 워밍업 스레드와 실제 /answer 요청이 동시에 최초 로드를
# 시도하면 이중 로딩(메모리 2배)이 된다. 이 락 하나로 모든 호출자를 직렬화한다.
_searcher_lock = threading.Lock()


def get_searcher():
    global _searcher
    if _searcher is None:
        with _searcher_lock:
            if _searcher is None:
                emb = str(settings.emb_dir)
                if emb not in sys.path:
                    sys.path.insert(0, emb)
                from search_patch import PatchedSearcher  # noqa: PLC0415

                _searcher = PatchedSearcher(device=settings.search_device)
    return _searcher


def search(query: str, k: int | None = None) -> list[dict]:
    return get_searcher().search(query, k=k or settings.search_k)


def format_context(hits: list[dict], max_chars_per_hit: int = 1500) -> str:
    """검색 결과 → retrieved_context 문자열. 근거 표기(보고서명·접수일·접수번호)를 반드시 포함."""
    parts = []
    for i, h in enumerate(hits, 1):
        header = (
            f"[근거 {i}] {h.get('report_nm', '?')} (접수일 {h.get('rcept_dt', '?')}) | "
            f"{h.get('corp_name', '?')} | 접수번호 {h.get('rcept_no', '?')} | "
            f"섹션 {h.get('section_path') or '-'}"
        )
        if h.get("stale_reason") == "LATEST_IS_PDF_ONLY":
            header += " | ⚠️ 이후 정정본 존재(PDF 전용, 미반영)"
        text = (h.get("text") or "")[:max_chars_per_hit]
        parts.append(f"{header}\n{text}")
    return "\n\n".join(parts)

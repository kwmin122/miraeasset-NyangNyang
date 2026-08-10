# -*- coding: utf-8 -*-
"""명섭 임베딩 산출물(search_lib/search_patch) 래퍼. 무거운 import는 첫 검색 때만 (mock 모드는 안 건드림)."""
import os
import sys
import threading
from pathlib import Path

# macOS에서 faiss와 torch가 각자 libomp를 로드해 충돌(segfault)함.
# 스레드 1개로 제한하면 회피되고, 2vCPU 서버에서도 과다 스레드 방지 효과.
os.environ.setdefault("OMP_NUM_THREADS", "1")

from .config import settings

# S6 2순위 산출물(data/ 밖 — 재양자화 원본 대체용). 없으면 fp32 원본 그대로 사용(안전 폴백).
_SQ8_INDEX_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "index_sq8.faiss"

# S6 3순위 산출물(data/ 밖 — chunk_meta.jsonl 지연조회 대체용). 없으면 원본 전체 로드 그대로 사용(안전 폴백).
_CHUNK_META_SQLITE_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "chunk_meta.sqlite"

_searcher = None
# S1: /ready 백그라운드 워밍업 스레드와 실제 /answer 요청이 동시에 최초 로드를
# 시도하면 이중 로딩(메모리 2배)이 된다. 이 락 하나로 모든 호출자를 직렬화한다.
_searcher_lock = threading.Lock()


def _patch_cpu_embedding_dtype() -> None:
    """S6 1순위: data/share_embeddings/search_lib.py:28이 device!="cuda"일 때
    model_kwargs={"torch_dtype": torch.float32}를 강제한다. 체크포인트 원본은
    이미 bf16(model.safetensors, 2.1GB)인데 fp32로 업캐스트하면서 로딩 도중
    이중 버퍼링 스파이크(651MB→3.4GB/1s, S3 4GB 컨테이너 OOM 실측 원인)가 난다.
    search_lib.py는 data/ 산출물이라 원본 수정 금지 — SentenceTransformer 생성자를
    감싸 "cpu용 float32 강제 요청"만 골라 bfloat16로 되돌리는 방식으로 우회한다.
    cuda 요청(torch.bfloat16 그대로 통과)은 건드리지 않음 — search_device는 항상
    "cpu"로 고정(config.py)이라 실질적으로 이 분기만 탄다.
    """
    import torch
    import sentence_transformers

    _Original = sentence_transformers.SentenceTransformer

    class _CPUFriendlySentenceTransformer(_Original):
        def __init__(self, *args, **kwargs):
            model_kwargs = dict(kwargs.get("model_kwargs") or {})
            if model_kwargs.get("torch_dtype") == torch.float32:
                model_kwargs["torch_dtype"] = torch.bfloat16
                model_kwargs.setdefault("low_cpu_mem_usage", True)
                kwargs["model_kwargs"] = model_kwargs
            super().__init__(*args, **kwargs)

    sentence_transformers.SentenceTransformer = _CPUFriendlySentenceTransformer


def _patch_faiss_sq8_index() -> None:
    """S6 2순위: search_lib.py가 out/index.faiss(IndexFlatIP fp32, 2.1GB)를 ASCII 임시경로로
    shutil.copy한 뒤 faiss.read_index하는 지점을 가로채, SQ8 재양자화본
    (server/artifacts/index_sq8.faiss, 0.53GB — data/ 밖에서 별도 생성, 원본 미접촉)이 있으면
    그걸로 치환한다. chunk_meta.jsonl·correction_map.json은 원본 out_dir 그대로 읽는다
    (경로 안 바꿈 — host/container에서 emb_dir이 달라도 안전).
    산출물이 없으면 아무 것도 안 하고 원본 fp32 인덱스를 그대로 쓴다(안전 폴백).
    """
    if not _SQ8_INDEX_PATH.exists():
        return
    import shutil

    _orig_copy = shutil.copy

    def _patched_copy(src, dst, *args, **kwargs):
        if str(src).endswith("index.faiss"):
            src = str(_SQ8_INDEX_PATH)
        return _orig_copy(src, dst, *args, **kwargs)

    shutil.copy = _patched_copy


def _patch_lazy_chunk_meta() -> None:
    """S6 3순위: search_lib.py:36 `self.meta = [json.loads(l) for l in open(chunk_meta.jsonl)]`가
    257,186행(원본 1.04GB)을 통째로 파싱해 파이썬 dict 리스트로 프로세스 수명 내내 들고 있는다
    (dict/문자열 객체 오버헤드로 상주 메모리가 원본의 수배 — 다이어트 전 4GB 컨테이너 실측에서
    RSS 정상상태 최대 항목). self.meta는 search_lib.py:49 `dict(self.meta[i])` 한 곳에서만,
    FAISS가 반환한 인덱스 포지션으로 포지셔널 접근된다(재청킹 없음 → 라인번호==인덱스 포지션,
    search_patch.py Resolver 주석 근거). search_lib.py는 data/ 산출물이라 원본 수정 금지 —
    Searcher를 서브클래싱해 __init__ 실행 구간에서만 builtins.open을 가로채
    chunk_meta.jsonl 읽기를 빈 이터레이터로 바꿔치기(self.meta=[]로 끝나게 해 파싱 자체를 스킵)한
    뒤, 끝나자마자 self.meta를 SQLite(server/artifacts/chunk_meta.sqlite, 빌드 시 rowid=라인번호로
    저장) 기반 지연조회 객체로 교체한다. 산출물이 없으면 아무 것도 안 하고 원본 전체 로드
    그대로 사용(안전 폴백).
    """
    if not _CHUNK_META_SQLITE_PATH.exists():
        return
    import builtins
    import json as _json
    import sqlite3

    import search_lib  # noqa: PLC0415 (emb_dir가 sys.path에 이미 들어간 뒤 호출됨)

    _OriginalSearcher = search_lib.Searcher
    _sqlite_path = str(_CHUNK_META_SQLITE_PATH)

    class _LazyChunkMeta:
        """self.meta[i] 포지셔널 접근만 지원 (search_lib.py:49가 유일한 사용처)."""

        def __init__(self, db_path: str):
            uri = f"file:{db_path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

        def __getitem__(self, idx):
            row = self._conn.execute(
                "SELECT data FROM meta WHERE idx = ?", (int(idx),)
            ).fetchone()
            if row is None:
                raise IndexError(idx)
            return _json.loads(row[0])

    class _LazySearcher(_OriginalSearcher):
        def __init__(self, out_dir=None, device="cuda"):
            real_open = builtins.open

            def _fake_open(file, *args, **kwargs):
                if str(file).endswith("chunk_meta.jsonl"):
                    return iter(())  # self.meta = [] 로 끝남 (읽기·파싱 완전 스킵)
                return real_open(file, *args, **kwargs)

            builtins.open = _fake_open
            try:
                super().__init__(out_dir=out_dir, device=device)
            finally:
                builtins.open = real_open
            self.meta = _LazyChunkMeta(_sqlite_path)

    search_lib.Searcher = _LazySearcher


def get_searcher():
    global _searcher
    if _searcher is None:
        with _searcher_lock:
            if _searcher is None:
                emb = str(settings.emb_dir)
                if emb not in sys.path:
                    sys.path.insert(0, emb)
                # search_patch.py는 경욱 작성분이라 명섭 배포본(data/share_embeddings)에
                # 없을 수 있다 — 레포 동봉 사본(server/vendor)을 뒤에 붙여 폴백.
                # emb가 sys.path 앞이므로 data/ 사본이 있으면 그쪽이 항상 우선.
                vendor = str(Path(__file__).resolve().parents[1] / "vendor")
                if vendor not in sys.path:
                    sys.path.append(vendor)
                _patch_cpu_embedding_dtype()
                _patch_faiss_sq8_index()
                _patch_lazy_chunk_meta()
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

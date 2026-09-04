# -*- coding: utf-8 -*-
"""팀 공유용 검색 라이브러리 — 임베딩 산출물만으로 동작 (코퍼스·청커 불필요).

필요 파일(이 파일과 같은 폴더의 out/ 하위): index.faiss, chunk_meta.jsonl, correction_map.json
선택: table_index.sqlite (표 행 단위 색인 — 확정 수치 조회용, search_lib은 쓰지 않음)
설치:  pip install faiss-cpu sentence-transformers torch
사용:
    from search_lib import Searcher
    s = Searcher()                          # GPU 없으면 device="cpu" (질의당 1~3초)
    hits = s.search("삼성전자 2025년 매출액", k=5)
    for h in hits: print(h["report_nm"], h["rcept_dt"], h["text"][:100])
"""
import json, os, shutil, tempfile
from pathlib import Path

import faiss
import numpy as np

MODEL_ID = "nvidia/Nemotron-3-Embed-1B-BF16"
ROOT = Path(__file__).resolve().parent


class Searcher:
    def __init__(self, out_dir=None, device="cuda"):
        out = Path(out_dir) if out_dir else ROOT / "out"
        import torch
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(MODEL_ID, trust_remote_code=True,
                                         model_kwargs={"torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32},
                                         device=device)
        # faiss C++는 한글 경로 못 열음 -> ASCII 임시경로 경유
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as tf:
            tmp = tf.name
        shutil.copy(out / "index.faiss", tmp)
        self.index = faiss.read_index(tmp)
        os.unlink(tmp)
        self.meta = [json.loads(l) for l in open(out / "chunk_meta.jsonl", encoding="utf-8")]
        self.superseded_by = json.loads((out / "correction_map.json").read_text(encoding="utf-8"))["superseded_by"]

    def search(self, query: str, k: int = 5, latest_only: bool = True, exclude_viewer: bool = True):
        """latest_only=True: 정정본이 결과에 있으면 구본 제거 (일반 질의용).
        False: 구본도 그대로 반환 (이력 분석 질의용).
        exclude_viewer=True: doc_role='viewer' 청크 제외. PDF 대체수집 3건에서 나온
        DART 뷰어 JavaScript 잔해(288청크)라 내용이 없음. 같은 문서의 실제 본문은
        doc_role='pdftext'로 따로 들어 있으므로 켜두는 것이 맞다."""
        v = self.model.encode(["query: " + query], normalize_embeddings=True,
                              convert_to_numpy=True).astype(np.float32)
        scores, ids = self.index.search(v, k * 3)
        hits = []
        for s, i in zip(scores[0], ids[0]):
            if i < 0:
                continue
            c = dict(self.meta[i])
            if exclude_viewer and c.get("doc_role") == "viewer":
                continue
            c["score"] = float(s)
            c["corrected_by"] = self.superseded_by.get(c["rcept_no"], [])
            hits.append(c)
        if latest_only:
            rn = {h["rcept_no"] for h in hits}
            hits = [h for h in hits if not any(x in rn for x in h["corrected_by"])]
        return hits[:k]

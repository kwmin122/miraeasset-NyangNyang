# -*- coding: utf-8 -*-
"""S6 2순위 산출물 빌드 스크립트 (1회성, 재현 가능하도록 repo에 포함).

data/share_embeddings/out/index.faiss(IndexFlatIP, fp32, 2.1GB)를 읽기 전용으로 읽어
SQ8(8bit 스칼라 양자화)로 재양자화 -> server/artifacts/index_sq8.faiss (0.53GB).

data/ 원본은 절대 쓰지 않음(읽기만). server/app/search.py의 _patch_faiss_sq8_index()가
이 산출물이 존재하면 자동으로 fp32 원본 대신 사용한다(없으면 안전 폴백으로 원본 그대로 사용).

사용법 (repo 루트에서, 또는 아무 위치에서든 — 경로는 __file__ 기준 상대 계산):
    .venv/bin/python3 server/tools/build_sq8_index.py

주의: aarch64(colima)에서 빌드한 인덱스는 FAISS 파일 포맷 자체는 아키텍처 무관이지만,
NCP(x86_64) 배포 전 재검증 권장 (S7 책임 — CONTEXT.md/LOG.md 참조).
"""
import time
from pathlib import Path

import faiss
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "data" / "share_embeddings" / "out" / "index.faiss"
DST = REPO_ROOT / "server" / "artifacts" / "index_sq8.faiss"


def main() -> None:
    print(f"reading {SRC} ...")
    t0 = time.time()
    src_idx = faiss.read_index(str(SRC))
    print(f"loaded ntotal={src_idx.ntotal} d={src_idx.d} metric={src_idx.metric_type} ({time.time()-t0:.1f}s)")

    assert isinstance(src_idx, faiss.IndexFlatIP), f"예상과 다른 인덱스 타입: {type(src_idx)}"

    t0 = time.time()
    vecs = src_idx.reconstruct_n(0, src_idx.ntotal)
    print(f"reconstructed {vecs.shape} dtype={vecs.dtype} ({time.time()-t0:.1f}s)")

    sq_idx = faiss.IndexScalarQuantizer(src_idx.d, faiss.ScalarQuantizer.QT_8bit, faiss.METRIC_INNER_PRODUCT)
    t0 = time.time()
    sq_idx.train(vecs)
    sq_idx.add(vecs)
    print(f"SQ8 built ntotal={sq_idx.ntotal} ({time.time()-t0:.1f}s)")

    DST.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(sq_idx, str(DST))
    size_mb = DST.stat().st_size / 1e6
    print(f"saved -> {DST} ({size_mb:.1f} MB)")

    # 정합성 체크: 무작위 쿼리 20개로 top-10 비교 (fp32 vs sq8), recall@10 측정
    rng = np.random.default_rng(0)
    sample_idx = rng.choice(src_idx.ntotal, size=20, replace=False)
    queries = vecs[sample_idx]
    _, gt_ids = src_idx.search(queries, 10)
    _, sq_ids = sq_idx.search(queries, 10)
    recalls = []
    for gt, sq in zip(gt_ids, sq_ids):
        recalls.append(len(set(gt.tolist()) & set(sq.tolist())) / 10)
    print(f"sanity recall@10 (fp32 vs sq8, 20 random queries): mean={np.mean(recalls):.3f} min={np.min(recalls):.3f}")


if __name__ == "__main__":
    main()

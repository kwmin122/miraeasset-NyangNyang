# -*- coding: utf-8 -*-
"""S6 3순위 산출물 빌드 스크립트 (1회성, 재현 가능하도록 repo에 포함).

data/share_embeddings/out/chunk_meta.jsonl(1.04GB, 257,186행)을 읽기 전용으로 읽어
server/artifacts/chunk_meta.sqlite로 변환한다.
idx(=SQLite PK) == FAISS 인덱스 포지션(0-based 라인번호, 재청킹 없음 —
data/share_embeddings/search_patch.py의 Resolver 주석이 근거).

data/ 원본은 절대 쓰지 않음(읽기만). server/app/search.py의 _patch_lazy_chunk_meta()가
이 산출물이 존재하면 자동으로 self.meta를 SQLite 지연조회로 교체한다
(없으면 안전 폴백으로 원본 전체를 파이썬 리스트로 로드하는 기존 동작 유지).

사용법 (repo 루트에서, 또는 아무 위치에서든 — 경로는 __file__ 기준 상대 계산):
    .venv/bin/python3 server/tools/build_chunk_meta_sqlite.py
"""
import json
import random
import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "data" / "share_embeddings" / "out" / "chunk_meta.jsonl"
DST = REPO_ROOT / "server" / "artifacts" / "chunk_meta.sqlite"


def main() -> None:
    DST.parent.mkdir(parents=True, exist_ok=True)
    if DST.exists():
        DST.unlink()

    conn = sqlite3.connect(str(DST))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE meta (idx INTEGER PRIMARY KEY, data TEXT NOT NULL)")

    t0 = time.time()
    n = 0
    rows = []
    with open(SRC, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.rstrip("\n")
            json.loads(line)  # 정합성 검증(파싱 가능해야 함) — 저장은 원본 텍스트 그대로(재직렬화 오차 방지)
            rows.append((i, line))
            n += 1
            if len(rows) >= 20000:
                conn.executemany("INSERT INTO meta (idx, data) VALUES (?, ?)", rows)
                conn.commit()
                rows = []
    if rows:
        conn.executemany("INSERT INTO meta (idx, data) VALUES (?, ?)", rows)
        conn.commit()

    print(f"inserted {n} rows in {time.time()-t0:.1f}s")

    # 정합성 체크: 무작위 위치 몇 개를 원본과 대조
    rng = random.Random(0)
    sample_lines = {}
    with open(SRC, encoding="utf-8") as f:
        for i, line in enumerate(f):
            sample_lines[i] = line.rstrip("\n")

    check_idxs = rng.sample(range(n), 20)
    mismatches = 0
    for idx in check_idxs:
        row = conn.execute("SELECT data FROM meta WHERE idx = ?", (idx,)).fetchone()
        if row is None or row[0] != sample_lines[idx]:
            mismatches += 1
            print(f"MISMATCH at idx={idx}")
        else:
            d = json.loads(row[0])
            print(f"ok idx={idx} rcept_no={d.get('rcept_no')} chunk_id={d.get('chunk_id')}")
    print(f"mismatches={mismatches}/20")

    conn.execute("VACUUM")
    conn.close()
    size_mb = DST.stat().st_size / 1e6
    print(f"saved -> {DST} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()

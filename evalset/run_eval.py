#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""평가셋 채점기 — 실행 중인 API 서버를 호출해 채점한다 (평가와 동일 경로, SPEC §3).

사용:  python3 evalset/run_eval.py [--base http://localhost:8000] [--file evalset/questions_v1.jsonl]
의존성 없음 (표준 라이브러리만).
"""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

LIMIT_MARKERS = ["확인되지 않", "확인할 수 없", "존재하지 않", "찾지 못", "제공된 공시 데이터"]


def norm(s: str) -> str:
    return re.sub(r"[,\s]", "", s or "")


def call_api(base: str, q: dict) -> tuple[dict, float]:
    params = urllib.parse.urlencode({"question_id": q["qid"], "question": q["question"]})
    t0 = time.time()
    with urllib.request.urlopen(f"{base}/answer?{params}", timeout=120) as r:
        return json.loads(r.read().decode("utf-8")), time.time() - t0


def grade(q: dict, resp: dict) -> dict:
    answer, ctx = resp.get("answer", ""), resp.get("retrieved_context", "")
    ans_n = norm(answer)
    trap = q.get("trap")

    if trap in ("기간밖", "상장전"):
        answer_ok = any(m in answer for m in LIMIT_MARKERS)
    elif trap == "프롬프트공격":
        answer_ok = all(norm(p) not in ans_n for p in q.get("must_not_patterns", []))
    elif q["closed"]:
        answer_ok = any(norm(p) in ans_n for p in q["accept_patterns"]) and all(
            norm(p) not in ans_n for p in q.get("must_not_patterns", []))
    else:  # open: 필수 키워드 커버리지 60% 이상
        pats = q["accept_patterns"]
        answer_ok = (sum(norm(p) in ans_n for p in pats) / len(pats) >= 0.6) if pats else False

    ev = q.get("evidence", [])
    ev_hits = sum(e["rcept_no"] in ctx for e in ev)
    return {
        "answer_ok": answer_ok,
        "evidence_recall": round(ev_hits / len(ev), 2) if ev else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--file", default=str(Path(__file__).parent / "questions_v1.jsonl"))
    args = ap.parse_args()

    questions = [json.loads(l) for l in open(args.file, encoding="utf-8") if l.strip()]
    results, by_type = [], defaultdict(list)

    for q in questions:
        try:
            resp, elapsed = call_api(args.base, q)
            g = grade(q, resp)
        except Exception as e:
            resp, elapsed, g = {"answer": f"(호출 실패: {e})"}, 0.0, {"answer_ok": False, "evidence_recall": 0.0}
        row = {"qid": q["qid"], "task_type": q["task_type"], "trap": q.get("trap"),
               "elapsed_s": round(elapsed, 1), **g, "answer_head": resp.get("answer", "")[:120]}
        results.append({**row, "response": resp})
        by_type[q["task_type"]].append(row)
        mark = "✅" if g["answer_ok"] else "❌"
        ev = f" ev={g['evidence_recall']}" if g["evidence_recall"] is not None else ""
        print(f"{mark} {q['qid']:<12} {row['elapsed_s']:>5}s{ev}  {row['answer_head'][:80]}")

    print("\n== 유형별 요약 ==")
    for t in sorted(by_type):
        rows = by_type[t]
        acc = sum(r["answer_ok"] for r in rows) / len(rows)
        evs = [r["evidence_recall"] for r in rows if r["evidence_recall"] is not None]
        ev_avg = f"{sum(evs)/len(evs):.2f}" if evs else "-"
        lat = max(r["elapsed_s"] for r in rows)
        print(f"유형 {t}: 정답률 {acc:.0%} ({sum(r['answer_ok'] for r in rows)}/{len(rows)}) | 근거recall {ev_avg} | 최대지연 {lat}s")

    out = Path(__file__).parent / f"results_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n상세 결과: {out}")


if __name__ == "__main__":
    main()

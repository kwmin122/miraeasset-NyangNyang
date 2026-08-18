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
    # '*' 제거: HCX가 수치 일부만 볼드 처리(**19**%)하면 패턴이 별표로 쪼개짐 (S5b 실측)
    return re.sub(r"[,\s*]", "", s or "")


def call_api(base: str, q: dict) -> tuple[dict, float]:
    params = urllib.parse.urlencode({"question_id": q["qid"], "question": q["question"]})
    t0 = time.time()
    with urllib.request.urlopen(f"{base}/answer?{params}", timeout=120) as r:
        return json.loads(r.read().decode("utf-8")), time.time() - t0


def _fmt_date_variants(rcept_no: str) -> set[str]:
    d = (rcept_no or "")[:8]
    if len(d) != 8 or not d.isdigit():
        return set()
    y, m, day = d[:4], d[4:6], d[6:8]
    return {d, f"{y}-{m}-{day}", f"{y}.{m}.{day}", f"{y}/{m}/{day}",
            f"{y}년 {m}월 {day}일", f"{y}년{m}월{day}일"}


def _core_report_name(report_nm: str) -> str:
    """보고서명에서 [기재정정] 등 대괄호 태그를 제거한 핵심 명칭."""
    return re.sub(r"\[[^\]]*\]", "", report_nm or "").strip()


def _citation_shown(e: dict, answer: str) -> bool:
    """(S4, 근거표시 보조지표 — 참고용, answer_ok 미반영) answer에 evidence의
    rcept_no 원문 또는 (접수일 표기 + 보고서명 일부)가 함께 드러나는지 본다."""
    rcept_no = e.get("rcept_no", "")
    if rcept_no and rcept_no in answer:
        return True
    if any(v in answer for v in _fmt_date_variants(rcept_no)):
        core = norm(_core_report_name(e.get("report_nm", "")))
        if core and core[:4] in norm(answer):
            return True
    return False


def grade(q: dict, resp: dict, *, legacy_grading: bool = False) -> dict:
    """legacy_grading=True: S4 이전 로직 재현(open 문항 must_not 미적용) — 회귀 롤백용 스위치.
    기본값(False)이 신규 동작이며, 기존 18문에 대해 결과가 legacy와 동일함을 오프라인 재채점으로 확인함
    (T4-O-001/002 외 open+must_not 보유 문항 없음, 두 문항 모두 answer_ok가 이미 False라 AND 연산 결과 불변)."""
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
    else:  # open: 필수 키워드 커버리지 60% 이상 (+ must_not 위반 시 실패; S4 확장, (b))
        pats = q["accept_patterns"]
        coverage_ok = (sum(norm(p) in ans_n for p in pats) / len(pats) >= 0.6) if pats else False
        must_not_ok = legacy_grading or all(norm(p) not in ans_n for p in q.get("must_not_patterns", []))
        answer_ok = coverage_ok and must_not_ok

    # 근거 recall: rcept_no 또는 alt_rcept_no(정정/원공시 계보) 중 하나라도 검색되면 인정 (S4 확장, (a))
    ev = q.get("evidence", [])

    def _ev_hit(e: dict) -> bool:
        cands = [e["rcept_no"], *e.get("alt_rcept_no", [])]
        return any(c in ctx for c in cands)

    ev_hits = sum(_ev_hit(e) for e in ev)

    # 근거표시 보조지표 (S4, Fable 승인 (C)) — 리포트 참고용 컬럼일 뿐 answer_ok 판정에는 관여하지 않음
    citation_hits = sum(_citation_shown(e, answer) for e in ev)

    return {
        "answer_ok": answer_ok,
        "evidence_recall": round(ev_hits / len(ev), 2) if ev else None,
        "citation_display": round(citation_hits / len(ev), 2) if ev else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--file", default=str(Path(__file__).parent / "questions_v1.jsonl"))
    ap.add_argument("--legacy-grading", action="store_true",
                     help="S4 이전 채점 로직 재현 (open 문항 must_not 미적용) — 회귀 의심 시 롤백용")
    args = ap.parse_args()

    questions = [json.loads(l) for l in open(args.file, encoding="utf-8") if l.strip()]
    results, by_type = [], defaultdict(list)

    for q in questions:
        try:
            resp, elapsed = call_api(args.base, q)
            g = grade(q, resp, legacy_grading=args.legacy_grading)
        except Exception as e:
            resp, elapsed, g = {"answer": f"(호출 실패: {e})"}, 0.0, {
                "answer_ok": False, "evidence_recall": 0.0, "citation_display": None}
        row = {"qid": q["qid"], "task_type": q["task_type"], "trap": q.get("trap"),
               "elapsed_s": round(elapsed, 1), **g, "answer_head": resp.get("answer", "")[:120]}
        results.append({**row, "response": resp})
        by_type[q["task_type"]].append(row)
        mark = "✅" if g["answer_ok"] else "❌"
        ev = f" ev={g['evidence_recall']}" if g["evidence_recall"] is not None else ""
        cd = f" cite={g['citation_display']}" if g["citation_display"] is not None else ""
        print(f"{mark} {q['qid']:<12} {row['elapsed_s']:>5}s{ev}{cd}  {row['answer_head'][:80]}")

    print("\n== 유형별 요약 ==")
    for t in sorted(by_type):
        rows = by_type[t]
        acc = sum(r["answer_ok"] for r in rows) / len(rows)
        evs = [r["evidence_recall"] for r in rows if r["evidence_recall"] is not None]
        ev_avg = f"{sum(evs)/len(evs):.2f}" if evs else "-"
        cds = [r["citation_display"] for r in rows if r["citation_display"] is not None]
        cd_avg = f"{sum(cds)/len(cds):.2f}" if cds else "-"
        lat = max(r["elapsed_s"] for r in rows)
        print(f"유형 {t}: 정답률 {acc:.0%} ({sum(r['answer_ok'] for r in rows)}/{len(rows)}) | "
              f"근거recall {ev_avg} | 근거표시(참고) {cd_avg} | 최대지연 {lat}s")

    out = Path(__file__).parent / f"results_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n상세 결과: {out}")


if __name__ == "__main__":
    main()

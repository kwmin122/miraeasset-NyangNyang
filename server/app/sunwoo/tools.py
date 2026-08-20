"""도구 계층 — 각 slice의 '수집' 부분을 HCX 호출 없이 꺼내 쓰는 인터페이스.

왜 따로 두는가. 지금까지는 질문 하나 = 유형 하나 = slice 하나였다.
"삼성SDI 유증의 예정가와 확정가를 비교해라"는 원본 조회 + 최종본 조회 + 비교,
세 단계인데 slice 하나는 한 단계만 한다. 그래서 각 slice가 '찾는 일'까지만
하고 근거팩을 돌려주게 잘라내고, 플래너가 그 도구들을 여러 개 엮는다.

기존 slice 코드는 건드리지 않는다. 여기서 그 함수들을 import해서 감싼다.
도구는 전부 (items, trace) 를 돌려준다. items는 build_context가 먹는 dict 리스트.

코퍼스 범위는 상수로 둔다. 데이터 README가 2023-01-01 ~ 2026-03-31 이라고
명시했고, 이걸 코드가 모르면 "2022년 건"을 조용히 0건으로 흘려 '없다'와
'범위 밖이다'를 구분 못 한다.
"""
import re

from slice1 import s, RCEPT_IDX, retrieve
from slice4 import (docs, by_rcept, pick_keywords, group_chains,
                    resolve_latest, asked_months, question_terms)
from slice5 import as_item, first_text
from slice6 import gather_year
from extract import ALIAS

CORPUS_FROM = 2023
CORPUS_TO = 2026
CORPUS_TO_MONTH = 3


def in_corpus(year, month=None):
    y = int(year)
    if y < CORPUS_FROM or y > CORPUS_TO:
        return False
    if y == CORPUS_TO and month and int(month) > CORPUS_TO_MONTH:
        return False
    return True


_CORP_NAMES = None
_CODE2NAME = None


def _corp_tables():
    global _CORP_NAMES, _CODE2NAME
    if _CORP_NAMES is None:
        _CORP_NAMES = {d["corp_name"] for d in docs}
        _CODE2NAME = {}
        for d in docs:
            sc = str(d.get("stock_code") or "").strip()
            if sc:
                _CODE2NAME.setdefault(sc, d["corp_name"])
            ln = str(d.get("listed_name") or "").strip()
            if ln:
                _CODE2NAME.setdefault(ln, d["corp_name"])
    return _CORP_NAMES, _CODE2NAME


def norm_corp(name):
    """플래너가 낸 기업명을 manifest의 corp_name으로 맞춘다.

    실측에서 플래너가 '005930'(종목코드), '한화 에어로스페이스'(띄어쓰기),
    'LIG넥스원'(옛 사명)을 냈고 collect가 정확 일치로 걸러 전부 0건이 됐다.
    extract가 쓰던 ALIAS + 종목코드/통용명 표 + 공백 제거 순으로 맞춘다.
    못 맞추면 원문 그대로 돌려준다 — 여기서 지어내지 않는다.
    """
    if not name:
        return name
    names, code2 = _corp_tables()
    raw = str(name).strip()
    if raw in names:
        return raw
    if raw in ALIAS and ALIAS[raw] in names:
        return ALIAS[raw]
    if raw in code2:
        return code2[raw]
    compact = raw.replace(" ", "")
    if compact in names:
        return compact
    for n in names:
        if n.replace(" ", "") == compact:
            return n
    return raw


def _doc_body(d, budget):
    idxs = RCEPT_IDX.get(d["rcept_no"], [])
    body = ""
    for i in idxs:
        if len(body) >= budget:
            break
        body += s.meta[i]["text"]
    return body


def _doc_item(d, orig=None, budget=2500, note=None):
    orig = orig or d
    fixed = d["rcept_no"] != orig["rcept_no"]
    sp = note
    if sp and fixed:
        sp = (f"{sp}. 본문은 {d['rcept_dt']}에 접수된 최신 정정본이다")
    elif sp is None and fixed:
        sp = (f"이 건은 {orig['rcept_dt']}에 공시된 사건이다. "
              f"본문은 {d['rcept_dt']}에 접수된 최신 정정본이다")
    return {"text": _doc_body(d, budget), "report_nm": d["report_nm"],
            "rcept_dt": orig["rcept_dt"], "rcept_no": d["rcept_no"],
            "corp_name": d.get("corp_name"), "section_path": sp}


def chain_of(d):
    """원본부터 최신 정정본까지 접수일 순 목록. 양 끝을 대조할 때 쓴다."""
    seen, out = set(), []
    cur = d
    while cur and cur["rcept_no"] not in seen:
        seen.add(cur["rcept_no"])
        out.append(cur)
        nexts = [by_rcept[x] for x in s.superseded_by.get(cur["rcept_no"], [])
                 if x in by_rcept and x not in seen]
        cur = max(nexts, key=lambda x: (x["rcept_dt"], x["rcept_no"])) if nexts else None
    return out


def _root_of(d):
    """정정본이 들어오면 원본까지 거슬러 올라간다."""
    seen = set()
    cur = d
    while cur["rcept_no"] not in seen:
        seen.add(cur["rcept_no"])
        prev = [o for o, subs in s.superseded_by.items()
                if cur["rcept_no"] in subs and o in by_rcept and o not in seen]
        if not prev:
            return cur
        cur = by_rcept[min(prev, key=lambda r: by_rcept[r]["rcept_dt"])]
    return cur


def collect(corp, year=None, topic="", version="latest", recency=None,
            date=None, month=None, limit=12, question=""):
    """manifest 목록 필터. slice4의 필터 체인과 같은 순서.

    version  latest   정정 체인을 최신본으로 교체 (기본, slice4와 동일)
             original 정정 체인의 원본만
             all      체인 전체를 접수일 순으로 (변화 과정을 물을 때)
    recency  latest   rcept_dt 내림차순 1건 — "가장 최근"을 코드가 정한다
    date     YYYYMMDD 특정 접수일 건만
    """
    trace = []
    cands = [d for d in docs if d["corp_name"] == corp]
    trace.append(f"{corp} {len(cands)}건")

    if date:
        cands = [d for d in cands if d["rcept_dt"] == str(date).replace("-", "")]
        trace.append(f"접수일 {date} 필터 {len(cands)}건")
    elif year:
        ys = [str(y) for y in (year if isinstance(year, (list, tuple)) else [year])]
        cands = [d for d in cands if d["rcept_dt"][:4] in ys]
        trace.append(f"연도 {'/'.join(ys)} 필터 {len(cands)}건")
        if month:
            ms = [f"{ys[0]}{int(month):02d}"]
            cands = [d for d in cands if d["rcept_dt"][:6] in ms] or cands

    kws = pick_keywords(topic, cands) if topic else []
    if kws:
        cands = [d for d in cands if any(k in d["report_nm"] for k in kws)]
        trace.append(f"키워드({'/'.join(kws[:3])}) {len(cands)}건")

    if not cands:
        return [], trace

    if version == "all":
        roots = {}
        for d in cands:
            r = _root_of(d)
            roots.setdefault(r["rcept_no"], r)
        items = []
        for r in roots.values():
            ch = chain_of(r)
            for i, d in enumerate(ch):
                tag = "원본" if i == 0 else f"{i}차 정정"
                items.append(_doc_item(d, r, note=f"{tag} ({d['rcept_dt']} 접수)"))
        trace.append(f"정정 체인 전체 {len(items)}건")
        return items[:limit], trace

    if version == "original":
        roots = {}
        for d in cands:
            r = _root_of(d)
            roots.setdefault(r["rcept_no"], r)
        pairs = [(r, r) for r in roots.values()]
        trace.append(f"원본만 {len(pairs)}건")
    else:
        grouped = group_chains(cands)
        pairs = resolve_latest(grouped)
        trace.append(f"최신본 {len(pairs)}건")

    if question:
        months = asked_months(question, [str(y) for y in (year or [])] if year else [])
        if months:
            ym = {m[:4] for m in months}
            nar = [(d, o) for d, o in pairs
                   if o["rcept_dt"][:4] not in ym or o["rcept_dt"][:6] in months]
            if nar:
                pairs = nar
        terms = question_terms(question)
        if terms and len(pairs) > 1:
            nar = [(d, o) for d, o in pairs
                   if RCEPT_IDX.get(d["rcept_no"])
                   and any(t in s.meta[RCEPT_IDX[d["rcept_no"]][0]]["text"] for t in terms)]
            if nar and len(nar) < len(pairs):
                pairs = nar

    if recency == "latest":
        pairs.sort(key=lambda p: (p[1]["rcept_dt"], p[1]["rcept_no"]), reverse=True)
        pairs = pairs[:1]
        trace.append(f"가장 최근 1건 ({pairs[0][1]['rcept_dt']})")

    n = min(len(pairs), limit)
    budget = max(800, min(4000, 14000 // max(n, 1)))
    # 원본/최신본을 두 단계로 나눠 뽑을 때는 어느 쪽인지 근거 헤더에 박아야 한다.
    # 안 박으면 HCX가 최신본만 읽고 그 안의 앞쪽 숫자를 집는다(실측: 정정 전 예정가
    # 169,200 대신 중간 정정본의 146,200을 답함).
    tag = {"original": "이 건은 정정 전 원본이다. 여기 적힌 값이 '예정·당초' 값이다",
           "latest": "이 건은 최신본이다. 여기 적힌 값이 '확정·최종' 값이다"}.get(version)
    items = [_doc_item(d, o, budget, note=tag) for d, o in pairs[:limit]]
    return items, trace


def find(corp, year=None, topic="", question="", k=5):
    """벡터 검색 + slice1의 보정 전부(정정교체·별도/연결 보강·재정렬).

    처음엔 맨 벡터검색(gather)만 감쌌다가 별도/연결·사명변경 문항이 한꺼번에 깨졌다.
    찾는 일은 slice1.retrieve 한 곳에 두고 여기서는 그걸 부른다.
    검색 질의는 원 질문을 쓴다 — topic만 쓰면 질문의 조건어(별도/연결 등)가 빠진다.
    """
    q = question or f"{corp} {year or ''} {topic}".strip()
    hits, tr = retrieve(q, corp, year, topic, k)
    return hits, tr


def trace_event(corp, date=None, year=None, topic=""):
    """특정 공시의 후속을 추적한다.

    slice5는 '해지' 공시만 앵커로 잡는다. 유상증자 철회처럼 정정 계보로
    표현되는 후속은 못 본다. 여기서는 둘 다 본다:
      ① 지목된 원본의 정정 체인 전체 (철회·변경이 정정본으로 나온다)
      ② 해지 공시 중 원본과 짝이 되는 것 (slice5 방식)
    """
    trace = []
    origin_items, _ = collect(corp, year=year, date=date, topic=topic,
                              version="all", limit=20)
    items = list(origin_items)
    trace.append(f"원공시 정정 체인 {len(origin_items)}건")

    cands = [d for d in docs if d["corp_name"] == corp]
    ends = [d for d in cands if "해지" in d["report_nm"]]
    ends = [l for l, _ in resolve_latest(group_chains(ends))]
    if date:
        key = str(date).replace("-", "")
        key_fmt = f"{key[:4]}-{key[4:6]}-{key[6:]}"
        hit = [e for e in ends if key_fmt in first_text(e) or key in first_text(e)]
        if hit:
            ends = hit
    for e in ends[:3]:
        items.append(as_item(e, "해지"))
    if ends:
        trace.append(f"해지 공시 {min(len(ends),3)}건")
    return items, trace


def yeartab(corp, year, topic="", question=""):
    """slice6의 연도별 수치 + 배경. answer_type6과 같은 gather_year를 쓴다."""
    hits, bg, tr = gather_year(corp, year, question, topic)
    return hits + bg, tr


_NUM = re.compile(r"\(?-?[\d,]+\.?\d*\)?")


def _nums_in(text):
    out = []
    for m in _NUM.finditer(text or ""):
        t = m.group().strip("()")
        try:
            v = float(t.replace(",", ""))
            if m.group().startswith("("):
                v = -v
            out.append(v)
        except ValueError:
            pass
    return out


def compute(op, values):
    """합·차·증감률을 파이썬이 계산한다. HCX에 시키면 식은 적어도 틀린다."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    if op == "sum":
        return sum(vals)
    if op == "diff" and len(vals) >= 2:
        return vals[-1] - vals[0]
    if op == "pct_change" and len(vals) >= 2 and vals[0]:
        return (vals[-1] - vals[0]) / abs(vals[0]) * 100
    if op == "max":
        return max(vals)
    if op == "min":
        return min(vals)
    return None


def fmt_num(v):
    if v is None:
        return "계산 불가"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.2f}"


# ── 단계 검산 ───────────────────────────────────────────────────────────
# 질문 분석은 맨 앞에서 한 번, 답변은 맨 뒤에서 한 번. 그 사이 수집 단계는
# 질문을 다시 보지 않는다. 그래서 "2023년 4월"에 날짜를 지어내 0건이 나와도
# 코드가 그걸 그대로 받아 "공시가 없습니다"를 냈다(실측).
# 여기서 수집 결과가 질문과 맞는지 코드가 대조한다. HCX를 부르지 않으므로
# 공짜이고 결정적이다.

_STOPQ = {"얼마", "무엇", "어디", "어떻게", "누구", "몇", "각각", "그리고", "또는",
          "기준", "관련", "대한", "공시", "내용", "경우", "비교", "설명", "정리",
          "알려줘", "인가", "인지", "된다", "한다", "이다"}


def question_clues(question):
    """질문에서 근거와 대조할 고유 단서를 뽑는다.

    영문 대문자 약어(VLGC), 고유명사로 보이는 한글 3자 이상, 콤마 포함 숫자.
    흔한 의문사·서술어는 뺀다. 목록을 새로 만드는 게 아니라 질문에서만 뽑는다.
    """
    q = question or ""
    out = set(re.findall(r"[A-Z][A-Za-z0-9\-]{2,}", q))
    out |= {w for w in re.findall(r"[가-힣]{3,}", q) if w not in _STOPQ}
    out |= set(re.findall(r"\d[\d,]{3,}", q))
    return out


def clue_hits(items, clues):
    """근거 본문에 실제로 등장하는 단서만 돌려준다."""
    if not items or not clues:
        return set()
    blob = " ".join((it.get("text") or "") + " " + str(it.get("report_nm") or "")
                    for it in items)
    return {c for c in clues if c in blob}

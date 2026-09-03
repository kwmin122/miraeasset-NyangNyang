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
                    resolve_latest, latest_version, asked_months, question_terms)
from slice5 import as_item, first_text
from slice6 import gather_year
from extract import ALIAS
from attribute import content_words

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


def full_chain(rcept_no):
    """이 공시에서 갈라져 나간 정정 계보의 접수번호 전부(분기 포함, 오름차순).

    chain_of 는 회수용이라 분기점에서 최신 한쪽만 걷는다(한 원공시에 정정본이
    둘이면 하나를 건너뛴다). 계보 '표시'에는 전 분기가 필요해서 따로 둔다 —
    실측(T5-C-004): 20241030 유상증자 체인이 11-13·11-14 두 정정본으로 갈라졌고
    회수가 11-14 쪽만 걸어 11-13 접수분이 답변 어디에도 안 남았다(근거표시 0.00).
    """
    seen, todo = set(), [str(rcept_no)]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        todo += [x for x in s.superseded_by.get(cur, []) if x not in seen]
    return sorted(seen)


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


_LINK_CACHE = {}


def _base_title(nm):
    return re.sub(r"^\[[^\]]*\]", "", str(nm or "")).strip()


def latest_of(d):
    """최신 정정본을 찾되, correction_map에 빠진 연결을 조회 시점에 보완한다.

    correction_map에 안 이어진 정정본이 484건 있다(에코프로비엠 신규시설투자가
    그 예 — 원공시 2023-05-23, 정정본 2024-10-22가 남남으로 있다).
    그러면 연도 필터가 정정본을 잘라내고 폐기된 값을 답한다.

    원본 데이터는 건드리지 않는다. 명섭 형이 재임베딩으로 correction_map을
    고치면 아래 보완은 그냥 안 걸리고 지나간다.

    연결 조건은 좁게 잡았다. 느슨하게 하면 같은 제목의 별개 공시가 엮인다
    (한 기업의 공급계약체결은 제목이 다 같아서 411건이 서로 엮인다).
      ① 같은 기업 ② 정정표시 뗀 제목 동일 ③ 정정본이 원본보다 나중
      ④ **정정본 본문에 원본의 접수일이 적혀 있다** (정정공시는 원공시의
         이사회결의일·계약일을 본문에 다시 적는다)
    실측: 484건 중 이 조건을 통과하는 것은 51건. 나머지는 진짜 별개 공시다.
    """
    lv = latest_version(d)
    if lv["rcept_no"] != d["rcept_no"]:
        return lv
    rn = d["rcept_no"]
    if rn in _LINK_CACHE:
        return _LINK_CACHE[rn] or lv
    dt, title = d["rcept_dt"], _base_title(d["report_nm"])
    iso = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
    found = []
    for c in docs:
        if (not c.get("is_correction") or c["corp_name"] != d["corp_name"]
                or c["rcept_dt"] <= dt or _base_title(c["report_nm"]) != title):
            continue
        t = _doc_body(c, 3000)
        if iso in t or dt in t:
            found.append(c)
    best = max(found, key=lambda x: (x["rcept_dt"], x["rcept_no"])) if found else None
    _LINK_CACHE[rn] = best
    return best or lv


def collect(corp, year=None, topic="", version="latest", recency=None,
            date=None, month=None, limit=12, question="", filer=None):
    """manifest 목록 필터. slice4의 필터 체인과 같은 순서.

    version  latest   정정 체인을 최신본으로 교체 (기본, slice4와 동일)
             original 정정 체인의 원본만
             all      체인 전체를 접수일 순으로 (변화 과정을 물을 때)
    recency  latest   rcept_dt 내림차순 1건 — "가장 최근"을 코드가 정한다
    date     YYYYMMDD 특정 접수일 건만
    filer    제출인(flr_nm). 지분공시는 남이 그 회사 지분을 신고하는 문서라
             발행회사(corp_name)와 제출인이 항상 다르다(holding 1083건 전부).
             "삼성전자가 레인보우로보틱스에 대해 제출한 보고서"에서
             corp=레인보우로보틱스, filer=삼성전자다. 이 축이 없으면
             corp=삼성전자로 걸려 엉뚱하게 삼성전자 자기 지분을 답한다(실측).
    """
    trace = []
    # corp_name 을 그대로 비교하면 별칭이 통째로 0건이 된다.
    # 코퍼스의 엔씨소프트 표기는 "NC"라서 collect("엔씨소프트")가 0건이었다(OQ-58).
    # find 는 정규화를 거치므로 같은 질문이 find 로 가면 통과해 오래 안 드러났다.
    # 정규화 이름으로 0건이면 원래 이름으로 되돌려 기존 동작을 보존한다.
    _c = norm_corp(corp) if corp else corp
    cands = [d for d in docs if d["corp_name"] == _c]
    if not cands and _c != corp:
        cands = [d for d in docs if d["corp_name"] == corp]
    trace.append(f"{corp} {len(cands)}건")

    if filer:
        f = str(filer).strip()
        nar = [d for d in cands if f in str(d.get("flr_nm") or "")]
        if not nar:  # 공백 표기 차이 대비
            fc = f.replace(" ", "")
            nar = [d for d in cands if fc in str(d.get("flr_nm") or "").replace(" ", "")]
        if nar:
            cands = nar
            trace.append(f"제출인({f}) 필터 {len(cands)}건")
        else:
            trace.append(f"제출인({f}) 일치 0건 -> 필터 생략")

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
        # resolve_latest를 그대로 쓰지 않는다. 그 함수는 최신본 기준으로 중복을
        # 지우는데, 정정본 하나가 원공시 여러 건을 한꺼번에 정정하는 경우가 있다
        # (한미약품 2023-08-01 3건이 2024-02-01 정정본 하나로 수렴).
        # 그러면 3건이 1건으로 뭉쳐 "총 몇 건이냐"에 2건이라 답한다.
        # 값을 물으면 최신본이 맞지만 개수를 물으면 원공시 개수가 맞다.
        # 최신본이 같은 원공시가 둘 이상이면 원공시를 그대로 남겨 개수를 지킨다.
        # 개수를 묻는 질문일 때만 원공시를 복원한다.
        # 값을 물으면 원공시의 폐기된 값이 답에 쓰인다(LG엔솔 해지금액이
        # 정정 전 4,082,377,000,000으로 나왔다). 둘은 반대 방향이라 질문으로 가른다.
        counting = bool(re.search(r"몇 건|몇건|총 \d+\s*건|\d+\s*건", question or ""))
        byl = {}
        for orig in group_chains(cands):
            lv = latest_of(orig)
            byl.setdefault(lv["rcept_no"], []).append((lv, orig))
        pairs, n_ex = [], 0
        for gsub in byl.values():
            if counting and len(gsub) > 1:
                pairs += [(o, o) for _, o in gsub]
                n_ex += len(gsub) - 1
            else:
                pairs += gsub[:1] if len(gsub) > 1 else gsub
        if n_ex:
            trace.append(f"개수 질문 + 한 정정본이 원공시 여럿 정정 -> 원공시 {n_ex}건 복원")
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
    if op == "ratio" and len(vals) >= 2 and vals[1]:
        # 앞 값 ÷ 뒤 값을 백분율로. CAPEX÷매출, 배당성향 같은 비중 질문용이다.
        # 이게 없어서 HCX가 직접 나눗셈을 하다 단위를 뭉갰다
        # (삼성전자 CAPEX를 매출보다 큰 187조로 적고 그걸 11%라고 계산한 실측).
        return vals[0] / vals[1] * 100
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


def ko_amount(n):
    """원 단위 정수를 '9조 6,030억원' 표기로 바꾼다. 조 미만은 None.

    공시 원문은 금액을 원 단위 숫자로만 적는데 채점은 한글 단위 표기도 따로 요구한다
    (T5-C-002 '9조 6,030억', T6-O-005 '12조 7,835억원' 실측). 억 미만은 버린다 —
    원문에도 그렇게 적히고 반올림하면 없던 오차를 만든다.
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n < 10 ** 12:
        return None
    jo, rest = divmod(n, 10 ** 12)
    eok = rest // 10 ** 8
    return f"{jo}조 {eok:,}억원" if eok else f"{jo}조원"


# ── 단계 검산 ───────────────────────────────────────────────────────────
# 질문 분석은 맨 앞에서 한 번, 답변은 맨 뒤에서 한 번. 그 사이 수집 단계는
# 질문을 다시 보지 않는다. 그래서 "2023년 4월"에 날짜를 지어내 0건이 나와도
# 코드가 그걸 그대로 받아 "공시가 없습니다"를 냈다(실측).
# 여기서 수집 결과가 질문과 맞는지 코드가 대조한다. HCX를 부르지 않으므로
# 공짜이고 결정적이다.

_STOPQ = {"얼마", "무엇", "어디", "어떻게", "누구", "몇", "각각", "그리고", "또는",
          "기준", "관련", "대한", "공시", "내용", "경우", "비교", "설명", "정리",
          "알려줘", "인가", "인지", "된다", "한다", "이다"}


def coverage(corp):
    """그 기업이 코퍼스에 언제부터 들어와 있는지와, 그것을 증명할 정기보고서.

    부재를 단언할 때 근거로 쓴다. 신규 상장사는 상장일이 최초 정기보고서 본문에
    적혀 있어서, 그 한 건만 붙여도 "왜 그 해 자료가 없는지"가 증명된다.
    반환 (최초접수일, 최종접수일, 근거 아이템 목록). 기업이 없으면 (None, None, []).
    """
    if not corp:
        return None, None, []
    _c = norm_corp(corp)
    cands = [d for d in docs if d["corp_name"] == _c]
    if not cands and _c != corp:
        cands = [d for d in docs if d["corp_name"] == corp]
    if not cands:
        return None, None, []
    first = min(d["rcept_dt"] for d in cands)
    last = max(d["rcept_dt"] for d in cands)
    per = sorted((d for d in cands if d.get("doc_group") == "periodic"),
                 key=lambda d: d["rcept_no"])
    items = []
    if per:
        items.append(_doc_item(per[0], budget=1200,
                               note="코퍼스에 수록된 이 기업의 최초 정기보고서"))
    return first, last, items

def question_clues(question):
    """질문에서 근거와 대조할 고유 단서를 뽑는다.

    영문 대문자 약어(VLGC), 고유명사로 보이는 한글 3자 이상, 콤마 포함 숫자.
    흔한 의문사·서술어는 뺀다. 목록을 새로 만드는 게 아니라 질문에서만 뽑는다.
    """
    q = question or ""
    out = set(re.findall(r"[A-Z][A-Za-z0-9\-]{2,}", q))
    # 한글은 content_words로 뽑는다. 정규식으로 그냥 자르면 조사가 딸려와
    # "레인보우로보틱스에"가 단서가 되고, 근거에 "레인보우로보틱스"가 있어도
    # 매칭이 안 돼 검산이 항상 미달로 나온다(실측: 단서 9개 중 0개 매칭).
    # content_words는 조사를 떼고 흔한 서술어·의문사(STOP)도 걸러준다.
    out |= {w for w in content_words(q) if len(w) >= 3 and w not in _STOPQ}
    out |= set(re.findall(r"\d[\d,]{3,}", q))
    return out


def clue_hits(items, clues):
    """근거 본문에 실제로 등장하는 단서만 돌려준다."""
    if not items or not clues:
        return set()
    blob = " ".join((it.get("text") or "") + " " + str(it.get("report_nm") or "")
                    for it in items)
    return {c for c in clues if c in blob}

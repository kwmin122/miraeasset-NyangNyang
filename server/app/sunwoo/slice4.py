import json, re
from pathlib import Path
from slice1 import s, ROOT, API_KEY, URL, find_path, RCEPT_IDX
from hcx import call_hcx
from attribute import BASE_RULES, GUARD_RULES, build_context

MANIFEST_PATH = find_path([
    "3.공시/3.공시/corpus/manifest.jsonl",
    "data/corpus/manifest.jsonl",
    "corpus/manifest.jsonl",
])
docs = [json.loads(line) for line in open(MANIFEST_PATH, encoding="utf-8")]

rcept_index = RCEPT_IDX

by_rcept = {d["rcept_no"]: d for d in docs}

FUNDING_KEYWORDS = ["유상증자", "전환사채", "신주인수권부사채", "교환사채", "조건부자본증권"]

ITEM_ALIAS = {
    "자사주": "자기주식", "자기주식": "자기주식",
    "수주": "공급계약", "계약": "공급계약",
    "배당": "배당", "합병": "합병", "분할": "분할",
    "유상증자": "유상증자", "무상증자": "무상증자",
    "신탁": "신탁", "소송": "소송",
}


AGG_WORDS = ("합계", "총액", "총 ", "합쳐", "합하", "모두 얼마", "차이", "차액",
             "비교", "계산", "몇 배", "증감", "대비")


def asked_months(question, years):
    """질문에 적힌 '{연도}년 {n}월'에서 연월을 뽑는다.

    연도만으로 거르면 후보가 수십 건이 되고, 그중 어느 것이 질문 대상인지
    고르는 일이 LLM에 넘어간다. 질문이 달을 명시했으면 코드가 좁히는 게 맞다.
    """
    keep = {str(y) for y in (years or [])}
    out = []
    for y, m in re.findall(r"(20\d{2})\s*년\s*(\d{1,2})\s*월", question or ""):
        if not keep or y in keep:
            out.append(f"{y}{int(m):02d}")
    return sorted(set(out))


def question_terms(question):
    """질문에 나온 영문 대문자 약어를 뽑는다. VLGC·VLAC·ROA 같은 것들이다."""
    return [t for t in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", question or "")]


def wants_aggregate(question):
    """질문이 합계·차이 같은 집계를 요구하는지 본다.

    건별 나열만 시키면 "3건의 합계는?"에 답을 못 하고,
    항상 합산을 허용하면 서로 무관한 건을 더해 엉뚱한 수를 낸다.
    질문 문구로 갈라야 둘 다 잡힌다.
    """
    return any(w in (question or "") for w in AGG_WORDS)


def pick_keywords(item, cands):
    """item에서 실제 보고서명에 존재하는 키워드를 고른다.

    item을 통째로 쓰면 안 된다. HCX는 "신규시설투자 금액"처럼 말을 붙여 주는데
    실제 보고서명은 "신규시설투자등"이라 부분일치가 실패하고 후보가 0건이 된다.
    별칭 사전을 먼저 보고, 없으면 item에서 뽑은 토막 중 후보 제목에 실제로
    등장하는 것만 남긴다. 하나도 안 남으면 키워드 필터를 걸지 않는다 —
    엉뚱한 문자열로 0건을 만들어 거짓 부재를 단언하는 것보다 낫다.
    """
    if not item:
        return []
    if "자금" in item:
        return FUNDING_KEYWORDS

    hit = [v for k, v in ITEM_ALIAS.items() if k in item]
    if hit:
        return sorted(set(hit))

    titles = " ".join(d["report_nm"] for d in cands)
    toks = re.findall(r"[가-힣]{2,}", item)
    toks = sorted(set(toks), key=len, reverse=True)
    out = []
    for t in toks:
        while len(t) >= 2:
            if t in titles:
                out.append(t)
                break
            t = t[:-1]
    return sorted(set(out), key=len, reverse=True)[:3]


def group_chains(cands):
    """같은 사건의 원본과 [기재정정]본을 하나로 묶고 최신 1건만 남긴다."""
    chains = []
    last = {}
    for d in sorted(cands, key=lambda x: (x["rcept_dt"], x["rcept_no"])):
        base = re.sub(r"^\[[^\]]*\]", "", d["report_nm"])
        if d["is_correction"] and base in last:
            chains[last[base]].append(d)
        else:
            chains.append([d])
            last[base] = len(chains) - 1
    return [c[-1] for c in chains]


def latest_version(d):
    """이 공시를 대체한 정정본이 코퍼스에 있으면 최신 정정본으로 바꿔 돌려준다.

    group_chains만으로는 부족하다. 연도 필터가 먼저 돌기 때문에 이듬해 접수된
    정정본은 후보에서 이미 잘려나가 있고, 그러면 group_chains는 폐기된 원본을
    '정정 없는 단독 체인'으로 보고 통과시킨다(감사 C-2).
    여기서는 연도와 무관하게 correction_map을 따라가 최신본을 찾는다.

    seen 가드는 선택이 아니다. correction_map에 상호 참조가 실재해서
    (삼성전자·이마트 등 7건) 가드가 없으면 무한루프로 프로세스가 멈춘다.
    """
    seen = set()
    while True:
        rn = d["rcept_no"]
        if rn in seen:
            return d
        seen.add(rn)
        nexts = [by_rcept[x] for x in s.superseded_by.get(rn, [])
                 if x in by_rcept and x not in seen]
        if not nexts:
            return d
        d = max(nexts, key=lambda x: (x["rcept_dt"], x["rcept_no"]))


def resolve_latest(cands):
    """후보를 최신 정정본으로 교체하고 중복을 제거한다.

    반환은 (최신본, 원본) 쌍의 리스트. 근거 라벨에 '원공시 → 정정' 관계를
    적어주려면 원본 접수일이 필요해서 짝으로 들고 다닌다.
    """
    out, seen = [], set()
    for orig in cands:
        latest = latest_version(orig)
        if latest["rcept_no"] in seen:
            continue
        seen.add(latest["rcept_no"])
        out.append((latest, orig))
    return out


SYSTEM4 = (
    "너는 DART 공시 기반 분석 비서다. 여러 건의 공시 근거가 제공된다. "
    "반드시 근거 내용만 사용해 항목을 유형별로 분류·정리하라. 각 항목에 공시 날짜를 붙여라. "
    "정정 신고서에는 '정 정 전'과 '정 정 후' 값이 나란히 표기된다. "
    "반드시 '정 정 후' 값만 사용하라. '정 정 전' 값은 이미 폐기된 값이므로 절대 인용하지 마라."
    + BASE_RULES + GUARD_RULES
)

def answer_type4(question, corp, year, item):
    trace = []
    cands = [d for d in docs if d["corp_name"] == corp]
    trace.append(f"기업 필터 후 {len(cands)}건")

    years = year if isinstance(year, (list, tuple)) else ([year] if year else [])
    years = [str(y) for y in years if y]
    if years:
        cands = [d for d in cands if d["rcept_dt"][:4] in years]
        trace.append(f"연도 필터(rcept_dt {'/'.join(years)}) 후 {len(cands)}건")

    kws = pick_keywords(item, cands)

    if kws:
        cands = [d for d in cands if any(k in d["report_nm"] for k in kws)]
        trace.append(f"키워드 필터({'/'.join(kws[:3])}) 후 {len(cands)}건")

    pairs = []
    if cands:
        cands = group_chains(cands)
        pairs = resolve_latest(cands)
        n_fixed = sum(1 for latest, orig in pairs if latest["rcept_no"] != orig["rcept_no"])
        trace.append(f"정정 체인 그룹핑 후 {len(pairs)}건"
                     + (f" (연도 밖 정정본으로 {n_fixed}건 교체)" if n_fixed else ""))

        months = asked_months(question, years)
        if months:
            # 월이 명시된 연도만 그 달로 좁힌다. 질문이 "2023년 초"처럼 월을 안 밝힌
            # 연도까지 같이 잘라내면 그쪽 근거가 통째로 사라진다.
            ym_years = {m[:4] for m in months}
            narrowed = [(d, o) for d, o in pairs
                        if o["rcept_dt"][:4] not in ym_years or o["rcept_dt"][:6] in months]
            if narrowed:
                pairs = narrowed
                trace.append(f"질문에 적힌 월({'/'.join(months)})로 좁혀 {len(pairs)}건")

        terms = question_terms(question)
        if terms and len(pairs) > 1:
            def has_term(d):
                idxs = rcept_index.get(d["rcept_no"], [])
                return bool(idxs) and any(t in s.meta[idxs[0]]["text"] for t in terms)
            narrowed = [(d, o) for d, o in pairs if has_term(d)]
            if narrowed and len(narrowed) < len(pairs):
                pairs = narrowed
                trace.append(f"질문 용어({'/'.join(terms[:3])})로 좁혀 {len(pairs)}건")

    if not pairs:
        cond = f"기업 '{corp}'" + (f", {'·'.join(years)}년" if years else "")
        return {"answer": f"{cond} 조건에 해당하는 공시를 찾지 못했습니다. 기업명과 연도를 확인해 주세요.",
                "retrieved_context": "", "think_trace": " -> ".join(trace)}

    # 공시 건수가 적으면 건당 본문을 넉넉히, 많으면 짧게. 총 투입량을 일정하게 유지한다.
    # 첫 청크 800자만 넣으면 자금조달 목적 내역·이자율처럼 표 뒤쪽에 오는 수치가 잘린다.
    n_docs = min(len(pairs), 12)
    budget = max(800, min(4000, 14000 // max(n_docs, 1)))

    items = []
    for d, orig in pairs[:12]:
        idxs = rcept_index.get(d["rcept_no"], [])
        if not idxs:
            continue
        body = ""
        for i in idxs:
            if len(body) >= budget:
                break
            body += s.meta[i]["text"]
        fixed = d["rcept_no"] != orig["rcept_no"]
        items.append({"text": body, "report_nm": d["report_nm"],
                      "rcept_dt": orig["rcept_dt"], "rcept_no": d["rcept_no"],
                      "corp_name": d.get("corp_name"),
                      "section_path": (f"이 건은 {orig['rcept_dt']}에 공시된 사건이다. "
                                       f"본문은 {d['rcept_dt']}에 접수된 최신 정정본이다"
                                       if fixed else None)})
    n = len(items)
    context, _ev, _ = build_context(items, max_chars=budget)

    base = (f"근거 자료 (총 {n}건):\n{context}질문: {question}\n"
            f"근거 {n}건은 각각 별개의 건이다. 근거 1번부터 {n}번까지 순서대로, "
            f"건마다 항목을 나누어 정리하라. 한 건도 합치거나 빠뜨리지 마라. "
            f"각 건의 금액은 그 근거 안의 '정 정 후' 값을 쓰고, "
            f"각 건은 '근거 1번' 같은 내부 번호가 아니라 공시 접수일로 구분해 제목을 달아라. "
            f"금액은 원문에 적힌 자릿수를 그대로 옮겨라. 백만원 단위로 줄여 쓰지 마라. "
            f"각 건에 딸린 세부 항목(자금 용도별 금액, 이자율, 수량, 계약상대, 계약기간)이 "
            f"근거에 있으면 빠짐없이 함께 적어라.")
    if wants_aggregate(question):
        ask = (base + " 건별 정리를 마친 뒤, 질문이 요구한 합계 또는 차이를 마지막에 제시하라. "
                      "계산은 '303,200,000,000 + 317,300,000,000 = 620,500,000,000'처럼 "
                      "식을 그대로 적어 보여라. 원문에 없는 값을 계산에 넣지 마라.")
    else:
        ask = base + " 여러 건의 금액을 임의로 합산하지 마라."
    messages = [{"role": "system", "content": SYSTEM4},
                {"role": "user", "content": ask}]

    text, note = call_hcx(messages, max_tokens=1400)
    if text is None:
        return {"answer": None, "retrieved_context": context,
                "think_trace": " -> ".join(trace) + f" -> {note}"}

    trace.append(f"HCX 분류·정리 완료 (근거 {n}건 투입)")

    return {"answer": text, "retrieved_context": context,
            "think_trace": " -> ".join(trace) + note}

import json, re
from slice1 import s, find_path, RCEPT_IDX

MANIFEST_PATH = find_path([
    "3.공시/3.공시/corpus/manifest.jsonl",
    "data/corpus/manifest.jsonl",
    "corpus/manifest.jsonl",
])
if MANIFEST_PATH is None:
    raise SystemExit(
        "corpus/manifest.jsonl 을 찾지 못했다. data/corpus/ 를 배치한 뒤 다시 실행해라. "
        "(data/ 는 용량 때문에 git에서 제외돼 있어 clone 직후에는 없다. README 참조)")
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


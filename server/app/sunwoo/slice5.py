import re
from slice1 import s
from slice4 import docs, rcept_index, group_chains, resolve_latest
from hcx import call_hcx
from attribute import BASE_RULES, GUARD_RULES, build_context

SYSTEM5 = (
    "너는 DART 공시 기반 분석 비서다. 계약의 체결과 해지를 잇는 질문이 주어진다. "
    "근거에는 해지 공시와 체결 공시 후보가 섞여 있다. "
    "해지 공시 본문의 원 계약 정보(상대방, 계약일, 내용)와 일치하는 체결 공시만 짝으로 인정하라. "
    "일치하는 체결 공시가 없으면 해지 공시 본문만으로 답하라. "
    "각 계약을 '체결(날짜) -> 해지(날짜, 사유)' 시간순으로 서술하라. "
    "자기주식취득신탁계약의 해지는 계약 파기가 아니라 통상적인 신탁 종료이므로 구분해서 서술하라."
    + BASE_RULES + GUARD_RULES
)

def question_date_keys(question):
    """질문에 적힌 날짜를 공시 본문에서 찾을 수 있는 표기들로 바꾼다.

    "2023년 10월 6일 공시한 계약의 후속은?" 같은 질문이 온다.
    해지 공시 본문에는 '관련공시 2023-10-06'처럼 원공시 날짜가 적혀 있어서
    이 날짜로 어느 해지가 그 계약의 것인지 짚어낼 수 있다.
    """
    out = []
    for y, m, d in re.findall(r"(20\d{2})[.\-년\s]+(\d{1,2})[.\-월\s]+(\d{1,2})", question or ""):
        y, m, d = y, int(m), int(d)
        out += [f"{y}-{m:02d}-{d:02d}", f"{y}.{m:02d}.{d:02d}",
                f"{y}{m:02d}{d:02d}", f"{y}년 {m}월 {d}일"]
    return out


def as_item(d, tag=""):
    idxs = rcept_index.get(d["rcept_no"], [])
    if not idxs:
        return None
    return {"text": s.meta[idxs[0]]["text"][:800], "report_nm": d["report_nm"],
            "rcept_dt": d["rcept_dt"], "rcept_no": d["rcept_no"],
            "corp_name": d.get("corp_name"), "section_path": tag or None}


def first_text(d):
    """공시 본문 첫 청크. 체결-해지 짝짓기의 단어 비교에 쓴다."""
    idxs = rcept_index.get(d["rcept_no"], [])
    return s.meta[idxs[0]]["text"] if idxs else ""


def _keywords(text, limit=40):
    """본문에서 고유명사성 단어를 뽑는다. 상대방 회사명·사업명이 주로 걸린다."""
    toks = re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{2,}", text or "")
    stop = {"주식회사", "계약", "공시", "체결", "해지", "회사", "당사", "내용", "관련",
            "금액", "기간", "상대", "이사회", "결의", "사항", "보고서", "공급", "판매"}
    out = []
    for t in toks:
        if t in stop or t in out:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def pick_starts(ends_doc, cands, k=2):
    """해지 공시와 단어가 가장 많이 겹치는 체결 공시를 고른다.

    예전에는 '해지일 이전 최신 2건'을 접수일 순으로 골랐는데, 공급계약이 수십 건
    동시 진행되는 건설·조선사에서는 최신 2건이 진짜 짝일 이유가 없다.
    독립 정답셋 측정에서 공급계약 recall@2가 0/11이었다(감사 H-9).
    해지 공시 본문에는 원 계약의 상대방·사업명이 적혀 있으므로 그 단어로 맞춘다.
    """
    if "공급계약" in ends_doc["report_nm"]:
        family = "공급계약"
    elif "신탁" in ends_doc["report_nm"]:
        family = "신탁"
    else:
        return []

    pool = [d for d in cands if "체결" in d["report_nm"]
            and family in d["report_nm"] and d["rcept_dt"] <= ends_doc["rcept_dt"]]
    if not pool:
        return []

    keys = set(_keywords(first_text(ends_doc)))
    scored = []
    for d in pool:
        overlap = len(keys & set(_keywords(first_text(d))))
        scored.append((overlap, d["rcept_dt"], d))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [d for _, _, d in scored[:k]]

def answer_type5(question, corp):
    """연도를 인자로 받지 않는다.

    유형5는 체결과 해지를 잇는 질문인데, 두 사건은 대개 다른 해에 있다
    (예: 2023-10 체결 -> 2025-12 해지). 질문에 적힌 연도로 해지 공시를 거르면
    답이 되는 해지가 통째로 잘려나간다. 연도는 아래 question_date_keys가
    해지 본문에 적힌 '관련공시 2023-10-06' 같은 원공시 날짜와 맞추는 데 쓴다.
    거르는 게 아니라 순서를 정하는 데만 쓰는 것이 이 슬라이스에서는 맞다.
    """
    trace = []
    cands = [d for d in docs if d["corp_name"] == corp]
    trace.append(f"기업 필터 후 {len(cands)}건")

    ends = [d for d in cands if "해지" in d["report_nm"]]

    raw_n = len(ends)
    ends = [latest for latest, _ in resolve_latest(group_chains(ends))]
    if raw_n != len(ends):
        trace.append(f"해지 앵커 {raw_n}건 -> 정정 병합 후 {len(ends)}건")
    else:
        trace.append(f"해지 앵커 {len(ends)}건")

    keys = question_date_keys(question)
    if keys and len(ends) > 1:
        hit = [e for e in ends if any(k in first_text(e) for k in keys)]
        if hit:
            rest = [e for e in ends if e not in hit]
            ends = hit + rest
            trace.append(f"질문 날짜와 원공시가 일치하는 해지 {len(hit)}건 우선")

    if not ends:
        return {"answer": f"'{corp}'의 공시 {len(cands)}건 중 해지 공시가 없습니다. 해지된 계약이 없거나 기업명·연도를 확인해 주세요.",
                "retrieved_context": "", "think_trace": " -> ".join(trace)}

    items = []
    used = set()
    n_starts = 0
    for e in ends[:10]:
        if e["rcept_no"] not in used:
            used.add(e["rcept_no"])
            it = as_item(e, "해지")
            if it:
                items.append(it)

        for st in pick_starts(e, cands):
            if st["rcept_no"] not in used:
                used.add(st["rcept_no"])
                it = as_item(st, "체결 후보")
                if it:
                    items.append(it)
                    n_starts += 1

    context, _ev, _ = build_context(items)

    trace.append(f"해지 {min(len(ends), 10)}건 + 체결 후보 {n_starts}건 근거 조립")

    messages = [{"role": "system", "content": SYSTEM5},
                {"role": "user", "content": f"근거 자료:\n{context}질문: {question}\n해지 공시는 총 {min(len(ends), 10)}건이다. 각 해지 건을 빠짐없이 다뤄라. 해지되지 않은 계약은 목록에 넣지 마라."}
    ]
    text, note = call_hcx(messages, max_tokens=1400)
    if text is None:
        return {"answer": None, "retrieved_context": context,
                "think_trace": " -> ".join(trace) + f" -> {note}"}

    trace.append("HCX 체결-해지 체인 서술 완료")
    return {"answer": text, "retrieved_context": context,
            "think_trace": " -> ".join(trace) + note}

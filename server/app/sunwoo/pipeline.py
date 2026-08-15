import re

from extract import extract
from slice1 import answer_type1
from slice3 import answer_type3
from slice4 import answer_type4
from slice5 import answer_type5
from slice6 import answer_type6
from verify import check_dates
from attribute import parse_ev, check_grounding, check_hedging, trace_note


def unify_units(text, ctx):
    """금액 단위 표기의 띄어쓰기를 근거 원문 쪽으로 되돌린다.

    한화오션 문항에서 근거 원문은 '12조 7,835억원'인데 HCX가 '12조 7,835억 원'으로
    띄어 썼다. 숫자도 단위도 맞는데 공백 하나 때문에 채점 문자열이 어긋나
    한 문항이 통째로 틀린 것으로 처리됐다.

    임의로 붙이거나 띄우는 게 아니다. 근거 본문에서 두 표기의 등장 횟수를 세서
    많이 쓰인 쪽으로 맞춘다. '원문 표기를 그대로 옮긴다'는 원칙을 코드가 집행하는
    것이고, 근거에 없는 표기를 새로 만들어내지 않는다.
    """
    for u in ("조", "억", "만", "천"):
        joined, spaced = u + "원", u + " 원"
        nj, ns = ctx.count(joined), ctx.count(spaced)
        if nj > ns:
            text = text.replace(spaced, joined)
        elif ns > nj:
            text = text.replace(joined, spaced)
    return text


def clean(r):
    """모든 유형이 이 출구를 거친다. 후처리와 검증을 여기 한 곳에만 둔다."""
    if r.get("answer"):
        r["answer"] = re.sub(r"\*+", "", r["answer"])
        r["answer"] = unify_units(r["answer"], r.get("retrieved_context", ""))

        bad = check_dates(r["answer"], r.get("retrieved_context", ""))
        if bad:
            r["think_trace"] += f" -> [검증경고] 근거에 없는 날짜: {', '.join(bad)}"

        ev = parse_ev(r.get("retrieved_context", ""))
        found = check_grounding(r["answer"], ev)
        if found:
            r["think_trace"] += " -> " + trace_note(found)

        hedged = check_hedging(r["answer"])
        if hedged:
            r["think_trace"] += (" -> [추측어투] "
                                 + " / ".join(f"'{h[:34]}'" for h in hedged[:3]))
    return r


def normalize_slots(slots):
    """extract가 준 슬롯의 타입을 여기서 한 번에 정리한다.

    slice마다 방어를 흩뿌리면 구멍이 계속 생긴다. 실제로 slice6만 item=None에
    무방비여서 크래시가 났다. 라우터 입구에서 한 번 정리하면 그런 편차가 사라진다.

    반환 (qtype, corps, years, item)
    """
    try:
        qtype = int(str(slots.get("qtype")).strip())
    except (TypeError, ValueError):
        qtype = None
    if qtype not in (1, 2, 3, 4, 5, 6):
        qtype = None

    raw = slots.get("corps") or []
    if isinstance(raw, str):
        raw = [raw]
    corps = [str(c).strip() for c in raw if c]

    y = slots.get("year")
    cand = y if isinstance(y, list) else [y]
    years = []
    for v in cand:
        m = re.search(r"20\d{2}", str(v or ""))
        if m:
            years.append(int(m.group()))
    years = sorted(set(years))

    item = slots.get("item")
    if isinstance(item, (list, tuple)):
        # HCX가 항목을 리스트로 줄 때가 있다. str()로 감싸면 "['매출액', ...]"가 되어
        # 검색어와 키워드 필터가 통째로 망가진다.
        item = " ".join(str(x) for x in item if x)
    elif not isinstance(item, str):
        item = "" if item is None else str(item)

    return qtype, corps, years, item


def _answer_question(question):
    slots = extract(question)
    if "error" in slots:
        return {"answer": "질의를 해석하지 못했습니다. 기업명과 연도, 찾으시는 항목을 함께 적어 다시 물어봐 주세요.",
                "retrieved_context": "",
                "think_trace": f"슬롯 추출 실패: {str(slots.get('raw'))[:200]}"}

    qtype, corps, years, item = normalize_slots(slots)
    trace = [f"슬롯: {slots}"]

    corp = corps[0] if corps else None
    year = years[-1] if years else None

    if qtype == 3 and len(corps) < 2 and len(years) >= 2:
        qtype = 6
        trace.append("라우터 보정: 기업 1곳 + 연도 2개 -> 유형 6")
    elif qtype == 3 and len(corps) < 2:
        # slice3은 기업이 2곳이 아니면 근거 없이 "비교 대상 부족"만 돌려주고 끝난다.
        # 그건 어떤 경우에도 정답이 아니라 단일 검색으로 되돌리는 편이 낫다.
        qtype = 1
        trace.append("라우터 보정: 비교 대상 1곳 -> 유형 1")
    elif qtype == 6 and len(corps) >= 2:
        qtype = 3
        trace.append("라우터 보정: 기업 2곳 -> 유형 3")
    elif qtype == 6 and len(corps) == 1 and re.search(
            r"발행|증자|사채|계약 체결|공급계약|투자|취득|처분|해지", question or ""):
        # 유형6은 정기보고서의 연도 대조를 전제한다. 사건성 수시공시(사채 발행·계약 등)를
        # 두 시점 물으면 벡터 검색이 정기보고서 수천 청크에 묻힌다.
        # 그런 질문은 manifest 직행으로 문서를 확정하는 유형4가 맞다.
        qtype = 4
        trace.append("라우터 보정: 단일 기업 + 수시공시 사건 -> 유형 4")
    elif (qtype in (1, 2, 4) and corps
          and re.search(r"이후 어떻게|이후에 어떻게|후속|결국|해지된|해지됐|취소된", question or "")
          and re.search(r"20\d{2}[.\-년]", question or "")):
        # "2023년 10월 6일 공시한 계약은 이후 어떻게 됐는가" 같은 후속 추적 질문.
        # 단일 검색으로는 원공시만 찾고 그 뒤에 일어난 해지·정정을 못 본다.
        # slice5가 해지 공시를 앵커로 잡고 원공시와 짝지어야 답이 나온다.
        qtype = 5
        trace.append("라우터 보정: 특정 공시의 후속 추적 -> 유형 5")
    elif qtype == 5 and not re.search(
            r"해지|해제|취소|종료|이후 어떻게|이후에 어떻게|후속|결국", question or ""):
        # 위 규칙의 반대 방향이다. 승격 규칙만 있고 강등 규칙이 없어서,
        # extract가 유형5로 잘못 보내면 slice5가 해지 공시를 앵커로 찾다가
        # "해지 공시가 없습니다"라고 단언해 버린다. 질문은 해지를 묻지도 않았는데.
        # 실측: 문장 끝을 "얼마인가?" -> "얼마인지 알려주실 수 있나요?"로 바꾸자
        # extract가 유형5로 분류해 TR-NAME-001이 통째로 틀렸다.
        # 판정 어휘는 위 승격 규칙과 같은 것을 쓴다. 새 어휘를 만들지 않는다.
        qtype = 1
        trace.append("라우터 보정: 후속·해지 어휘가 없음 -> 유형 1")
    elif qtype in (1, 2) and re.search(r"\d+\s*건|여러 건|각 건", question or ""):
        # "3건의 합계", "두 건을 비교" 처럼 건수를 명시한 질문은 단일 검색으로 풀 수 없다.
        # extract가 이걸 유형1로 보내는 일이 실측으로 확인돼 코드가 되돌린다.
        qtype = 4
        trace.append("라우터 보정: 질문이 여러 건을 지목 -> 유형 4")
    elif qtype is None:
        qtype = 1
        trace.append("유형 판별 실패 -> 유형 1로 처리")

    if qtype in (1, 2):
        r = answer_type1(question, corp=corp, year=year, item=item)
    elif qtype == 3:
        r = answer_type3(question, corps, year, item)
    elif qtype == 4:
        r = answer_type4(question, corp, years, item)
    elif qtype == 5:
        r = answer_type5(question, corp)
    else:
        r = answer_type6(question, corp, year, item)

    r["think_trace"] = " -> ".join(trace) + " -> " + r.get("think_trace", "")
    return clean(r)


def answer_question(question):
    """바깥에서 부르는 이름. 어떤 예외가 나도 dict를 돌려준다.

    제출이 API 서버 형태라 미처리 예외는 500 응답, 즉 그 문항 무응답이 된다.
    이 5줄이 나머지 모든 결함의 피해 상한을 '오답 1건'으로 묶는다.
    """
    try:
        return _answer_question(question)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"answer": "처리 중 오류가 발생해 답변을 생성하지 못했습니다.",
                "retrieved_context": "",
                "think_trace": f"미처리 예외: {type(e).__name__}: {e}"}


if __name__ == "__main__":
    q = input("질문: ")
    r = answer_question(q)
    print("\n답변:", r["answer"])
    print("\n추론 과정:", r["think_trace"])

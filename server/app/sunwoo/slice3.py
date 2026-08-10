from slice1 import s, search, year_of, prefer_financial
from hcx import call_hcx
from attribute import (BASE_RULES, GUARD_RULES, SCOPE_RULES, TABLE_RULES,
                       build_context)

SYSTEM3 = (
    "너는 DART 공시 기반 분석 비서다. 두 기업의 근거 자료가 기업별로 나뉘어 번호와 함께 제공된다. "
    "반드시 근거 내용만 사용하라. 답변 순서: 1) 각 기업의 해당 수치를 근거에서 찾아 명시, "
    "2) 두 수치를 비교해 결론 제시. 두 수치의 차이를 말할 때는 근거의 원문 수치를 그대로 옮겨 적고 "
    "직접 계산한 값에는 '계산값'이라고 표시하라."
    + SCOPE_RULES + BASE_RULES + GUARD_RULES
)

def gather(corp, year, item, k=3):
    q = " ".join(str(x) for x in (corp, f"{year}년" if year else None, item) if x)
    hits = search(q, k=40)
    hits = [h for h in hits if h["corp_name"] == corp]
    if year:
        hits = [h for h in hits if year_of(h) == year]
    hits, _ = prefer_financial(hits, item)
    return hits[:k]

def answer_type3(question, corps, year, item):
    trace = []

    if len(corps) < 2:
        return {"answer": "비교 대상 기업 두 곳을 파악하지 못했습니다. 기업명을 확인해 주세요.",
                "retrieved_context": "", "think_trace": "비교 대상 부족"}
    a, b = corps[0], corps[1]
    hits_a = gather(a, year, item)
    trace.append(f"{a} 검색·필터 후 {len(hits_a)}건")
    hits_b = gather(b, year, item)
    trace.append(f"{b} 검색·필터 후 {len(hits_b)}건")

    if not hits_a or not hits_b:
        missing = a if not hits_a else b
        return {"answer": f"{missing}의 해당 조건 공시가 확인되지 않아 비교할 수 없습니다.",
                "retrieved_context": "", "think_trace": " -> ".join(trace)}
    
    total = len(hits_a) + len(hits_b)
    ctx_a, _, nxt = build_context(hits_a, start=1, label=a, total=total)
    ctx_b, _, _ = build_context(hits_b, start=nxt, label=b, total=total)
    context = ctx_a + ctx_b
    messages = [{"role": "system", "content": SYSTEM3},
                {"role": "user", "content": f"근거 자료 (총 {total}건):\n{context}질문: {question}"}]
    text, note = call_hcx(messages, max_tokens=900)
    if text is None:
        return {"answer": None, "retrieved_context": context,
                "think_trace": " -> ".join(trace) + f" -> {note}"}
    trace.append("HCX 비교 생성 완료")

    return {"answer": text, "retrieved_context": context,
            "think_trace": " -> ".join(trace) + note}

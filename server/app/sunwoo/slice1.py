import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def find_path(rels, start=None):
    """상위 폴더를 거슬러 올라가며 상대 경로 후보를 찾는다.

    이 코드는 두 트리에서 돌아야 한다. 개발 폴더(미래에셋증권 대회/agent)와
    팀 레포(miraeasset-NyangNyang/server/app/sunwoo)는 데이터까지의 깊이가 다르다.
    후보를 몇 개 열거해 두면 배치가 한 칸만 달라져도 import 단계에서 죽으므로,
    루트에 닿을 때까지 올라가며 찾는다.
    """
    here = (start or ROOT).resolve()
    for base in [here, *here.parents]:
        for rel in rels:
            p = base / rel
            if p.exists():
                return p
    return None


EMB_DIR = find_path(["share_embeddings", "data/share_embeddings"])
if EMB_DIR:
    sys.path.append(str(EMB_DIR))
from search_lib import Searcher

from hcx import call_hcx, API_KEY, URL
from attribute import (BASE_RULES, GUARD_RULES, SCOPE_RULES, TABLE_RULES,
                       build_context)


def year_of(h):
    """청크의 기준 연도를 돌려준다.

    base_year는 정기보고서에만 채워져 있고 수시공시(주요사항보고서·거래소·지분 공시)는
    전부 None이다. 그래서 base_year만 보면 연도를 지정한 질문에서 수시공시가
    카테고리째 탈락한다. 없으면 접수일 연도로 대신한다.
    """
    by = h.get("base_year")
    if by is not None:
        return by
    rd = str(h.get("rcept_dt") or "")
    return int(rd[:4]) if rd[:4].isdigit() else None

FX_UNIT = re.compile(r"\(\s*단위\s*[:：]([^)]{0,40})\)")
FX_CUR = re.compile(r"GBP|SGD|IDR|VND|USD|EUR|JPY|CNY|THB|INR|BRL|MYR|HKD|AUD|RUB")
KRW_TOK = re.compile(r"백만원|십억원|억원|천원|만원|\(\s*단위\s*[:：]\s*원")
OVERSEAS_Q = ("해외", "법인", "현지", "종속", "자회사", "지점")
FIN_SEC = ("요약재무정보", "재무제표")


def _sp(h):
    """section_path의 공백을 지운다.

    첨부 재무제표 경로가 '(첨부)연 결 재 무 제 표 > 주석'처럼 글자 사이에 공백이
    박혀 있어서, 정규화 없이 '연결'·'재무제표'를 찾으면 판정이 빗나간다.
    """
    return str(h.get("section_path") or "").replace(" ", "")


def _fx_only(h):
    """단위 선언이 외화뿐이고 원화 표기가 없는 청크 = 해외 종속법인 표.

    삼성화재 2024 청크 2,495건 중 8건만 걸리고, 그 8건이 오답 16,675천SGD와
    5,363천GBP를 만든 청크 전부다. 정답을 가진 청크는 하나도 안 걸린다.
    """
    t = h.get("text") or ""
    us = FX_UNIT.findall(t)
    return bool(us) and any(FX_CUR.search(u) for u in us) and not KRW_TOK.search(t)


def _n_con(h):
    """'연결' 밀도. 섹션경로에 있으면 가중치 2를 더한다."""
    return (h.get("text") or "").count("연결") + (2 if "연결" in _sp(h) else 0)


def _scope(question):
    """(별도질문, 연결질문). 둘 다 걸리거나 둘 다 아니면 꺼버린다."""
    q = question or ""
    sep, con = ("별도" in q), ("연결" in q)
    return (sep, con) if sep != con else (False, False)


def scope_rerank(hits, question):
    """질문이 별도/연결을 못박았을 때만 켜지는 재정렬.

    낱말로 고르면 진다. 정답이 든 요약별도재무정보 청크에는 '별도'가 0회이고,
    오답을 만든 싱가포르법인 표에는 '별도재무제표 기준으로 작성'이 2회 박혀 있다.
    표 제목이 청킹 경계에서 앞 청크로 잘려나간 탓이다. 그래서 낱말 대신
    외화 단위 여부·'연결' 밀도·섹션으로 층을 만든다. 버리지 않고 뒤로만 민다.
    """
    want_sep, want_con = _scope(question)
    if not (want_sep or want_con):
        return hits, False
    ask_overseas = any(w in (question or "") for w in OVERSEAS_Q)

    def tier(h):
        sp = _sp(h)
        if not ask_overseas and _fx_only(h):
            return 3
        n_con = _n_con(h)
        if want_sep:
            if n_con >= 2:
                return 2
            return 0 if any(x in sp for x in FIN_SEC) else 1
        if n_con >= 2:
            return 0
        return 2 if ("4.재무제표" in sp or "5.재무제표주석" in sp) else 1

    before = [tier(h) for h in hits]
    return sorted(hits, key=tier), before != sorted(before)


def scope_backfill(hits, question, corp, year, item="", n=2):
    """스코프가 명시된 질문에서 해당 재무 표를 s.meta에서 직접 끌어와 앞에 끼운다.

    재정렬만으로는 부족하다. 재정렬은 검색이 물어온 것 안에서만 순서를 바꾸는데,
    삼성화재 2024 청크는 2,495건이라 정답이 검색 상위에 못 들어오면 소용이 없다.
    slice6.gather_bg가 쓰는 필터 직행 방식을 스코프 질문에 그대로 적용한다.
    """
    want_sep, want_con = _scope(question)
    if not (want_sep or want_con) or not corp or not year:
        return hits, 0

    pool = []
    for kw in ((item or "").strip(), ""):
        for m in s.meta:
            if m.get("corp_name") != corp or year_of(m) != year:
                continue
            if not any(x in _sp(m) for x in FIN_SEC):
                continue
            if kw and kw not in (m.get("text") or ""):
                continue
            if _fx_only(m):
                continue
            nc = _n_con(m)
            if want_sep and nc >= 2:
                continue
            if want_con and nc < 2:
                continue
            pool.append(m)
        if pool:
            break
    if not pool:
        return hits, 0

    pool.sort(key=lambda m: (0 if "사업보고서" in str(m.get("report_nm")) else 1,
                             0 if m.get("is_correction") else 1,
                             0 if "요약재무정보" in _sp(m) else (2 if "첨부" in _sp(m) else 1),
                             m.get("chunk_ix") or 0))
    have = {(h.get("rcept_no"), h.get("section_path"), h.get("chunk_ix")) for h in hits}
    add = [m for m in pool
           if (m.get("rcept_no"), m.get("section_path"), m.get("chunk_ix")) not in have][:n]
    return add + hits, len(add)


def prefer_financial(hits, item):
    """재무제표 섹션 재정렬은 실측에서 역효과가 나 비활성 상태다(2026-08-10).

    TEAM_GUIDE §6-2의 "재무제표 섹션 필터 병용 권장"을 재정렬로 구현했더니
    유형①은 답을 못 찾고 유형③은 영업이익이 14조에서 3.5조로 틀어졌다.
    벡터 검색이 이미 관련도순인데 섹션 조건을 앞세우면 관련도 낮은 표 조각이
    상위를 차지한다. 평가셋으로 실제 반올림값 오답이 확인된 뒤 다른 방식으로 접근한다.
    """
    return hits, False


SYSTEM = (
    "너는 DART 공시 기반 분석 비서다. 반드시 제공된 근거 자료의 내용만으로 답하라."
    + SCOPE_RULES + BASE_RULES + GUARD_RULES
)
def _make_searcher():
    """검색기를 만든다. 팀 규약대로 PatchedSearcher를 경유한다.

    PatchedSearcher는 ①뷰어 청크(DART PDF 뷰어의 JS·CSS가 본문으로 임베딩된 288건) 제거
    ②정정 최신본 판정을 top-k 창과 무관하게 수행한다. search_lib의 latest_only는
    정정본이 우연히 같은 창에 잡힐 때만 구본을 지웠다.

    주의: PatchedSearcher는 생성자에서 Searcher를 새로 만든다. 우리가 Searcher를 따로
    들고 있으면 임베딩 모델과 FAISS가 두 번 올라가 메모리가 배로 든다. 그래서 여기서는
    PatchedSearcher만 만들고 그 안의 인스턴스를 meta·superseded_by 접근에 재사용한다.
    """
    try:
        from search_patch import PatchedSearcher
    except ImportError:
        return Searcher(device="cpu"), None
    p = PatchedSearcher(device="cpu")
    return p._s, p


s, _patched = _make_searcher()


def search(query, k=5):
    """검색 진입점. PatchedSearcher가 있으면 그쪽으로 보낸다."""
    if _patched is not None:
        return _patched.search(query, k=k)
    return s.search(query, k=k)

def answer_type1(question, corp=None, year=None, item="", k=5):
    trace = []
    # k를 40에서 120으로 올린다. 기업·연도 필터가 사후라 후보 풀이 얇아서,
    # 정답 청크가 40위 밖에 있으면 뒤의 어떤 정렬도 소용이 없다.
    hits = search(question, k=120)
    trace.append("검색 120건")
    if corp:
        hits = [h for h in hits if h["corp_name"] == corp]
        trace.append(f"기업 필터({corp}) 후 {len(hits)}건")
    if year:
        hits = [h for h in hits if year_of(h) == year]
        trace.append(f"연도 필터({year}) 후 {len(hits)}건")

    hits, n_add = scope_backfill(hits, question, corp, year, item)
    if n_add:
        trace.append(f"별도/연결 근거 직행 보강 {n_add}건")
    hits, moved = scope_rerank(hits, question)
    if moved:
        trace.append("별도/연결 재정렬(외화표·연결자료 후순위)")

    hits, moved = prefer_financial(hits, item)
    if moved:
        trace.append("재무제표 섹션 우선 정렬")
    hits = hits[:k]
    if not hits:
        parts = []
        if corp:
            parts.append(f"기업 '{corp}'")
        if year:
            parts.append(f"{year}년")
        cond = ", ".join(parts) if parts else "해당 조건"
        return {"answer": f"{cond} 조건에 맞는 공시를 찾지 못했습니다. 기업명과 연도를 확인해 주세요.",
                "retrieved_context": "", "think_trace": " -> ".join(trace)}
    context, _ev, _ = build_context(hits)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"근거 자료:\n{context}질문: {question}"},
    ]
    text, note = call_hcx(messages, max_tokens=700)
    if text is None:
        return {"answer": None, "retrieved_context": context,
                "think_trace": " -> ".join(trace) + f" -> {note}"}
    trace.append("HCX 생성 완료")
    return {"answer": text, "retrieved_context": context,
            "think_trace": " -> ".join(trace) + note}

if __name__ == "__main__":
    r = answer_type1("삼성전자의 2025년 연결기준 매출액은 얼마인가?", corp="삼성전자", year=2025)
    print("답변:", r["answer"])
    print("추론 과정:", r["think_trace"])

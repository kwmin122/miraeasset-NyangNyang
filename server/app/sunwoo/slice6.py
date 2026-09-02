import re
from slice1 import s, year_of, SLIM, with_text
from slice3 import gather

ANNUAL_SECTIONS = ("주요 제품 및 서비스", "매출 및 수주")
ANNUAL_HINT = ("매출 구성", "매출구성", "비중", "구성비", "품목별", "제품별")
_NUM = re.compile(r"^\(?-?[\d,]+\)?$")
_PCT = re.compile(r"^-?[\d.]+\s*%$")
_HEADHDR = ("매출액", "비율", "비중", "금액", "구분", "품목", "사업부문",
            "매출유형", "사용용도와기능", "사업영역", "사진", "설명", "-")


def annual_wanted(question, item):
    """사업보고서 매출표 주입이 이 질문에 맞는지 코드가 먼저 판정한다.

    이 게이트가 없으면 유형6으로 함께 들어오는 자금조달 질문에도 매출 구성표가
    주입돼 수치 근거 자리를 무관한 표가 차지한다. 라우팅은 문항 번호가 아니라
    extract의 qtype으로 정해지므로 유형6 진입 문항은 T6-* 만이 아니다.
    """
    blob = (question or "") + " " + (item or "")
    return "사업보고서" in blob or any(h in blob for h in ANNUAL_HINT)


def _kind(ln):
    t = ln.strip()
    if not t:
        return None
    if _PCT.match(t):
        return "P"
    if _NUM.match(t) and any(c.isdigit() for c in t):
        return "N"
    return None


def _clean_label(parts):
    """행 라벨에서 표 머리글 잔여물(매출액/비중/제N기/2025년/빈칸)을 떼어낸다."""
    out = []
    for p in parts:
        q = re.sub(r"\s+", "", p)
        if q in _HEADHDR or set(q) <= {"-", "—"}:
            continue
        if re.fullmatch(r"제\d+\(?[전당]{0,2}\)?기(3분기|반기|분기)?", q):
            continue
        if re.fullmatch(r"\(?[전당]{1,2}기\)?", q):
            continue
        if re.fullmatch(r"20\d{2}년\(?(제\d+기)?\)?", q):
            continue
        out.append(p.strip())
    return " ".join(out)[:60].strip()


def current_period_table(text, max_rows=12):
    """평탄화된 DART 표에서 당기(첫 번째 열) 금액·비율만 코드가 직접 읽는다.

    사업보고서 표는 당기 -> 전기 -> 전전기 순이고 한 행이
    [금액, 비율, 금액, 비율, ...]로 줄바꿈 평탄화된다. 앞의 두 개가 당기다.
    HCX가 이 순서를 못 읽어 전기 값을 당기로 답하는 실패가 반복돼서
    코드가 직접 읽어 확정치로 넘긴다.
    """
    lines = [l.strip() for l in (text or "").split("\n")]
    blocks, label_buf, run = [], [], []
    for ln in lines:
        if _kind(ln):
            run.append((_kind(ln), ln.strip()))
            continue
        if run:
            blocks.append((list(label_buf), list(run)))
            run, label_buf = [], []
        if ln:
            label_buf.append(ln)
            if len(label_buf) > 6:
                label_buf.pop(0)
    if run:
        blocks.append((list(label_buf), list(run)))

    out = []
    for label, r in blocks:
        if len(r) < 4 or len(r) % 2:
            continue
        if not all(r[j][0] == ("N" if j % 2 == 0 else "P") for j in range(len(r))):
            continue
        name = _clean_label(label)
        if not name:
            continue
        out.append((name, r[0][1], r[1][1]))
        flat = re.sub(r"\s+", "", name)
        if flat.endswith(("합계", "총계")) or flat in ("계", "소계"):
            break
        if len(out) >= max_rows:
            break
    return out


def table_consistent(tbl, tol=1.0):
    """합계 행을 뺀 비중 합이 100%인지 본다. 파싱 실패를 코드가 스스로 잡는 장치다.

    파서는 셀이 '-'인 행을 숫자런 길이 미달로 조용히 버린다. 그런 표를
    '확정치'라고 단언해 주입하면 없던 오류를 우리가 만들어 넣는 셈이라,
    합이 안 맞으면 주입을 포기한다.
    """
    body = [r for r in tbl
            if not re.sub(r"\s+", "", r[0]).endswith(("합계", "총계"))
            and re.sub(r"\s+", "", r[0]) not in ("계", "소계")]
    if len(body) < 1:
        return False
    try:
        total = sum(float(r[2].rstrip("%")) for r in body)
    except ValueError:
        return False
    return abs(total - 100.0) <= tol


def annual_fact_head(m):
    """청크에서 당기 확정 수치 머리 블록을 만든다. 못 만들면 빈 문자열."""
    if (m.get("base_month") or 0) != 12:
        return ""
    tbl = current_period_table(m.get("text") or "")
    if len(tbl) < 2 or not table_consistent(tbl):
        return ""
    lines = ["[코드추출·확정치] %s %s년 사업보고서 당기(표의 첫 번째 숫자 열) 값"
             % (m.get("corp_name"), m.get("base_year"))]
    for name, amt, pct in tbl:
        lines.append("  %s = %s (비중 %s)" % (name, amt, pct))
    lines.append("  ※ 코드가 당기 열에서 직접 읽은 값이다. 반올림하지 말고 이 숫자와 "
                 "비중을 그대로 인용하라. 아래 원문 표의 두 번째·세 번째 숫자 열은 "
                 "전기·전전기이므로 당기 값으로 쓰지 마라. "
                 "항목별 비중만 쓰지 말고 각 항목의 금액과 합계(계) 금액도 함께 적어라.")
    return "\n".join(lines) + "\n---- 원문 ----\n"


def gather_annual(corp, year, question, item, k=1):
    """유형6 수치 근거 보강. 벡터 검색을 건너뛰고 사업보고서 매출표를 직행으로 집는다.

    base_year만 보면 같은 해 분기·반기보고서가 전부 통과하는데, 그 표는 섹션 경로도
    모양도 사업보고서와 똑같아 벡터 점수로 구분되지 않는다. base_month==12로
    구조적으로 잘라낸다.
    """
    if not annual_wanted(question, item):
        return []
    # 메타만으로 먼저 좁히고, 본문은 살아남은 후보에만 붙인다(경량 색인 사용).
    narrowed = [m for m in SLIM
                if m.get("corp_name") == corp and m.get("base_year") == year
                and (m.get("base_month") or 0) == 12
                and not s.superseded_by.get(m.get("rcept_no"))
                and any(x in str(m.get("section_path") or "") for x in ANNUAL_SECTIONS)]
    cand = []
    for m in narrowed:
        full = with_text(m)
        head = annual_fact_head(full)
        if not head:
            continue
        m2 = dict(full)
        m2["text"] = head + full["text"]
        cand.append(m2)
    cand.sort(key=lambda m: (0 if "주요 제품" in str(m.get("section_path")) else 1,
                             m.get("chunk_ix", 0)))
    seen, out = set(), []
    for m in cand:
        sig = tuple(x[1] for x in current_period_table(m["text"]))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(m)
        if len(out) >= k:
            break
    return out


BG_SECTIONS = ("경영진단", "사업의 개요", "매출 및 수주")
BG_NOISE = ("예측정보에 대한 주의사항", "기재하지 않습니다", "참조 바랍니다")

SECTION_RANK = (("경영진단", 0), ("사업의 개요", 1), ("매출 및 수주", 2))


def _section_rank(m):
    sp = str(m.get("section_path") or "")
    for key, rank in SECTION_RANK:
        if key in sp:
            return rank
    return 9

def gather_bg(corp, year, item, k=3):
    item = item or ""
    narrowed = [m for m in SLIM
                if m.get("corp_name") == corp and year_of(m) == year
                and any(x in str(m.get("section_path") or "") for x in BG_SECTIONS)]
    out = []
    for m in narrowed:
        full = with_text(m)
        if any(n in full["text"] for n in BG_NOISE):
            continue
        out.append(full)
    out.sort(key=lambda m: (_section_rank(m),
                            "사업보고서" not in str(m.get("report_nm")),
                            item not in m["text"],
                            -len(m["text"])))
    return out[:k]

def gather_year(corp, y, question, item):
    """한 연도의 수치 근거와 배경 근거. answer_type6과 플래너의 yeartab 도구가 같이 쓴다.

    반환 (수치 hits, 배경 bg, trace). 사업보고서 당기 표가 있으면 그걸 앞에 두고
    벡터검색은 그 외 분만 채운다 — answer_type6이 하던 그대로다.
    """
    trace = []
    annual = gather_annual(corp, y, question, item)
    hits = gather(corp, y, item)
    if annual:
        keys = {(m.get("rcept_no"), m.get("section_id")) for m in annual}
        hits = [h for h in hits
                if (h.get("rcept_no"), h.get("section_id")) not in keys
                and (h.get("base_month") or 12) == 12]
        hits = annual + hits[:max(0, 3 - len(annual))]
        trace.append(f"{y}년 사업보고서 당기 표 코드 추출 {len(annual)}건")
    bg = gather_bg(corp, y, item)
    trace.append(f"{y}년 수치 {len(hits)}건 / 배경 {len(bg)}건")
    return hits, bg, trace


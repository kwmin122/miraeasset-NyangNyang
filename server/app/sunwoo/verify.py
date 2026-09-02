import re

# 답변과 근거 양쪽에서 '날짜처럼 생긴 것'만 뽑아 같은 형식으로 정규화한 뒤 대조한다.
#
# 예전 구현은 근거의 숫자를 전부 이어붙여 하나의 죽으로 만들고 그 안에 YYYYMMDD 가
# 들어 있는지 봤다. 양쪽으로 틀렸다(TODO M-7 "양방향 오류").
#   오탐: 근거가 "2026년 1월 17일" 이면 죽은 2026117 이 되는데 키는 20260117 이라 안 맞는다
#         -> 근거에 멀쩡히 있는 날짜를 환각이라고 짖는다 (실측 T4-O-018 에서 3건)
#   미탐: 무관한 숫자들이 이어져 우연히 20250115 를 만들면 진짜 환각이 통과한다
_D_SEP = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_D_RAW = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
# 접수번호(14자리)의 앞 8자리도 그 문서의 접수일이다. 근거 쪽에서만 인정한다.
_D_RCEPT = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})\d{6}(?!\d)")


def _norm_dates(text, with_rcept=False):
    """문자열에서 날짜를 뽑아 YYYYMMDD 집합으로 만든다. 달·일 범위를 벗어나면 버린다."""
    out = set()
    pats = [_D_SEP, _D_RAW] + ([_D_RCEPT] if with_rcept else [])
    for p in pats:
        for y, m, d in p.findall(text or ""):
            mi, di = int(m), int(d)
            if 1 <= mi <= 12 and 1 <= di <= 31:
                out.add(f"{y}{mi:02d}{di:02d}")
    return out


def check_dates(answer, context):
    """답변에 있는데 근거에는 없는 날짜를 돌려준다.

    근거 쪽은 접수번호에 박힌 접수일까지 인정한다 — 답변이 접수일을 밝히는 건
    정상인데 근거 머리표에 접수번호만 있는 경우가 있다.
    """
    if not answer:
        return []
    ctx = _norm_dates(context, with_rcept=True)
    return [d for d in sorted(_norm_dates(answer)) if d not in ctx]

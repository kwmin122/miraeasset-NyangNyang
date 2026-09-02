from slice1 import s
from slice4 import rcept_index

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


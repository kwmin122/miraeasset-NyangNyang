import re

def check_dates(answer, context):
    if not answer:
        return []
    ctx = re.sub(r"[^0-9]", "", context)
    found = re.findall(r"(20\d{2})[.\-년\s]+(\d{1,2})[.\-월\s]+(\d{1,2})", answer)
    found += re.findall(r"\b(20\d{2})(\d{2})(\d{2})\b", answer)
    bad = []
    for y, m, d in found:
        key = f"{y}{int(m):02d}{int(d):02d}"
        if key not in ctx and key not in bad:
            bad.append(key)
    return bad

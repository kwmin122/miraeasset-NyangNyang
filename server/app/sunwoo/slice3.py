from slice1 import search, year_of, prefer_financial

def gather(corp, year, item, k=3):
    q = " ".join(str(x) for x in (corp, f"{year}년" if year else None, item) if x)
    hits = search(q, k=40)
    hits = [h for h in hits if h["corp_name"] == corp]
    if year:
        hits = [h for h in hits if year_of(h) == year]
    hits, _ = prefer_financial(hits, item)
    return hits[:k]

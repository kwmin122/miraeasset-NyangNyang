# -*- coding: utf-8 -*-
"""평가서버(민경욱) → 팀 공유 패치.

search_lib.py도 index.faiss도 건드리지 않는다. 재청킹이 없으므로 chunk_id가 그대로 유효하다.
질의 시점 필터로 3가지를 고친다.

  ① doc_role == "viewer" 청크 288개 제거
     DART PDF 뷰어 HTML의 JavaScript/CSS가 본문으로 임베딩돼 있다. 검색되면 인용 가능한 쓰레기 근거가 된다.

  ② 최신본 판정을 correction_map 전체 계보로 수행
     원본 A가 정정본 B, C에게 대체될 때 correction_map은 superseded_by[A]=[B,C]만 기록하고
     superseded_by[B]는 비어 있다. 그래서 B와 C가 둘 다 "최신"으로 남는다(정기공시 897개 기간키 중 20개).
     같은 가족(A,B,C) 안에서 rcept_dt 최댓값 하나만 최신으로 인정한다.
     search_lib의 latest_only는 정정본이 같은 top-k 창에 우연히 잡힐 때만 구본을 지웠다 — 여기선 창과 무관.

  ③ 진짜 최신본이 PDF 전용(viewer)일 때, 구본을 지우지 않고 stale_reason으로 표시
     ①과 ②를 그냥 같이 적용하면 한화오션 2024Q1은 인덱스에서 통째로 사라진다.
     읽을 수 있는 최신 XML을 살려두되 "이 답변 이후 정정본이 있으나 PDF라 읽지 못했다"를 신호로 넘긴다.

추가: evidence_key = chunk_id + 인용문 sha1 앞 12자.
     재색인이 일어나면 chunk_id가 밀리는데, 해시가 함께 있으면 조용한 오매칭 대신 불일치로 드러난다.
"""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

VIEWER_ROLE = "viewer"


def evidence_key(chunk_id: str, quote: str) -> str:
    """골드셋과 에이전트 인용을 정확일치로 join하는 키."""
    return f"{chunk_id}#{hashlib.sha1(quote.encode('utf-8')).hexdigest()[:12]}"


class Resolver:
    """메타데이터만으로 최신본을 판정한다. FAISS도 임베딩 모델도 필요 없다."""

    def __init__(self, out_dir):
        out = Path(out_dir)
        sup = json.loads((out / "correction_map.json").read_text(encoding="utf-8"))["superseded_by"]
        dt, roles = {}, defaultdict(set)
        with open(out / "chunk_meta.jsonl", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                dt[d["rcept_no"]] = d["rcept_dt"]
                roles[d["rcept_no"]].add(d.get("doc_role"))
        self.roles = roles
        self.stale = set()
        self.pdf_only = {}  # 살려둔 구본 rcept_no -> 읽을 수 없는 진짜 최신본 rcept_no

        for orig, corrs in sup.items():
            family = [r for r in [orig, *corrs] if r in dt]
            if len(family) < 2:
                continue
            latest = max(family, key=lambda r: dt[r])
            readable = [r for r in family if roles[r] != {VIEWER_ROLE}]
            if roles[latest] == {VIEWER_ROLE} and readable:
                keep = max(readable, key=lambda r: dt[r])
                self.pdf_only[keep] = latest
            else:
                keep = latest
            self.stale.update(r for r in family if r != keep)

    def annotate(self, hits, include_stale=False):
        """include_stale=True: 구본도 반환 (TEAM_GUIDE §4의 '변경 이력' 질의용). 뷰어 JS는 언제나 제외."""
        out = []
        for h in hits:
            if h.get("doc_role") == VIEWER_ROLE:
                continue
            if h["rcept_no"] in self.stale:
                if not include_stale:
                    continue
                h["is_stale_version"] = True
            unreadable = self.pdf_only.get(h["rcept_no"])
            h["stale_reason"] = "LATEST_IS_PDF_ONLY" if unreadable else None
            h["unreadable_latest_rcept_no"] = unreadable
            h["evidence_key"] = evidence_key(h["chunk_id"], h["text"])
            out.append(h)
        return out


class PatchedSearcher:
    """search_lib.Searcher를 감싼다. 필터가 결과를 지우므로 넉넉히 과다인출한 뒤 자른다."""

    def __init__(self, out_dir=None, device=None):
        from search_lib import ROOT, Searcher

        if device is None:
            import torch

            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        self.device = device
        self._s = Searcher(out_dir=out_dir, device=device)
        self.resolver = Resolver(Path(out_dir) if out_dir else ROOT / "out")

    def search(self, query: str, k: int = 5, overfetch: int = 8):
        raw = self._s.search(query, k=k * overfetch, latest_only=False)
        return self.resolver.annotate(raw)[:k]

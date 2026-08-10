# -*- coding: utf-8 -*-
"""서버 ↔ 선우 에이전트 계약 어댑터 (docs/SPEC.md §2).

AGENT_MODE=sunwoo 일 때 app/agents.py가 이 파일의 SunwooAgent를 가져다 쓴다.
계약은 answer(question) -> {answer, retrieved_context, think_trace} 세 키뿐이고
question_id 에코와 HTTP 처리는 서버 계층이 맡는다.

실제 파이프라인은 app/sunwoo/ 아래에 있다. 그 폴더의 모듈들은 서로를 평평하게
import하므로(from hcx import ...) 폴더 자체를 sys.path에 넣어 준다.
서버 쪽 app.hcx / app.main 은 패키지 경로로 import되므로 이름이 겹쳐도 섞이지 않는다.
"""
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent / "sunwoo"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from pipeline import answer_question  # noqa: E402  (sys.path 설정 뒤에 와야 한다)


class SunwooAgent:
    def answer(self, question: str) -> dict:
        r = answer_question(question)
        return {
            "answer": r.get("answer") or "답변을 생성하지 못했습니다.",
            "retrieved_context": r.get("retrieved_context") or "",
            "think_trace": r.get("think_trace") or "",
        }

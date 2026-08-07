# -*- coding: utf-8 -*-
"""에이전트 구현체들. 계약: answer(question) -> AgentResult (docs/SPEC.md §2)."""
from typing import TypedDict

from . import hcx, search
from .config import settings


class AgentResult(TypedDict):
    answer: str
    retrieved_context: str
    think_trace: str


SYSTEM_PROMPT = """당신은 DART 공시자료만을 근거로 답하는 공시 분석 어시스턴트입니다. 규칙:
1. 반드시 아래 제공된 [근거]의 내용만 사용해 답하십시오. 근거에 없는 내용은 "공시에서 확인되지 않음"이라고 명시하고 추측하지 마십시오.
2. 수치는 원문 그대로 사용하십시오 (반올림·단위 변환 금지, 단위 명시).
3. 모든 답변 끝에 근거 공시를 "보고서명(접수일)" 형식으로 표기하십시오.
4. URL을 생성하지 마십시오.
5. 미래 예측·투자 의견·매수/매도 추천은 하지 마십시오. 요청받으면 정중히 거절하십시오.
6. 질문자는 일반인입니다. 전문용어는 풀어서 설명하십시오.
7. 개인정보(주민번호·연락처 등)는 답변에 포함하지 마십시오."""


class MockAgent:
    """파이프 검증용 — 검색·LLM 없이 스키마만 채운다."""

    def answer(self, question: str) -> AgentResult:
        return AgentResult(
            answer=f"[MOCK] '{question}'에 대한 답변 자리입니다. 근거: 사업보고서(20260310)",
            retrieved_context="[근거 1] 사업보고서 (접수일 20260310) | 삼성전자 | 접수번호 20260310000000\n(mock context)",
            think_trace="[MOCK] 1) 질의 수신 2) 검색 생략 3) 답변 생성 생략",
        )


class BaselineAgent:
    """검색 top-k + HCX-005 합성. HCX 키 없으면 추출형 폴백 (파이프 검증용).
    선우 모듈이 오기 전까지의 기준선이자, 최종 폴백."""

    def answer(self, question: str) -> AgentResult:
        trace = []
        hits = search.search(question)
        trace.append(f"1) FAISS 검색 k={settings.search_k} → {len(hits)}건 "
                     f"({', '.join(h.get('report_nm', '?') + '/' + h.get('rcept_dt', '?') for h in hits[:3])} …)")
        context = search.format_context(hits)

        if not hits:
            return AgentResult(
                answer="제공된 공시 데이터에서 관련 근거를 찾지 못했습니다. 공시에서 확인되지 않음.",
                retrieved_context="",
                think_trace="\n".join(trace + ["2) 검색 결과 0건 → 한계 고지"]),
            )

        try:
            content = hcx.chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"[근거]\n{context}\n\n[질문]\n{question}"},
            ])
            trace.append(f"2) {settings.hcx_model} 합성 완료")
        except hcx.HCXError as e:
            trace.append(f"2) HCX 미사용({e}) → 추출형 폴백")
            top = hits[0]
            content = (
                f"(추출형 폴백 — LLM 미연동 상태) 가장 관련성 높은 공시 발췌:\n"
                f"{(top.get('text') or '')[:800]}\n\n"
                f"근거: {top.get('report_nm')}({top.get('rcept_dt')})"
            )
        return AgentResult(answer=content, retrieved_context=context, think_trace="\n".join(trace))


def get_agent(mode: str | None = None):
    mode = mode or settings.agent_mode
    if mode == "mock":
        return MockAgent()
    if mode == "baseline":
        return BaselineAgent()
    if mode == "sunwoo":
        # 선우 모듈이 server/app/sunwoo_agent.py 로 들어오면 여기서 연결 (SPEC §2 시그니처)
        from .sunwoo_agent import SunwooAgent  # noqa: PLC0415

        return SunwooAgent()
    raise ValueError(f"알 수 없는 AGENT_MODE: {mode}")

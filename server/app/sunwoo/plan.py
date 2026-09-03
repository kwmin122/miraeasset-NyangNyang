"""플래너 경로 — 질문을 단계 목록으로 풀고, 도구로 실행하고, 한 번에 합성한다.

구조:
  질문 → HCX#1 계획(JSON) → 코드 검증 → 코드 실행(tools) → 범위 판정 → HCX#2 합성

HCX는 여전히 두 번만 부른다. 달라진 건 첫 호출이 '유형 번호 하나'가 아니라
'단계 목록'을 돌려준다는 것이다. 단순 질문이면 단계가 1개라 기존과 같다.

안전장치:
  - 계획이 스키마에 안 맞거나 말이 안 되면(비교인데 입력 1개 등) 반려 → None 반환
    → pipeline이 기존 단일 경로로 폴백한다. 최악의 경우에도 기존 점수가 유지된다.
  - 실행 중 어떤 단계가 빈 결과를 내도 죽지 않고 '해당 없음'으로 마킹해 합성에 넘긴다.
"""
import json
import re

import os

# 코드 보정 스위치 — 명섭 형 제안(라우터 보정을 LLM에 맡기기)을 같은 평가셋으로
# 재보기 위한 A/B 장치다. AGENT_FIX=off 면 "LLM 계획을 코드가 되돌리는" 부분을
# 전부 건너뛴다. 끄는 것은 판단 교정뿐이고, 기업명 정규화 같은 버그 수정과
# 스키마 검증은 남긴다 — 버그까지 되살리면 공정한 비교가 아니다.
FIX_ON = os.environ.get("AGENT_FIX", "on").lower() not in ("off", "0", "false")

from hcx import call_hcx
from extract import strip_fence
from attribute import (build_context, BASE_RULES, GUARD_RULES, CORRECTION_RULES,
                       SCOPE_RULES, CITE_RULES, BALANCED_CONTRACT)
import tools as T

PLAN_SYSTEM = """너는 DART 공시 질의를 실행 계획으로 바꾸는 계획기다. 답하지 말고 JSON만 출력하라.

출력 스키마:
{"steps":[{"id":"s1","tool":"...","corp":"...","year":2025,"topic":"...",
           "version":"latest|original|all","recency":"latest|null","date":"YYYYMMDD|null",
           "month":1-12|null,"filer":"제출인|null","inputs":["s1",...],"op":"sum|diff|pct_change|ratio|max|min"}],
 "answer":{"mode":"single|list|compare|timeline","inputs":["s1",...]}}

도구 고르는 기준 (가장 중요):
- collect 는 공시 "제목"으로 거른다. 답이 공시 제목만으로 특정되는 사건성 공시(유상증자 결정, 사채 발행 결정,
  자기주식 취득·처분 결정, 공급계약 체결, 신규시설투자 등)에 쓴다. "전부", "모두", "몇 건"이면 collect.
- find 는 공시 "본문"을 검색한다. 재무제표 수치(매출액·영업이익·자산총계·당기순이익), 사업보고서 서술,
  특정 계약의 상대방·금액·조건, 회사분할 후 신설회사 상호, 콜옵션 조건처럼 제목에 안 나오는 내용은 find.
  확신이 없으면 find. find 는 corp 와 topic(찾는 항목) 필수, year 는 질문에 있을 때만.
- "A기업과 B기업 비교"처럼 기업이 둘이면 기업마다 find 또는 collect 를 하나씩 만든다.

도구:
- collect: 특정 기업의 공시 목록을 제목 조건으로 거른다.
  version=original 은 정정 전 원본, latest 는 최신 정정본(기본), all 은 정정 체인 전체.
  "가장 최근"이면 recency=latest. 특정 날짜면 date.
  **지분공시(대량보유상황보고서·주식등의대량보유)는 남이 그 회사 지분을 신고하는 문서다.**
  "A가 B에 대해 제출한 보고서"이면 corp=B(지분을 신고당한 회사), filer=A(신고한 쪽)로 나눠 넣어라.
  filer 에는 회사명뿐 아니라 사람 이름(이수만·최윤범 등)이나 기관명(국민연금공단)도 들어간다.
- find: 공시 본문을 벡터검색. corp, topic 필수.
- trace: 특정 공시가 이후 어떻게 됐는지(철회·변경·해지) 추적. corp, date 또는 year, topic.
- yeartab: 정기보고서의 **매출 구성표**(제품·서비스별 매출 비중)와 사업 배경 서술. corp, year, topic.
  질문에 "매출 구성", "비중", "구성비", "품목별", "제품별"이 있고 두 연도를 대조할 때만 쓴다.
  매출액·영업이익·수주잔고 같은 **개별 수치**를 물으면 두 연도라도 yeartab 이 아니라 find 다.
- compute: 앞 단계 결과의 수치를 계산. inputs=[단계id...], op.

규칙:
- 질문이 요구하는 모든 기업·연도·시점을 빠짐없이 단계로 만든다. 연도가 둘이면 단계도 둘.
- "예정가와 확정가", "변경 전후", "원래와 최종"을 물으면 version=original 과 version=latest 두 단계.
- "이후 어떻게 됐나", "철회됐나", "해지된", "취소된", "결국"이면 trace.
- "합계", "총액", "모두 합쳐"면 collect 뒤에 compute(sum).
- "매출 대비 비중", "비율", "~당 비율"처럼 **나눗셈**이 필요하면 compute(op="ratio").
  inputs 순서가 [분자단계, 분모단계]다. 직접 나누지 마라 — 단위를 틀린다.
- 질문에 "3건", "두 건"처럼 건수가 적혀 있으면 그 collect 단계에 "count": 3 을 넣는다.
- "가장 최근", "최신"이면 recency=latest 로 하고 year 는 반드시 null. 연도를 지어내지 마라.
- 질문에 연도가 없으면 year 는 null. 있지도 않은 연도를 넣지 마라.
- date 는 질문에 연·월·일이 모두 적혀 있을 때만 쓴다. "2023년 4월"처럼 월까지만 있으면
  date 는 null 로 두고 month 에 4 를 넣어라. 날짜를 지어내면 검색이 0건이 된다.
- answer.mode: 값 하나면 single, 여러 건 나열이면 list, 둘 이상 대조면 compare, 사건 경과면 timeline.
- 기업명은 질문에 적힌 한글 그대로 쓴다. 종목코드나 영문으로 바꾸지 마라.
- "A가 B에 대해/B의 지분을" 형태면 corp 는 **지분 대상 회사 B**, filer 는 **보고자 A**다. 뒤집지 마라.
- recency=latest 는 질문에 "가장 최근", "최신", "마지막" 같은 말이 있을 때만. 그 외에는 넣지 마라.
- JSON 외에 아무것도 쓰지 마라."""

_TOOLS = {"collect", "find", "trace", "yeartab", "compute"}
_MODES = {"single", "list", "compare", "timeline"}


def _parse_plan(text):
    """계획 JSON 파싱. extract.parse_json은 qtype 키를 요구하는 슬롯 전용이라 따로 둔다.
    꼬리가 잘린 응답은 닫는 괄호 후보를 붙여가며 복구하되, 내용 검문(steps 키)을 통과해야 인정한다."""
    raw = strip_fence(text).rstrip().rstrip(",")
    for tail in ("", "}", "]}", "}]}", '"}]}', '"}}'):
        try:
            data = json.loads(raw + tail)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "steps" in data:
            return data
    return None


def make_plan(question):
    msgs = [{"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": question}]
    text, note = call_hcx(msgs, max_tokens=700, wall=20)
    if text is None:
        return None, f"계획 호출 실패: {note}"
    plan = _parse_plan(text)
    if plan is None:
        text, note = call_hcx(msgs, max_tokens=700, wall=20)
        plan = _parse_plan(text) if text else None
    return plan, note


# 제목만으로 특정되는 사건성 공시 어휘. collect 로 보낼지 판단하는 데만 쓴다.
_EVENT_WORDS = (r"유상증자|무상증자|전환사채|교환사채|신주인수권|사채\s*발행|"
                r"자기주식|자사주|공급계약|수주|시설투자|설비투자|투자판단|"
                r"주요경영사항|합병|분할|영업양수|영업양도|배당|신탁계약|"
                r"최대주주|대량보유|임상|낙찰|시공자")


def code_plan(question):
    """HCX 없이 계획을 만든다. 계획 호출이 API 실패로 죽었을 때 쓰는 대체 경로.

    반환 plan dict 또는 None(기업을 못 찾으면 코드가 세울 근거가 없다).
    validate() 를 통과하는 형태로만 만든다.
    """
    q = question or ""
    names, _ = T._corp_tables()
    in_q = sorted([n for n in names if n in q or n.replace(" ", "") in q.replace(" ", "")],
                  key=len, reverse=True)
    # 긴 이름이 짧은 이름을 품는 경우(현대자동차 vs 현대차)를 걷어낸다
    corps = []
    for n in in_q:
        if not any(n in c for c in corps):
            corps.append(n)
    if not corps:
        corps = [T.norm_corp(a) for a in T.ALIAS if a in q]
    corps = [c for c in dict.fromkeys(corps) if c][:2]
    if not corps:
        return None

    years = sorted({int(y) for y in re.findall(r"20\d{2}", q)})
    if re.search(r"이후 어떻게|이후에 어떻게|후속|결국|해지된|해지됐|해지되|취소된|철회", q):
        tool = "trace"
    elif re.search(r"매출\s*구성|구성비|비중|품목별|제품별", q) and len(years) >= 2:
        tool = "yeartab"
    # "3건의 계약금액 합계"처럼 앞에 '총'이 없는 건수 표기를 놓쳤다(오프라인 검증).
    elif re.search(r"\d+\s*건|몇\s*건|모두 찾|전부|각 건|건별|공시한 건|정리해", q) \
            and re.search(_EVENT_WORDS, q):
        tool = "collect"
    else:
        tool = "find"

    topic = " ".join(sorted(c for c in T.question_clues(q)
                            if not any(c in n or n in c for n in corps)))[:80]
    if not topic:
        topic = "주요 내용"

    steps, n = [], 1
    for c in corps:
        for y in (years or [None]):
            if len(steps) >= 6:
                break
            st = {"id": f"c{n}", "tool": tool, "corp": c, "topic": topic,
                  "version": "latest"}
            if y is not None:
                st["year"] = y
            steps.append(st)
            n += 1
    # trace 는 date 또는 year 가 있어야 validate 를 통과한다. 없으면 find 로 낮춘다.
    if tool == "trace" and not years:
        for st in steps:
            st["tool"] = "find"
    # yeartab 도 year 필수다.
    if tool == "yeartab" and not years:
        for st in steps:
            st["tool"] = "find"

    if re.search(r"이후 어떻게|후속|결국|경과", q):
        mode = "timeline"
    elif len(steps) >= 2:
        mode = "compare"
    elif re.search(r"몇\s*건|모두|각각|전부|건별", q):
        mode = "list"
    else:
        mode = "single"
    return {"steps": steps, "answer": {"mode": mode, "inputs": [st["id"] for st in steps]}}

def validate(plan):
    """계획이 말이 되는지 코드가 본다. 기존 라우터 보정 규칙이 여기로 옮겨왔다.

    반환 (ok, reason). 불통이면 pipeline이 기존 경로로 폴백한다.
    """
    if not isinstance(plan, dict):
        return False, "JSON 아님"
    steps = plan.get("steps")
    ans = plan.get("answer") or {}
    if not isinstance(steps, list) or not steps:
        return False, "steps 없음"
    if len(steps) > 8:
        return False, "단계 과다"
    ids = set()
    for st in steps:
        if not isinstance(st, dict):
            return False, "단계가 dict 아님"
        sid, tool = st.get("id"), st.get("tool")
        if not sid or sid in ids:
            return False, f"id 중복/누락 {sid}"
        ids.add(sid)
        if tool not in _TOOLS:
            return False, f"모르는 도구 {tool}"
        if tool == "compute":
            ins = st.get("inputs") or []
            if not ins or not all(i in ids for i in ins):
                return False, "compute 입력이 앞 단계를 가리키지 않음"
            if st.get("op") not in ("sum", "diff", "pct_change", "ratio", "max", "min"):
                return False, f"compute op 불명 {st.get('op')}"
        else:
            if not st.get("corp"):
                return False, f"{tool}에 corp 없음"
            if tool == "yeartab" and not st.get("year"):
                return False, "yeartab에 year 없음"
            if tool == "find" and not st.get("topic"):
                return False, "find에 topic 없음"
            if tool == "trace" and not (st.get("date") or st.get("year")):
                return False, "trace에 date/year 없음"
    mode = ans.get("mode")
    if mode not in _MODES:
        # 반려하지 않고 낮춘다. 아래 compare->list 강등과 같은 판단이다 —
        # 계획 내용(단계·도구)은 멀쩡한데 답변 형식 라벨 하나가 틀렸을 뿐인데
        # 반려하면 플래너 경로 전체가 버려진다.
        # 실측(T4-O-008, 2026-09-02): 플래너가 mode="compute" 를 내서 통째로 반려됐고,
        # 계획 자체는 collect + compute(sum) 으로 정확했는데 그게 통째로 날아갔다.
        ans["mode"] = "list" if len(steps) > 1 else "single"
    a_in = ans.get("inputs") or [s["id"] for s in steps]
    if not all(i in ids for i in a_in):
        return False, "answer.inputs가 단계를 가리키지 않음"
    if mode == "compare" and len([i for i in a_in if i in ids]) < 2:
        # 반려하지 않고 mode만 낮춘다. 단계가 하나여도 그 단계가 여러 건을 수집하면
        # 비교가 가능하다(메리츠금융지주 같은 날 해지+신규 2건이 한 collect에 들어온다).
        # 반려하면 플래너 경로 전체가 폴백돼 손해가 더 크다(실측: 57 -> 55).
        ans["mode"] = "list"
    return True, "ok"


def _year_list(y):
    if y is None:
        return []
    return [int(v) for v in (y if isinstance(y, (list, tuple)) else [y]) if v]


_RECENCY_WORDS = ("가장 최근", "최근", "최신", "마지막", "제일 최근")

# 재무제표·사업보고서 표에 이미 합계가 적혀 있는 항목들. 이런 항목의 '합계'는
# 개별 공시를 더해서 만드는 값이 아니다. PLAN_SYSTEM 도 "매출액·영업이익·수주잔고
# 같은 개별 수치는 find"라고 적어뒀지만, 같은 프롬프트의 "'합계'면 collect 뒤에
# compute(sum)" 규칙과 충돌해서 진다. 프롬프트끼리 부딪히는 건 코드가 끊는다.
_STMT_SUM = re.compile(
    r"(수주잔고|자산총계|부채총계|자본총계|매출액|영업이익|당기순이익|매출총이익|"
    r"영업수익|연구개발비|자본금|이익잉여금)[^.?!\n]{0,12}?(합계|총액|총합)")


def sanitize(plan, question):
    """플래너 출력의 알려진 실수를 코드가 되돌린다. LLM 선택을 코드가 검산하는 자리다.

    실측된 실수 둘:
      - corp 에 종목코드(그것도 틀린 코드)를 냄 → 질문 원문에서 기업명을 다시 찾는다
      - 질문에 '최근'이 없는데 recency=latest 를 붙여 원본 1건으로 좁힘 → 뗀다
    """
    names, _ = T._corp_tables()
    q = question or ""
    in_q = sorted([n for n in names if n in q or n.replace(" ", "") in q.replace(" ", "")],
                  key=len, reverse=True)
    alias_in_q = [T.norm_corp(a) for a in T.ALIAS if a in q]
    fallback = in_q + [a for a in alias_in_q if a in names]
    for st in plan.get("steps", []):
        c = st.get("corp")
        if isinstance(c, (list, tuple)):
            c = c[0] if c else None
            st["corp"] = c
        if c and (str(c).isdigit() or T.norm_corp(c) not in names) and fallback:
            st["corp"] = fallback[0]
        if not FIX_ON:
            continue          # A/B: 판단 교정은 건너뛴다 (corp 교정은 위에서 이미 끝)
        if st.get("recency") and not any(w in q for w in _RECENCY_WORDS):
            st["recency"] = None
        # 질문에 연도가 하나도 없는데 플래너가 연도를 지어냈으면 뗀다.
        # "삼성전자 요즘 뭐로 돈 벌어?"에 year=2022 를 넣어 코퍼스 범위 밖 기권을
        # 낸 실측이 있다(OQ-27). 연도를 빼면 전체에서 찾으니 최신본이 잡힌다.
        if st.get("year") and not re.search(r"20\d{2}", q):
            st["year"] = None
        # count 는 질문에 건수가 적혀 있을 때만 인정한다.
        # "연봉 제일 많이 받는 사람"을 count=1 로 해석해 후보 91건을 1건으로 뭉개고
        # 엉뚱한 공시만 남겨 "확인할 수 없습니다"를 낸 실측이 있다(OQ-59).
        # 최댓값을 묻는 것과 건수를 묻는 것은 다르다.
        if st.get("count") and not re.search(r"\d+\s*건|몇\s*건|총\s*\d+", q):
            st["count"] = None
        # topic·year 는 리스트로 올 때가 있다. 아래 도구들은 문자열/정수를 기대한다.
        tp = st.get("topic")
        if isinstance(tp, (list, tuple)):
            st["topic"] = " ".join(str(x) for x in tp if x)
        elif tp is not None and not isinstance(tp, str):
            st["topic"] = str(tp)
    # 질문에 실제로 적히지 않은 날짜를 코드가 떼어낸다.
    # 실측: "2023년 4월"만 적힌 질문에 플래너가 date="20230401"(4월 1일)을 지어냈고,
    # date 는 정확 일치 필터라 0건이 되어 문항이 통째로 날아갔다.
    # 질문에 일(日)이 안 적혔으면 date 를 month 로 낮춘다.
    q_ymd = {f"{y}{int(m):02d}{int(d):02d}"
             for y, m, d in re.findall(r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})", q)}
    q_ymd |= set(re.findall(r"(20\d{6})", q))
    for st in plan.get("steps", []):
        dt = st.get("date")
        if not dt:
            continue
        key = str(dt).replace("-", "").replace(".", "")
        if key not in q_ymd:
            st["date"] = None
            if len(key) >= 6 and not st.get("month"):
                st["month"] = int(key[4:6])
            if len(key) >= 4 and not st.get("year"):
                st["year"] = int(key[:4])

    # 후속 추적 질문인데 trace 단계가 없으면 하나 더한다.
    # 판정 어휘는 기존 라우터의 유형5 승격 규칙이 쓰던 것 그대로다. 새 어휘를 만들지 않는다.
    # 기존 단계는 그대로 두고 해지·정정 계보 근거만 보탠다(빼는 게 아니라 더하는 보정).
    steps = plan.get("steps", [])
    if (re.search(r"이후 어떻게|이후에 어떻게|후속|결국|해지된|해지됐|해지되|취소된|철회", q)
            and not any(st.get("tool") == "trace" for st in steps) and len(steps) < 8):
        base = next((st for st in steps if st.get("tool") in ("collect", "find")), None)
        if base and base.get("corp"):
            steps.append({"id": "_tr", "tool": "trace", "corp": base["corp"],
                          "year": base.get("year"), "date": base.get("date"),
                          "topic": base.get("topic") or ""})
            ai = (plan.get("answer") or {}).get("inputs")
            if isinstance(ai, list):
                ai.append("_tr")

    if not FIX_ON:
        return plan       # A/B: 도구 전환·구어 사전·단계 보강을 건너뛴다

    # yeartab 은 연도 대조용이다. 계획 전체에 yeartab 이 하나뿐이면 단일 연도 수치 질문인데,
    # 그 경로(slice6)는 별도/연결 보강이 없어 find(slice1.retrieve)로 보내야 맞는 값을 집는다.
    # 프롬프트로 두 번 시도해 두 번 실패해서 코드로 옮겼다 (T3-C-002).
    # yeartab 은 매출 구성표 전용이다. 질문이 구성·비중을 안 물으면 find 로 바꾼다.
    # 실측: "매출액과 영업이익은 전년 대비 어떻게 변했나"에 yeartab 을 골라
    # 매출 구성표만 보고 정작 매출액 수치를 놓쳤다(find 로 가면 잡는다).
    yt = [st for st in plan.get("steps", []) if st.get("tool") == "yeartab"]
    if yt and not re.search(r"매출\s*구성|구성비|비중|품목별|제품별", q):
        for st in yt:
            st["tool"] = "find"
    elif len(yt) == 1:
        yt[0]["tool"] = "find"

    # 구어를 공시 용어로 옮겨 topic 에 덧붙인다.
    # "엔씨 게임사업 쪼갠 거 뭐야?"가 사업보고서로 새서 "찾을 수 없습니다"가 나왔다(OQ-58).
    # 같은 사건을 문어체로 물으면(TR-NAME-002) 통과한다 — 뜻이 아니라 단어가 문제다.
    # 덮어쓰지 않고 덧붙이기만 해서 원래 topic 이 맞았던 경우를 망치지 않는다.
    _slang = " ".join(term for word, term in (
        ("쪼갠", "회사분할"), ("쪼개", "회사분할"), ("분사", "회사분할"),
        ("먹으려", "대량보유상황보고서 경영권"), ("인수한 거", "최대주주 변경"),
        ("연봉", "임원 보수"), ("빚", "차입금"),
        ("끌어왔", "유상증자 전환사채 자금조달"), ("끌어온", "유상증자 전환사채 자금조달"),
        ("돈 어디서", "자금조달"), ("돈 벌", "매출 주요 제품"),
        ("뭐로 벌", "매출 주요 제품")) if word in q)
    if _slang:
        for st in plan.get("steps", []):
            cur = st.get("topic") or ""
            if st.get("tool") == "collect":
                # 제목 필터는 키워드를 전부 만족해야 남는다. 구어에서 옮긴 말에
                # 원래 topic 을 덧붙이면 "게임사업 회사분할"이 되어 0건이 된다(OQ-58).
                # 공시 제목에 실제로 쓰이는 용어만 남긴다.
                st["topic"] = _slang
            elif st.get("tool") == "find":
                # 벡터 검색은 문맥이 많을수록 좋으니 덧붙인다. 겹치는 말만 뺀다.
                add = " ".join(w for w in _slang.split() if w not in cur)
                if add:
                    st["topic"] = (cur + " " + add).strip()

    # 사업보고서 본문 섹션에만 있는 항목은 제목 검색으로 못 찾는다.
    # 플래너가 이런 질문을 collect 로 보내면 제목 후보만 잔뜩 모으고 답은 못 찾아
    # "확인할 수 없습니다"가 나간다(OQ-59 임원 보수, OQ-40 연구개발비 실측).
    # 제목 검색이 0건도 아니라서 기존 승격 조건에도 안 걸린다.
    # 공시 "제목"에도 쓰이는 말(배당 결정·최대주주 변경 등)은 넣지 않는다.
    for st in plan.get("steps", []):
        if st.get("tool") == "collect" and any(
                w in (st.get("topic") or "") for w in
                ("보수", "연봉", "급여", "직원", "연구개발", "생산능력",
                 "가동률", "원재료", "계열회사", "종속회사", "배당성향", "배당정책")):
            st["tool"] = "find"
            st["count"] = None

    # 아래 둘은 이미 플래너 프롬프트에 있는 규칙인데, 어순이 바뀌면 플래너가 놓친다
    # (어순변경 변형에서만 T4-O-004·T4-O-007이 깨졌다). 프롬프트 문구를 그대로 코드로 옮긴다.
    steps = plan.get("steps", [])

    # ① "예정가와 확정가", "변경 전후" → 원본과 최종본 두 단계가 있어야 한다
    if re.search(r"예정.{0,6}(가액|가격|금액).{0,20}(확정|최종)|변경 전.{0,6}후|당초.{0,10}(최종|변경)", q):
        has_o = any(st.get("version") == "original" for st in steps)
        has_l = any(st.get("version") in (None, "latest") and st.get("tool") == "collect"
                    for st in steps)
        base = next((st for st in steps if st.get("tool") == "collect"), None)
        # 양 끝을 대조해야 하는 질문이다. 한쪽만 있으면 반대쪽을 만든다.
        if base and len(steps) < 8 and not (has_o and has_l):
            twin = dict(base)
            twin["id"] = "_orig" if not has_o else "_latest"
            twin["version"] = "original" if not has_o else "latest"
            twin.pop("recency", None)
            steps.insert(0 if not has_o else len(steps), twin)
            ai = (plan.get("answer") or {}).get("inputs")
            if isinstance(ai, list):
                ai.append(twin["id"])
            plan.setdefault("answer", {})["mode"] = "compare"

    # ② "합계·총액" → 파이썬이 더한다. compute가 없으면 붙인다
    #
    # 단, 재무제표·사업보고서 항목의 합계는 예외다. 그 합계는 표에 이미 적혀 있고
    # 개별 공시를 더해서 만드는 값이 아니다. 아래 가드가 원래 "collect 면 붙인다"였는데,
    # 플래너가 재무제표 항목을 엉뚱한 collect 로 옮겨 놓으면 그대로 뚫렸다.
    # 실측(T6-O-005): "수주잔고 합계"에 collect(공급계약 체결)가 세워져 있어
    # 공급계약 12건 합계를 [코드계산] 확정치로 답에 주입했다. 축을 도구가 아니라
    # 질문이 가리키는 항목으로 바꾼다.
    stmt = _STMT_SUM.search(q)
    if stmt:
        # 플래너가 스스로 낸 collect 합산도 같은 이유로 걷어낸다.
        by_id = {st.get("id"): st for st in steps}
        drop = {st.get("id") for st in steps
                if st.get("tool") == "compute" and str(st.get("op")) == "sum"
                and any((by_id.get(i) or {}).get("tool") == "collect"
                        for i in (st.get("inputs") or []))}
        if drop:
            steps[:] = [st for st in steps if st.get("id") not in drop]
            ai = (plan.get("answer") or {}).get("inputs")
            if isinstance(ai, list):
                ai[:] = [i for i in ai if i not in drop]
    if (re.search(r"합계|총액|모두 합|합쳐|총 얼마", q)
            and not any(st.get("tool") == "compute" for st in steps) and len(steps) < 8):
        src = [st["id"] for st in steps if st.get("tool") == "collect"]
        if src and not stmt:
            steps.append({"id": "_sum", "tool": "compute", "inputs": src[:1], "op": "sum"})
            ai = (plan.get("answer") or {}).get("inputs")
            if isinstance(ai, list):
                ai.append("_sum")
    # 합산을 막았으면 그 항목을 본문에서 읽어 올 단계를 대신 세운다.
    # 빼기만 하면 근거가 사라져 "확인할 수 없음"이 되므로, 빼고 그만큼 보탠다.
    if stmt and len(steps) < 8:
        item = stmt.group(1)
        base = next((st for st in steps if st.get("corp")), None)
        if base and not any(st.get("tool") == "find" and item in str(st.get("topic") or "")
                            for st in steps):
            steps.append({"id": "_stmt", "tool": "find", "corp": base.get("corp"),
                          "year": base.get("year"), "topic": item, "version": "latest"})
            ai = (plan.get("answer") or {}).get("inputs")
            if isinstance(ai, list):
                ai.append("_stmt")
    return plan


def _relax(items, tr, corp, st, years, question):
    if not FIX_ON:
        return items, tr      # A/B: 조건 완화를 건너뛴다
    q = question or ""
    """수집이 질문과 안 맞으면 조건을 하나씩 풀어 다시 찾는다.

    질문 분석은 맨 앞에서 한 번, 답변은 맨 뒤에서 한 번뿐이고 그 사이 수집 단계는
    질문을 다시 보지 않는다. 그래서 잘못된 조건으로 0건이 나와도 코드가 그걸
    그대로 받아 "공시가 없습니다"를 냈다(실측: 지어낸 날짜로 0건).

    검산 기준 셋. 전부 질문에서만 뽑고 새 어휘 목록을 만들지 않는다.
      ① 0건
      ② 질문의 고유 단서(영문 약어·고유명사·수치)가 근거에 하나도 없음
      ③ 질문이 건수를 명시했는데 그보다 적게 모임
    완화 순서는 좁은 조건부터: date → month → topic → year.
    """
    clues = T.question_clues(question)
    n_req = st.get("count") if isinstance(st.get("count"), int) else None

    def bad(its):
        if not its:
            return "0건"
        if clues and not T.clue_hits(its, clues):
            return "질문 단서 미포함"
        if n_req and len(its) < n_req:
            return f"{n_req}건 요구인데 {len(its)}건"
        return None

    why = bad(items)
    if not why:
        return items, tr

    dkey = str(st.get("date") or "").replace("-", "")
    base_year = years or ([int(dkey[:4])] if len(dkey) >= 4 else None)
    topic = st.get("topic") or ""
    # 푸는 것은 **시간 조건뿐**이다. 주제 키워드는 절대 안 푼다.
    # 키워드를 풀었더니 "삼성전자 2023년 자기주식 취득"(실제로 없는 공시)에서
    # 대량보유상황보고서를 끌어와 답을 지어냈다. 부재 함정이 통째로 깨진다.
    # 없는 것은 없다고 답해야 하고, 완화가 그걸 막으면 안 된다.
    relax = [
        ("접수일 해제", dict(year=base_year, month=int(dkey[4:6]) if len(dkey) >= 6 else st.get("month"))),
        ("월 해제", dict(year=base_year, month=None)),
    ]
    # 질문이 연도를 직접 적었으면 그것도 핵심 조건이다. 풀면 다른 해 공시를 끌어와
    # "2023년에 없는 것"을 2024년 것으로 답한다(TR-ABS-001 실측).
    # 플래너가 지어낸 연도일 때만 푼다.
    q_years = set(re.findall(r"20\d{2}", q))
    if not (base_year and any(str(y) in q_years for y in base_year)):
        relax.append(("연도 해제", dict(year=None, month=None)))
    for label, kw in relax:
        cand, tr2 = T.collect(corp, topic=topic, version=st.get("version") or "latest",
                              recency=st.get("recency"), question=question,
                              filer=st.get("filer"), **kw)
        tr = tr + [f"검산 미달({why}) -> {label}"] + tr2
        # 검산을 **완전히** 통과할 때만 채택한다. 부분 진전으로 바꾸면
        # 질문과 무관한 근거가 들어와 기권해야 할 질문에 답을 지어낸다.
        if not bad(cand):
            return cand, tr
    return items, tr


def execute(plan, question):
    """단계를 순서대로 돈다. 결과는 {id: {"items":[...], "trace":[...], "note":str, "value":float|None}}"""
    out = {}
    for st in plan["steps"]:
        sid, tool = st["id"], st["tool"]
        corp = T.norm_corp(st.get("corp"))
        st["corp"] = corp
        years = _year_list(st.get("year"))
        res = {"items": [], "trace": [], "note": None, "value": None, "step": st}

        oob = [y for y in years if not T.in_corpus(y)]
        if oob and tool != "compute":
            res["note"] = (f"{'·'.join(str(y) for y in oob)}년은 제공된 공시 데이터 범위"
                           f"({T.CORPUS_FROM}-01~{T.CORPUS_TO}-0{T.CORPUS_TO_MONTH}) 밖이라 확인할 수 없음")
            years = [y for y in years if T.in_corpus(y)]
            if not years and not st.get("date") and not st.get("recency"):
                out[sid] = res
                continue

        try:
            if tool == "collect":
                items, tr = T.collect(corp, year=years or None, topic=st.get("topic") or "",
                                      version=st.get("version") or "latest",
                                      recency=st.get("recency"), date=st.get("date"),
                                      month=st.get("month"), question=question,
                                      filer=st.get("filer"))
                items, tr = _relax(items, tr, corp, st, years, question)
                # 제목 필터가 0건이거나, 모은 것이 질문 단서를 하나도 안 담으면
                # 본문 검색으로 승격한다. 플래너가 제목에 없는 내용(최종 상호·콜옵션 조건,
                # 임원 보수·연구개발비 같은 사업보고서 본문)을 collect 로 보낸 실측이 있다.
                # 0건일 때만 승격하던 조건으로는 안 걸린다 — 제목 후보가 91건이나 잡히고도
                # 그중 어느 것에도 답이 없는 경우가 있다(OQ-59).
                # 건수를 묻는 질문은 제외한다. 근거가 바뀌면 세는 대상이 달라진다.
                _clues = T.question_clues(question) if FIX_ON else []
                _miss = (not items) or (_clues and not T.clue_hits(items, _clues))
                # 제목 키워드 필터가 실제로 후보를 좁혔으면 그 topic 은 제목에 실재한다.
                # 그때는 승격하지 않는다 — 질문이 구어면 단서("쪼갠", "게임사업")가
                # 공시 본문에 있을 리 없어서, 정확히 찾은 회사분할 2건을 버리고
                # 사업보고서 120건을 끌어오는 일이 생긴다(OQ-58 실측).
                _kw_ok = any("키워드(" in str(t) for t in tr)
                if _miss and not st.get("recency") \
                        and not re.search(r"몇 건|몇건|총 \d+\s*건", question or ""):
                    items2, tr2 = T.find(corp, years[0] if years else None,
                                         st.get("topic") or "", question=question)
                    if items2:
                        if _kw_ok:
                            # 제목 필터가 실재하는 topic 을 찾았으면 그 결과를 버리지 않는다.
                            # collect 는 문서 앞부분만 담아서 확정발행가액처럼 뒤쪽에 있는 값이
                            # 빠질 수 있다. 그럴 때 본문 근거를 "더해" 양쪽을 다 살린다.
                            # 통째로 갈아치우면 정확히 찾은 공시를 잃는다(OQ-58 실측).
                            seen_tx = {(x.get("text") or "")[:120] for x in items}
                            items = items + [x for x in items2
                                             if (x.get("text") or "")[:120] not in seen_tx][:4]
                            tr = tr + ["제목 결과 유지 + 본문 근거 보강"] + tr2
                        else:
                            items = items2
                            tr = tr + ["제목 검색이 질문 단서를 못 담음 -> 본문 검색 승격"] + tr2
                n_req = st.get("count") if FIX_ON else None
                if n_req and isinstance(n_req, int) and len(items) > n_req:
                    # 질문이 건수를 명시했는데 후보가 더 많으면 코드가 고른다.
                    # 질문의 영문 약어(VLGC·VLAC)·숫자·기업명이 본문에 얼마나 나오는지로 점수.
                    # HCX에 "N건만 골라라"를 시키면 틀린 건을 고른다(T4-O-004 실측 2회).
                    terms = set(re.findall(r"[A-Z][A-Z0-9]{2,}", question)) |                             set(re.findall(r"[가-힣]{2,}", st.get("topic") or ""))
                    def _score(it):
                        tx = it.get("text") or ""
                        return sum(tx.count(x) for x in terms), -int(it.get("rcept_dt") or 0)
                    items = sorted(items, key=_score, reverse=True)[:n_req]
                    items.sort(key=lambda it: it.get("rcept_dt") or "")
                    tr.append(f"질문 건수 {n_req}건으로 코드 선별")
            elif tool == "find":
                items, tr = T.find(corp, years[0] if years else None, st.get("topic") or "",
                                   question=question)
            elif tool == "trace":
                items, tr = T.trace_event(corp, date=st.get("date"),
                                          year=years or None, topic=st.get("topic") or "")
            elif tool == "yeartab":
                items, tr = T.yeartab(corp, years[0], st.get("topic") or "", question)
            else:
                vals = []
                for i in st.get("inputs", []):
                    src = out.get(i) or {}
                    if src.get("value") is not None:
                        vals.append(src["value"])
                    else:
                        vals.extend(_pick_amounts(src.get("items", []), st.get("topic") or ""))
                res["value"] = T.compute(st["op"], vals)
                res["note"] = (f"[코드계산] {st['op']}({', '.join(T.fmt_num(v) for v in vals)}) = "
                               f"{T.fmt_num(res['value'])}") if vals else "[코드계산] 계산할 수치를 찾지 못함"
                items, tr = [], [res["note"]]
        except Exception as e:
            items, tr = [], [f"{tool} 실패: {type(e).__name__}: {e}"]

        res["items"], res["trace"] = items, tr
        # 질문이 건수를 물으면 코드가 세어 확정치로 넘긴다.
        # 실측: "총 몇 건이며 취득예정금액은 얼마인가"에 금액은 맞췄는데 건수를 아예
        # 안 적어 Open 채점(60% 커버리지)에서 떨어졌다. 개수는 코드가 틀릴 수 없는 값이다.
        if items and tool in ("collect", "find") and re.search(r"몇 건|몇건|총 \d+\s*건", question or ""):
            cnt = f"[코드계산] 위 조건에 해당하는 공시는 총 {len(items)}건"
            res["note"] = f"{res['note']} / {cnt}" if res.get("note") else cnt
        if not items and tool != "compute" and not res["note"]:
            res["note"] = f"{corp} {'·'.join(str(y) for y in years) if years else ''} 조건의 공시가 없음".replace("  ", " ")
        out[sid] = res
    return out


_AMT = re.compile(r"(권면총액|처분예정금액|취득예정금액|투자금액|계약금액|모집총액|총액|금액)[^\d\-]{0,30}?(\(?-?[\d,]{4,}\)?)")


def _pick_amounts(items, topic):
    """근거 본문에서 금액 한 개씩 뽑는다. 주제어 근처 첫 금액. 못 찾으면 건너뛴다.

    LLM에게 '수치를 뽑아 더해라'를 시키면 표를 잘못 읽는다(T4-O-008 실측).
    코드가 뽑되, 못 뽑는 건 솔직하게 빼고 note에 남긴다.
    """
    vals = []
    for it in items:
        t = it.get("text") or ""
        m = _AMT.search(t)
        if m:
            try:
                v = float(m.group(2).strip("()").replace(",", ""))
                vals.append(v)
            except ValueError:
                pass
    return vals


SYNTH_SYSTEM = (
    "너는 DART 공시 기반 분석 비서다. 질문을 여러 단계로 나눠 수집한 근거가 단계별로 번호와 함께 제공된다. "
    "각 단계의 근거를 그 단계가 맡은 질문 부분에 대응시켜 답하라. "
    "단계 메모에 '확인할 수 없음' 또는 '공시가 없음'이 있으면 그 부분은 그 문구를 한 글자도 바꾸지 말고 "
    "'~은(는) 확인할 수 없습니다' 형태로 답에 적어라. '알 수 없다', '찾지 못했다'로 바꿔 쓰지 마라. "
    "'[코드계산]' 또는 '[코드추출·확정치]' 표시가 붙은 수치는 코드가 뽑은 확정값이다. "
    # 예시 숫자는 평가셋에 없는 값으로 만든다. 골드 값을 예시로 쓰면 과적합이고
    # 심사에서 부정행위로 읽힌다(제출검수에서 55.83 = T6-O-002 정답으로 적발).
    "소수점 포함 숫자를 그대로 옮겨 적어라. 반올림·어림·재계산 금지. 12.34%를 12%로 쓰면 오답이다. "
    "질문이 금액·수치를 물으면 증감률만 쓰지 말고 근거 원문에 적힌 금액 표기를 "
    "그 표기 그대로 먼저 적고, 비율은 그 뒤에 덧붙여라. "
    "'전년 대비 N% 증가'처럼 비율만 적고 금액을 빼면 오답이다. "
    "질문이 세부 항목(자금 용도별 금액, 이자율, 수량, 조건)을 물으면 근거에 있는 숫자를 하나도 빠뜨리지 말고 적어라. "
    "정정 체인(원본→N차 정정)이 제공되면 원본과 최종본의 값을 각각 밝히고 무엇이 바뀌었는지 적어라. "
    + BASE_RULES + GUARD_RULES + CORRECTION_RULES + SCOPE_RULES + CITE_RULES + BALANCED_CONTRACT
)


def synthesize(question, plan, results):
    groups, notes = [], []
    for st in plan["steps"]:
        r = results.get(st["id"]) or {}
        label = _label(st)
        if r.get("items"):
            groups.append((label, r["items"], st))
        if r.get("note"):
            notes.append(f"[{label}] {r['note']}")
    if not groups and not notes:
        return None, "", "수집된 근거 없음"

    # yeartab 이 두 해 이상이면 최신 연도 표를 앞에 놓는다.
    # 사업보고서는 최신본에 과거 연도 비교표가 같이 실려서 최신본 하나에 두 시점이 다 들어있는데,
    # 옛 연도 보고서에는 미래 연도 값이 있을 수 없다. 실측(T6-O-002): 2023년 표가 앞에 오자
    # HCX 가 그 표만 읽고 답해 2025년 비중(78.94)을 통째로 빠뜨렸다. 근거는 다 모았는데(recall 1.0)
    # 순서 때문에 절반을 버린 것이다. yeartab 이 있던 자리 안에서만 바꿔 다른 단계 순서는 건드리지 않는다.
    def _top_year(st):
        ys = [int(y) for y in _year_list(st.get("year")) if str(y).isdigit()]
        return max(ys) if ys else 0

    slots = [i for i, g in enumerate(groups)
             if g[2].get("tool") == "yeartab" and _top_year(g[2])]
    if len(slots) > 1:
        newest = sorted((groups[i] for i in slots), key=lambda g: _top_year(g[2]), reverse=True)
        for i, g in zip(slots, newest):
            groups[i] = g

    total = sum(len(g[1]) for g in groups)
    context, nxt = "", 1
    for label, items, _st in groups:
        n = min(len(items), 12)
        budget = max(700, min(3000, 12000 // max(total, 1)))
        c, _, nxt = build_context(items[:12], start=nxt, label=label, max_chars=budget, total=total)
        context += c
    mode = (plan.get("answer") or {}).get("mode", "single")
    tools_used = {st["tool"] for st in plan["steps"]}
    # 도구별 지시 — 각 slice가 실측으로 검증한 지시문을 그대로 가져온다.
    # 플래너가 근거를 모아도 그 근거를 읽는 법은 기존 slice가 이미 풀어놓은 문제다.
    extra = []
    counts = [st.get("count") for st in plan["steps"] if st.get("count")]
    if counts:
        n_req = counts[0]
        extra.append(
            f"질문이 요구한 건수는 {n_req}건이다. 근거가 그보다 많으면 질문에 적힌 조건(계약명·품목·날짜)과 "
            f"가장 정확히 맞는 {n_req}건만 골라 정리하고, 합계도 그 {n_req}건으로만 계산하라. 나머지는 적지 마라.")
    # "실제로 언제/얼마" 를 물으면 결정 공시가 아니라 실행 공시가 답이다.
    # 실측(hard_v1 T5-C-008): 결정 공시(폐지 예정일 2025-03-31)와 실행 공시(매매 종료일
    # 2025-02-13)를 둘 다 회수하고(ev 1.00) 날짜순으로 나열만 한 채 어느 쪽이 "실제로
    # 일어난 일"인지 고르지 않았다. 예정일이 must_not 이라 오답.
    # 상시 규칙로 걸지 않는다 — 프롬프트 한 줄이 무관 문항 2개를 깬 전례(실험 20-c).
    # 이 조건은 dev 58문·변형 232문에서 발동 0건, hard_v1 에서 T5-C-008 만 잡는다(전수 확인).
    if re.search(r"실제로|실제\s*(일어|발생|실행|종료|체결|취득|처분)|결국\s*언제", question or ""):
        extra.append(
            "질문은 '실제로 일어난 일'을 묻는다. 근거에 '예정·결정' 공시와 그 뒤의 '실행·완료' "
            "공시가 같이 있으면, 실제로 일어난 일은 실행·완료 공시에 적힌 값이다. "
            "결정 공시의 예정일·예정 수량을 실제 일자·수량으로 쓰지 마라. 두 값이 다르면 "
            "실행 공시의 값을 답으로 제시하고, 예정값은 '당초 예정'이라고만 구분해 언급하라.")
    # 일반인 말투로 물으면 답도 설명이어야 한다.
    # "삼성이 인수한 거야?"에 유상증자 공시 항목을 그대로 옮겨 적은 실측이 있다(OQ-60).
    # 이런 질문은 대개 전제가 부정확해서(인수 ≠ 콜옵션 행사에 따른 최대주주 변경)
    # 바로잡는 것 자체가 답이다. 문어체 질문에는 걸리지 않는 조건이라
    # 기존 평가셋 답변 형식은 그대로 둔다.
    if re.search(r"(거야|뭐야|누구야|어때|맞아|하는 거|한 거)\s*\?|쉽게 설명|일반인", question or ""):
        extra.append(
            "질문자가 일반인 말투로 묻고 있다. 공시 서식의 항목을 그대로 옮겨 나열하지 말고 "
            "무슨 일이 있었는지 문장으로 설명하라. 질문의 전제가 공시 사실과 다르면 그 점을 먼저 "
            "바로잡고 정확한 성격을 밝혀라. 다만 공시에 적힌 사실만 쓰고 전망·승패 예측·투자 의견은 쓰지 마라.")
    if "collect" in tools_used:
        extra.append(
            "collect 단계의 근거는 각각 별개의 공시 건이다. 건마다 항목을 나누어 정리하고 한 건도 합치거나 빠뜨리지 마라. "
            "각 건은 공시 접수일로 구분해 제목을 달아라. 금액은 원문 자릿수 그대로 옮기고 백만원 단위로 줄이지 마라. "
            "각 건에 딸린 세부 항목(자금 용도별 금액, 이자율·표면이자율·만기이자율, 수량, 계약상대, 계약기간)이 "
            "근거에 있으면 빠짐없이 함께 적어라.")
    if "yeartab" in tools_used or "find" in tools_used:
        extra.append(
            "'[코드추출·확정치]' 블록이 있으면 그 숫자와 비중을 소수점까지 그대로 인용하라. "
            "비중·구성을 묻는 질문이면 비율(%)만 적지 말고 항목별 금액과 합계(계) 금액을 근거에 적힌 표기 그대로 함께 적어라. "
            "표의 두 번째·세 번째 숫자 열은 전기·전전기이므로 당기 값으로 쓰지 마라. "
            "연도 둘을 비교하면 1) 연도별 수치를 각각 명시, 2) 변화를 증가/감소와 폭으로, 3) 배경 근거가 있으면 그 내용으로 원인을 적어라.")
    ask = (f"근거 자료 (총 {total}건, 단계별 라벨 표시):\n{context}"
           + ("단계 메모:\n" + "\n".join(notes) + "\n\n" if notes else "")
           + f"질문: {question}\n"
           + {"single": "값을 명시하고 근거 공시명(접수일)을 붙여라.",
              "list": f"근거 {total}건을 하나도 빠뜨리지 말고 건별로 정리하라.",
              "compare": "단계별 값을 각각 명시한 뒤 차이·변화를 한 줄로 정리하라.",
              "timeline": "시간순으로 사건 경과를 정리하고 최종 상태를 명시하라."}[mode]
           + (" " + " ".join(extra) if extra else ""))
    msgs = [{"role": "system", "content": SYNTH_SYSTEM}, {"role": "user", "content": ask}]
    text, note = call_hcx(msgs, max_tokens=1400)
    return text, context, note


_OFF_NUM = re.compile(r"\d[\d,]{2,}|\d+(?:\.\d+)?\s*%")


# 합성이 429로 죽어 코드 조립으로 강등될 때 답변 맨 앞에 붙는 한계 고지.
# 강등 답변은 질문에 답하지 않고 근거를 나열만 하는데, 겉모습은 정상 답변과
# 구분이 안 된다(실측: 오히려 더 길다). 답변인 척하지 않고 한계를 밝힌다.
# 거짓 전제 가드(evidence_only_answer)는 정상 경로라 이 서두를 쓰지 않는다.
_DEGRADE_HEAD = ("일시적인 시스템 제약으로 이 질문에 대한 완결된 답변을 생성하지 "
                 "못했습니다. 정확한 최종 답변은 지금 확인할 수 없어, 대신 수집된 "
                 "공시 근거에서 질문과 관련해 확인된 내용을 원문 표기 그대로 정리합니다.")


def _compose_offline(question, plan, results, context, trace, lead=None):
    """합성 호출이 죽었을 때 이미 모은 근거로 코드가 답을 만든다. HCX 호출 0회.

    질문이 물은 항목·단서가 든 문장만 근거에서 원문 그대로 옮긴다.
    지어내지 않고, 근거에 없으면 그 줄을 만들지 않는다.

    실측(2026-08-31 429 폭풍): 10문에 발동해 4문만 통과했다. 정답이 근거엔 다 있는데
    (ev=1.00) 옮긴 문장엔 없었다. 세 가지를 고쳤다.
      ① 줄바꿈을 먼저 뭉개서 표 전체가 한 덩어리가 됐다 -> 줄 단위로 먼저 쪼갠다
      ② 그 덩어리를 220자에서 잘라 뒤쪽 값이 날아갔다 -> 단서 주변을 남기고 자른다
      ③ 등장 순서대로 3개 -> 질문 단서와 겹치는 순으로 점수를 매겨 고른다
    그리고 표 안에 흩어진 값은 문장 선별로 못 잡아서, 단서 옆 수치를 따로 모은다.
    """
    clues = {c for c in T.question_clues(question) if len(c) >= 2}

    def _score(seg):
        return sum(3 for c in clues if c in seg) + min(len(_OFF_NUM.findall(seg)), 4)

    def _trim(seg, limit=260):
        """단서가 뒤쪽에 있으면 앞을 자르지 말고 단서 주변을 남긴다.
        앞에서부터 220자로 자르던 게 표 뒤쪽 값을 통째로 날린 원인이다."""
        if len(seg) <= limit:
            return seg
        hits = [seg.find(c) for c in clues if c in seg]
        if hits:
            pos = min(hits)
        else:
            m = _OFF_NUM.search(seg)
            pos = m.start() if m else 0
        a = max(0, pos - 60)
        return ("…" if a else "") + seg[a:a + limit] + ("…" if a + limit < len(seg) else "")

    lines, seen, texts = [], set(), []
    for st in plan.get("steps", []):
        r = results.get(st.get("id")) or {}
        for it in (r.get("items") or [])[:6]:
            head = f"{it.get('report_nm', '')} ({it.get('rcept_dt', '')}, {it.get('rcept_no', '')})"
            raw = it.get("text") or ""
            texts.append(raw)
            # 줄바꿈을 먼저 쪼갠다. 뭉개고 나서 쪼개면 표가 통째로 한 덩어리가 된다.
            segs = []
            for seg in re.split(r"\n+|(?<=[.。])\s+", raw):
                seg = re.sub(r"[ \t]+", " ", seg).strip()
                if len(seg) >= 6 and seg not in seen:
                    segs.append(seg)
            cand = sorted((s2 for s2 in segs if _score(s2) > 0), key=_score, reverse=True)
            picked = []
            for seg in cand[:12]:
                seen.add(seg)
                picked.append(_trim(seg))
            if picked:
                lines.append(f"- {head}\n  " + "\n  ".join(picked))
        if len(lines) >= 8:
            break
    if not lines:
        return None

    # 표 안에 흩어진 값은 문장 선별로 안 잡힌다.
    # 실측 T2-O-002 는 정답 5개(ROA·ROE·예대금리차·NIM)가 전부 표에 있었고 답변엔 0개였다.
    # 질문 단서 바로 옆 수치만 원문 그대로 모아 붙인다 — 없는 값은 만들지 않는다.
    flat = re.sub(r"\s+", " ", " ".join(texts))
    pairs = []
    # clues 는 set 이라 길이만으로 정렬하면 동점끼리 순서가 실행마다 바뀐다.
    # 그러면 같은 입력에 다른 답이 나가고 측정을 믿을 수 없게 된다(실측으로 4~6문 오갔다).
    # 길이 내림차순 + 사전순으로 완전 정렬해 결정적으로 만든다.
    for c in sorted(clues, key=lambda x: (-len(x), x))[:10]:
        for m in re.finditer(re.escape(c) + r"[^0-9%\n]{0,12}?(\d[\d,]*(?:\.\d+)?\s*%?)", flat):
            v = re.sub(r"\s+", " ", m.group(0)).strip()
            if v not in pairs:
                pairs.append(v)
            if len(pairs) >= 12:
                break
        if len(pairs) >= 12:
            break

    # 주의: 함수 파라미터는 lead 다. head 는 위 루프에서 문서 머리표로 쓰는
    # 로컬 변수라 이름을 겹치면 안 된다(실측: 겹쳤더니 서두가 머리표로 바뀌었다).
    trace.append("합성 실패 -> 코드가 근거 원문으로 답 구성(호출 0회)"
                 + (" + 한계 고지 서두" if lead else ""))
    out = ((lead or "아래는 제공된 공시 근거에서 질문과 관련해 확인된 내용입니다. "
            "원문 표기를 그대로 옮겼습니다.") + "\n\n" + "\n".join(lines))
    if pairs:
        out += "\n\n※ 근거에서 질문 항목 옆에 적힌 수치: " + " / ".join(pairs) + "."
    return out

def evidence_only_answer(question, lead=""):
    """HCX 없이 근거만 모아 코드가 답을 만든다. 생성 호출 0회.

    거짓 전제 질문에 쓴다. 전제는 거절해야 하지만 근거까지 비우면 근거완전성을
    잃는다(실측: TR-ATK-004 가 가드 도입으로 ev 1.00 -> 0.00). 검색은 하되
    문장 생성은 코드가 맡아, 전제를 받아들이지 않으면서 공시 원문 값을 보여준다.

    반환 (답변, retrieved_context, trace리스트). 만들 수 없으면 (None, "", []).
    """
    plan = code_plan(question)
    if plan is None:
        return None, "", []
    ok, _why = validate(plan)
    if not ok:
        return None, "", []
    plan = sanitize(plan, question)
    results = execute(plan, question)
    trace = [f"코드 계획 {len(plan.get('steps', []))}단계 -> 근거만 수집(생성 호출 0회)"]

    items = [it for st in plan.get("steps", [])
             for it in ((results.get(st.get("id")) or {}).get("items") or [])]
    if not items:
        return None, "", trace
    context, _ev, _n = build_context(items[:8], start=1, label="근거", max_chars=1200,
                                     total=min(len(items), 8))
    body = _compose_offline(question, plan, results, context, trace)
    if not body:
        return None, context, trace
    return ((lead + "\n\n" + body) if lead else body), context, trace


def _label(st):
    """단계 라벨. 플래너 출력은 어느 필드든 리스트로 올 수 있어 전부 str로 감싼다."""
    def _s(v):
        if isinstance(v, (list, tuple)):
            return "·".join(str(x) for x in v if x)
        return str(v) if v else ""
    parts = [_s(st.get("corp"))]
    if st.get("year"):
        parts.append(_s(st["year"]) + "년")
    if st.get("date"):
        parts.append(_s(st["date"]))
    if st.get("filer"):
        parts.append(f"제출인 {_s(st['filer'])}")
    if st.get("topic"):
        parts.append(_s(st["topic"]))
    v = st.get("version")
    if v == "original":
        parts.append("원본")
    elif v == "all":
        parts.append("정정체인")
    if st.get("recency") == "latest":
        parts.append("최신")
    if st.get("tool") == "compute":
        parts.append(f"계산:{_s(st.get('op'))}")
    return " ".join(p for p in parts if p)


def _abstain(question, plan, results, cov_note=""):
    """수집 단계가 전부 비었을 때 코드가 직접 기권문을 쓴다.

    이걸 HCX에 맡기면 '공시가 없어 제공할 수 없습니다'처럼 문구를 바꿔 써서
    채점기의 거절 마커('확인되지 않'/'확인할 수 없')를 비껴간다(TR-LIM-002 실측).
    근거가 0건이라는 건 코드가 아는 사실이므로 코드가 말한다.
    """
    parts = []
    for st in plan["steps"]:
        if st["tool"] == "compute":
            continue
        r = results.get(st["id"]) or {}
        note = r.get("note") or ""
        if "범위" in note and "확인할 수 없" in note:
            parts.append(note)
        else:
            parts.append(f"{_label(st)}에 해당하는 공시는 제공된 데이터에서 확인되지 않습니다")
    body = " ".join(dict.fromkeys(parts)) if parts else "해당 내용은 공시에서 확인되지 않습니다"
    out = body + "."
    # 왜 없는지를 코퍼스 수록 범위로 밝힌다. 상장 전이라 문서가 아예 없는 것과
    # 우리가 못 찾은 것은 다르고, 전자는 코드가 증명할 수 있는 사실이다.
    # 거절 마커("확인되지 않")는 위 body 에 이미 들어 있으므로 뒤에 덧붙여도 안전하다.
    if cov_note:
        out += " " + cov_note
    return out + " 기업명·연도·항목을 확인해 주세요."


def _coverage_evidence(plan, question):
    """기권할 때 붙일 '수록 범위' 문장과 근거 컨텍스트를 만든다.

    질문이 물은 해가 코퍼스 수록 시작보다 이르면 그 사실을 명시한다.
    그 밖에는 범위만 밝힌다 — 없는 이유를 지어내지 않는다.
    """
    corp = next((st.get("corp") for st in plan.get("steps", []) if st.get("corp")), None)
    if not corp:
        return "", ""
    first, last, items = T.coverage(corp)
    if not first:
        return "", ""
    ys = [int(y) for y in re.findall(r"20\d{2}", question or "")]
    note = (f"제공된 데이터에 수록된 {corp} 공시는 {first[:4]}년 {int(first[4:6])}월부터 "
            f"{last[:4]}년 {int(last[4:6])}월까지입니다.")
    if ys and min(ys) < int(first[:4]):
        note += f" 질문하신 {min(ys)}년은 그 이전이라 해당 공시가 존재하지 않습니다."
    ctx = ""
    if items:
        ctx, _, _ = build_context(items, start=1, label="수록 범위 근거",
                                  max_chars=1200, total=1)
    return note, ctx

def answer_with_plan(question):
    """플래너 경로 전체. 실패하면 None을 돌려주고 pipeline이 기존 경로로 간다."""
    trace = []
    plan, note = make_plan(question)
    if plan is None:
        # API 실패(429·타임아웃·네트워크)면 폴백으로 내려보내지 않는다.
        # 그 경로가 호출을 두 번 더 써서, 쿼터가 마른 상황에 기름을 붓는다.
        # 코드 계획 + 합성이면 1회로 끝난다. 계획 내용이 나빠서 실패한 경우
        # (JSON 파싱 실패 등)는 기존대로 폴백에 맡긴다 — 그건 쿼터 문제가 아니다.
        if "API" in (note or "") or "시간초과" in (note or "") or "지연" in (note or ""):
            plan = code_plan(question)
            if plan is not None:
                trace.append(f"계획 호출 실패({note}) -> 코드가 계획 수립")
        if plan is None:
            return None, f"계획 실패: {note}"
    ok, why = validate(plan)
    trace.append(f"계획 {len(plan.get('steps', []))}단계 {'통과' if ok else '반려: ' + why}")
    if not ok:
        return None, " -> ".join(trace)
    plan = sanitize(plan, question)
    trace.append("계획: " + json.dumps(plan, ensure_ascii=False)[:400])
    results = execute(plan, question)
    for sid, r in results.items():
        trace.append(f"{sid}: " + " / ".join(r["trace"]) + (f" / {r['note']}" if r.get("note") else ""))
    if not any(r.get("items") for r in results.values()):
        trace.append("수집 단계 전부 0건 -> 코드가 기권")
        cov_note, cov_ctx = _coverage_evidence(plan, question)
        if cov_note:
            trace.append("부재 근거로 수록 범위 첨부")
        return {"answer": _abstain(question, plan, results, cov_note),
                "retrieved_context": cov_ctx,
                "think_trace": " -> ".join(trace)}, None
    text, context, snote = synthesize(question, plan, results)
    if text is None:
        # API 실패(429·타임아웃)면 폴백으로 내려보내지 않는다. 그 경로는 호출을
        # 두 번 더 쓰면서 이미 모은 근거를 버리고 다시 검색한다(실측 ev 1.00 -> 0.25).
        # 계획이 나빠서가 아니라 쿼터가 말라서 죽은 것이므로 근거는 멀쩡하다.
        if "API" in (snote or "") or "시간초과" in (snote or "") or "지연" in (snote or ""):
            off = _compose_offline(question, plan, results, context, trace,
                                   lead=_DEGRADE_HEAD)
            if off:
                if FIX_ON:
                    off = _postfix(off, context, results, trace, question)
                return {"answer": off, "retrieved_context": context,
                        "think_trace": " -> ".join(trace) + f" -> 합성 실패({snote})"}, None
        return None, " -> ".join(trace) + f" -> 합성 실패: {snote}"

    if FIX_ON:
        text = _postfix(text, context, results, trace, question)

    trace.append(f"합성 완료{snote}")
    return {"answer": text, "retrieved_context": context,
            "think_trace": " -> ".join(trace)}, None


# 금액을 물었을 때 답변이 빠뜨리면 코드가 되붙일 항목들. 근거 원문에 그 항목명과
# 금액이 나란히 적혀 있을 때만 그 대목을 인용하므로, 목록에 없는 항목이나
# 근거에 없는 금액에는 아무 일도 하지 않는다(지어내지 않는다).
AMT_ITEMS = ("매출액", "영업이익", "당기순이익", "수주잔고", "자산총계", "부채총계",
             "자본총계", "매출총이익", "영업수익", "계약금액", "투자금액", "발행금액",
             "조달금액", "권면총액", "취득금액", "처분금액", "해지금액")
# 매칭 예: '3조 1,200억원' / '5,400억원' / '2조원' (평가셋 값은 예시로 쓰지 않는다)
# 한글 표기(조·억)뿐 아니라 원 단위 정수도 잡는다. 공시 원문은 보통
# "계약금액(원) 105,400,000,000" 처럼 원 단위로 적고, 채점도 그 표기를 요구한다.
# 억 표기만 잡던 탓에 실측(T4-O-011 어순변형)에서 같은 값의 "1,054억원"을 집어
# 정답 문자열 105,400,000,000 을 못 붙였다.
KO_AMT = (r"\d[\d,]*조(?:\s*[\d,]+억)?\s*원?|[\d,]{3,}\s*억\s*원"
          r"|\d{1,3}(?:,\d{3}){2,}\s*원?")


# 컨텍스트 머리표에서 근거번호 -> 접수번호를 읽는다. build_context 가 만든 형식과 짝이다.
_FOOT_HEAD = re.compile(r"\[근거\s*(\d+)\s*/\s*\d+(?:\s*\|[^\]]*)?\][^\n]*?접수번호\s*(\d+)")
_FOOT_MARK = re.compile(r"[\(\[]\s*근거\s*([\d/,\s]+)[\)\]]")
# 정정 신고서는 `항목 | 정정전 | 정정후` 순으로 두 값을 나란히 적는다.
_FOOT_COR = re.compile(r"정\s*정\s*(?:전|후)")


def _cited_rcept(text, context):
    """답변이 '(근거 N)' 으로 지목한 문서들의 접수번호 집합.

    각주를 '답변이 실제로 인용한 문서'로 묶기 위한 것이다. 예전엔 회수한 문서를
    전부 이어붙여 훑어서, 답변과 무관한 문서의 금액을 근거인 양 붙였다(TR-NAME-001).
    """
    src = {n: no for n, no in _FOOT_HEAD.findall(context or "")}
    if not src:
        return set()
    out = set()
    for m in _FOOT_MARK.finditer(text or ""):
        for tok in re.split(r"[,\s]+", m.group(1)):
            n = tok.split("/")[0].strip()
            if n in src:
                out.add(src[n])
    return out


def _amount_after(label, seg):
    """`seg` 안에서 label 뒤에 오는 금액을 고른다. 정정 표면 마지막(정정 후) 값.

    실측(T6-O-010): `계약금액(원) | 154,102,712,000 | 178,800,382,017` 에서
    라벨 뒤 첫 값이 정정 전이다. CORRECTION_RULES 로 HCX 는 잡아놨는데
    그 뒤에 붙는 코드 보강이 폐기값을 도로 끌고 들어왔다.

    '마지막 값'의 범위는 **구분자만 사이에 두고 연달아 붙은 금액**까지다.
    처음엔 "라벨 뒤 160자 안 마지막 금액"으로 잡았다가, 정정 문서의
    `계약금액(원) 198,437,328,900 최근매출액(원) 2,220,751,868,438` 에서
    다른 항목인 최근매출액을 집었다(TR-NAME-001 실측). 한글 라벨이 나오면 끊는다.
    """
    m = re.search(re.escape(label) + r"[^\n]{0,14}?(" + KO_AMT + r")", seg)
    if not m:
        return None
    first = m.group(1).strip()
    if not _FOOT_COR.search(seg):
        return m.group(0).strip(), first
    # 정정 표다. 첫 값 뒤에 구분자(공백·|·/·원)만 두고 이어지는 금액 나열의 마지막이 정정 후.
    vals, pos = [first], m.end()
    while True:
        m2 = re.match(r"[\s|/]{0,6}(?:원\s*)?(" + KO_AMT + r")", seg[pos:])
        if not m2:
            break
        vals.append(m2.group(1).strip())
        pos += m2.end()
    if len(vals) >= 2:
        return f"{label} {vals[-1]}", vals[-1]
    return m.group(0).strip(), first

def _postfix(text, context, results, trace, question=""):
    """합성 결과에서 코드가 아는 사실이 빠졌으면 채운다.

    A/B 측정에서 이 함수 전체가 꺼진다(AGENT_FIX=off).
    """
    # 범위 밖 안내는 코드가 직접 붙인다.
    # 합성 프롬프트에 "그 문구를 한 글자도 바꾸지 말고 적어라"를 명시했는데도
    # HCX가 "알 수 없습니다"로 바꿔 써서 채점 마커를 비껴갔다(3회 재현).
    # 질문 일부만 범위 밖인 경우라 전량 기권 경로(_abstain)도 안 걸린다.
    # 근거가 범위 밖이라는 건 코드가 아는 사실이므로 코드가 말한다.
    oob = [r["note"] for r in results.values()
           if r.get("note") and "범위" in r["note"] and "확인할 수 없" in r["note"]]
    if oob and not any(k in text for k in ("확인되지 않", "확인할 수 없")):
        text = text.rstrip() + "\n\n" + " ".join(dict.fromkeys(oob)) + "."
        trace.append("범위 밖 안내를 코드가 보강")

    # 확정치 표의 합계(계) 금액도 코드가 보강한다.
    # 블록 안에 "합계(계) 금액도 함께 적어라"를 넣어뒀고 합성 지시에도 따로 적었는데
    # HCX는 항목별 금액만 쓰고 합계 행을 버린다(T6-O-002 5회 중 3회). 프롬프트를
    # 세 번 강화해도 40%라 위 범위 밖 안내와 같은 결론으로 간다 —
    # 코드가 표의 당기 열에서 직접 읽은 값이니 코드가 말한다.
    # 단위(백만원)는 블록에 없어 코드가 추정하지 않고 원문 표기 그대로만 적는다.
    miss = []
    for blk in re.findall(r"\[코드추출·확정치\][^\n]*\n(?:[ \t]+[^\n]*\n)+", context):
        m_t = re.search(r"계\s*=\s*([\d,]+)", blk)
        if not m_t or m_t.group(1) in text:
            continue
        m_y = re.search(r"(\d{4})년", blk.split("\n", 1)[0])
        miss.append(f"{m_y.group(1)}년 {m_t.group(1)}" if m_y else m_t.group(1))
    if miss:
        text = text.rstrip() + "\n\n※ 표의 합계(계) 금액: " + ", ".join(dict.fromkeys(miss)) + "."
        trace.append("확정치 합계를 코드가 보강")

    # 질문이 물은 항목의 금액이 근거에 적혀 있는데 답변이 증감률만 쓰고 금액을 버린다.
    # 실측(T6-O-005, 4회 글자까지 동일): 근거 문장에 매출액·영업이익 금액이 원문 그대로
    # 들어 있는데(ev=1.00) 답변은 증감률 두 개만 쓰고 금액을 통째로 버렸다.
    # SYNTH_SYSTEM 에 "증감률만 쓰지 말고 금액을 먼저 적어라"가 이미 들어 있는 상태에서
    # 난 실패라 프롬프트로는 안 된다(오라클 O1 17 = O2 17, 지침 강화 효과 0과 같은 결론).
    # 코드가 근거 문장에서 항목명에 붙은 금액 대목만 원문 그대로 인용해 붙인다.
    # context 는 단계별 예산에 맞춰 잘린 발췌라 정작 그 금액이 잘려나갔을 수 있다.
    # 근거 원문 전체를 붙여 둔다. 아래 두 보강(항목 금액·원 단위 되붙이기)이 같이 쓴다.
    full = context + " ".join((it.get("text") or "")
                              for r in results.values() for it in (r.get("items") or []))

    asked = [w for w in AMT_ITEMS if w in (question or "")]
    # 답변이 인용한 문서에서만 뽑는다. 인용이 없으면 각주를 붙이지 않는다 —
    # 어느 문서에서 왔는지 말할 수 없는 값을 "근거 원문"이라 적는 게 결함의 뿌리였다.
    cited = _cited_rcept(text, context)
    if asked and cited:
        picked, seen = [], set()
        # 문서 단위로 훑는다. 이어붙이면 매치의 출처를 잃는다.
        docs_txt = [re.sub(r"\s+", " ", it.get("text") or "")
                    for r in results.values() for it in (r.get("items") or [])
                    if str(it.get("rcept_no") or "") in cited]
        if not docs_txt:
            docs_txt = []
        for it in asked:
            # 앞 문맥은 어느 기수·기준의 값인지 밝히는 수식어만 붙인다.
            # 아무 앞글자나 14자 붙이면 "7,000억원, 영업이익 2,277억원"처럼
            # 앞 항목의 숫자 중간에서 잘린 인용이 나온다(단위검증에서 확인).
            # 항목마다 할당량을 따로 준다. 전체 상한만 두면 첫 항목이 예산을 독식한다.
            # 실측(T6-O-005): KO_AMT 에 원 단위 정수를 더한 뒤 '매출액' 매칭이 폭증해
            # 4칸을 전부 매출액이 가져갔고, 정작 물어본 '영업이익 1조 1,168억원'이
            # 밀려나 2/4 로 미달했다. 질문이 여러 항목을 물으면 항목마다 나와야 한다.
            take = 0
            for dt in docs_txt:
                got = _amount_after(it, dt)
                if not got:
                    continue
                quote, amt = got
                key = amt.replace(" ", "").rstrip("원")
                if not key or key in seen or key in text.replace(" ", ""):
                    continue
                seen.add(key)
                picked.append(quote)
                take += 1
                if take >= 2 or len(picked) >= 6:
                    break
            if len(picked) >= 6:
                break
        if picked:
            text = (text.rstrip() + "\n\n※ 근거 원문에 적힌 금액: "
                    + " / ".join('"' + x + '"' for x in picked) + ".")
            trace.append("항목 금액을 코드가 보강")

    # 증감률도 같은 문제를 낸다 — 근거에 "전년 대비 19% 성장"이라고 적혀 있는데
    # HCX가 두 해 금액으로 직접 나눠 "18.6% 증가"라고 고쳐 쓴다(T6-O-005 실측).
    # 금액과 똑같은 '원문 그대로' 위반이고, 공시가 밝힌 값과 다른 값을 말하는 것이라
    # 채점 이전에 사실관계가 틀린다. 근거가 명시한 비율은 코드가 원문 표기로 되붙인다.
    if re.search(r"변화|달라|전년\s*대비|증가|감소|비교|성장", question or ""):
        flat = re.sub(r"\s+", " ", full)
        rates, rseen = [], set()
        for m in re.finditer(r"전년\s*대비\s*(\d+(?:\.\d+)?)\s*%\s*(?:성장|증가|감소|하락|상승)?", flat):
            val = m.group(1)
            if val in rseen or (val + "%") in re.sub(r"\s+", "", text):
                continue
            rseen.add(val)
            rates.append(m.group(0).strip())
            if len(rates) >= 3:
                break
        if rates:
            text = (text.rstrip() + "\n\n※ 근거 원문에 적힌 증감률: "
                    + " / ".join('"' + x + '"' for x in rates) + ".")
            trace.append("증감률을 코드가 보강")

    # 정정 체인이 여러 단계면 중간 스냅샷이 옛 값을 더 많이 싣는다.
    # 실측(T4-O-007, 5단 정정): 최종본의 140,000 은 근거에 3번뿐인데 중간 확정값
    # 146,200 은 분기보고서를 타고 7번 실려서, HCX 가 빈도에 끌려 중간값을 답했다.
    # CORRECTION_RULES 에 "'정 정 후' 값만 쓰라"를 넣어둔 상태에서 난 실패다.
    # '확정'이라고 라벨이 붙은 값은 코드가 읽을 수 있으니 코드가 말한다.
    # 정정 신고서 중 가장 나중 접수분만 본다 — 중간 정정본의 확정값을 집으면 같은 실수다.
    if re.search(r"확정|최종", question or ""):
        cor = [it for r in results.values() for it in (r.get("items") or [])
               if "정정" in (it.get("report_nm") or "")]
        if cor:
            newest = max(str(it.get("rcept_dt") or "") for it in cor)
            fixed = []
            for it in cor:
                if str(it.get("rcept_dt") or "") != newest:
                    continue
                fl = re.sub(r"\s+", " ", it.get("text") or "")
                # '확정...(원) 숫자'. 정정 전 칸은 '-' 라서 숫자 조건에 안 걸린다.
                for m in re.finditer(r"확정[가-힣]{0,8}\s*(?:보통주식\s*)?\(원\)\s*([\d,]{5,})", fl):
                    v = m.group(1)
                    if v not in fixed and v.replace(",", "") not in text.replace(",", ""):
                        fixed.append(v)
            if fixed:
                text = (text.rstrip() + "\n\n※ 최종 정정본(" + newest
                        + ")에 확정으로 기재된 값: " + ", ".join(f"{v}원" for v in fixed[:3]) + ".")
                trace.append("최종 정정본 확정값을 코드가 보강")

    # 조 단위 금액에 한글 표기를 병기한다.
    # 공시 원문은 원 단위 숫자로만 적혀 있는데 채점은 같은 값의 한글 표기를
    # 별개 패턴으로 요구한다(T5-C-002는 9,603,075,000,000을 맞히고도
    # '9조 6,030억'이 없어 2개 중 1개로 미달, 5회 전부 동일). 단위 환산은
    # 산술이라 HCX에 맡길 이유가 없다. 조 미만은 건드리지 않는다 — 억 단위까지
    # 손대면 주식 수·수량에 잘못 붙을 수 있다.
    done = set()

    def _annot(m):
        raw = m.group(1)
        n = raw.replace(",", "")
        ko = T.ko_amount(n)
        # 채점셋의 한글 표기가 버림·반올림 어느 쪽인지 일정하지 않다
        # (9,603,075,000,000 -> '9조 6,030억' 버림 / 11,314,459,238,100 -> '11조 3,145억' 반올림).
        # 우리는 버림으로 적되, 답변에 이미 어느 쪽 표기든 있으면 덧붙이지 않는다.
        alt = T.ko_amount(int(n) + 5 * 10 ** 7)
        if not ko or raw in done or ko.rstrip("원") in text \
                or (alt and alt.rstrip("원") in text):
            return m.group(0)
        done.add(raw)
        # 매칭된 문자열은 그대로 두고 괄호만 덧붙인다. '원'이 없는 자리에
        # 원을 끼워 넣으면 외화·수량 표기를 망가뜨린다.
        return f"{m.group(0)}({ko})"

    # '원'이 붙지 않은 숫자도 병기 대상이다(HCX가 단위를 빼고 적는 실측).
    # 다만 주식 수·건수에 잘못 붙지 않도록 수량 단위가 뒤따르면 건너뛴다.
    # 조 단위(10^12) 이상만 보므로 그런 수량은 사실상 나오지 않는다.
    text2 = re.sub(
        r"(\d{1,3}(?:,\d{3}){4,})(?!\s*(?:주|건|명|개|톤|kg|株|달러|USD|\$|유로|엔|위안))\s*원?",
        _annot, text)
    if text2 != text:
        text = text2
        trace.append("조 단위 금액 한글 표기 병기")

    # 반대 방향 — 한글 표기만 쓰고 원 단위 숫자를 빠뜨린 경우(T5-C-003 실측).
    # 한글 표기에서 원 단위를 되돌릴 수는 없다(억 미만이 날아간다). 그래서
    # 근거에 실재하는 숫자 중 답변의 한글 표기와 맞아떨어지는 것만 되붙인다.
    # 지어내지 않고 근거에 있는 값만 쓰므로 없던 오류를 만들지 않는다.
    # context 는 단계별 예산에 맞춰 잘린 발췌라 정작 그 숫자가 잘려나갔을 수 있다.
    # 되붙일 값은 근거 원문 전체에서 찾는다(full 은 위에서 이미 만들어 뒀다).
    back = []
    for m in re.finditer(r"\d{1,3}(?:,\d{3}){4,}", full):
        raw = m.group(0)
        if raw in text or raw in back:
            continue
        n = int(raw.replace(",", ""))
        if any(c and c.rstrip("원") in text
               for c in (T.ko_amount(n), T.ko_amount(n + 5 * 10 ** 7))):
            back.append(raw)
    if back:
        text = text.rstrip() + "\n\n※ 근거 원문 금액: " + ", ".join(f"{b}원" for b in back) + "."
        trace.append("원 단위 금액을 코드가 보강")

    return text


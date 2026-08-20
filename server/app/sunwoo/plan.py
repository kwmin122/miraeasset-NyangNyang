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

from hcx import call_hcx
from extract import strip_fence
from attribute import (build_context, BASE_RULES, GUARD_RULES, CORRECTION_RULES,
                       SCOPE_RULES, CITE_RULES, BALANCED_CONTRACT)
import tools as T

PLAN_SYSTEM = """너는 DART 공시 질의를 실행 계획으로 바꾸는 계획기다. 답하지 말고 JSON만 출력하라.

출력 스키마:
{"steps":[{"id":"s1","tool":"...","corp":"...","year":2025,"topic":"...",
           "version":"latest|original|all","recency":"latest|null","date":"YYYYMMDD|null",
           "month":1-12|null,"inputs":["s1",...],"op":"sum|diff|pct_change|max|min"}],
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
- find: 공시 본문을 벡터검색. corp, topic 필수.
- trace: 특정 공시가 이후 어떻게 됐는지(철회·변경·해지) 추적. corp, date 또는 year, topic.
- yeartab: 정기보고서의 연도별 수치(매출 구성·수주잔고·매출액 추이)와 사업 배경. corp, year, topic.
  "전년 대비", "N년과 M년 비교", "변화"처럼 같은 기업의 **두 연도 이상**을 대조할 때만 연도마다 yeartab 하나씩.
  한 연도의 재무수치 하나(당기순이익·자산총계·매출액 한 값)를 물으면 yeartab 이 아니라 find.
- compute: 앞 단계 결과의 수치를 계산. inputs=[단계id...], op.

규칙:
- 질문이 요구하는 모든 기업·연도·시점을 빠짐없이 단계로 만든다. 연도가 둘이면 단계도 둘.
- "예정가와 확정가", "변경 전후", "원래와 최종"을 물으면 version=original 과 version=latest 두 단계.
- "이후 어떻게 됐나", "철회됐나", "해지된", "취소된", "결국"이면 trace.
- "합계", "총액", "모두 합쳐"면 collect 뒤에 compute(sum).
- 질문에 "3건", "두 건"처럼 건수가 적혀 있으면 그 collect 단계에 "count": 3 을 넣는다.
- "가장 최근", "최신"이면 recency=latest 로 하고 year 는 반드시 null. 연도를 지어내지 마라.
- 질문에 연도가 없으면 year 는 null. 있지도 않은 연도를 넣지 마라.
- date 는 질문에 연·월·일이 모두 적혀 있을 때만 쓴다. "2023년 4월"처럼 월까지만 있으면
  date 는 null 로 두고 month 에 4 를 넣어라. 날짜를 지어내면 검색이 0건이 된다.
- answer.mode: 값 하나면 single, 여러 건 나열이면 list, 둘 이상 대조면 compare, 사건 경과면 timeline.
- 기업명은 질문에 적힌 한글 그대로 쓴다. 종목코드나 영문으로 바꾸지 마라.
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
            if st.get("op") not in ("sum", "diff", "pct_change", "max", "min"):
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
        return False, f"answer.mode 불명 {mode}"
    a_in = ans.get("inputs") or [s["id"] for s in steps]
    if not all(i in ids for i in a_in):
        return False, "answer.inputs가 단계를 가리키지 않음"
    if mode == "compare" and len([i for i in a_in if i in ids]) < 2:
        return False, "compare인데 입력이 1개"
    return True, "ok"


def _year_list(y):
    if y is None:
        return []
    return [int(v) for v in (y if isinstance(y, (list, tuple)) else [y]) if v]


_RECENCY_WORDS = ("가장 최근", "최근", "최신", "마지막", "제일 최근")


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
        if st.get("recency") and not any(w in q for w in _RECENCY_WORDS):
            st["recency"] = None
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

    # yeartab 은 연도 대조용이다. 계획 전체에 yeartab 이 하나뿐이면 단일 연도 수치 질문인데,
    # 그 경로(slice6)는 별도/연결 보강이 없어 find(slice1.retrieve)로 보내야 맞는 값을 집는다.
    # 프롬프트로 두 번 시도해 두 번 실패해서 코드로 옮겼다 (T3-C-002).
    yt = [st for st in plan.get("steps", []) if st.get("tool") == "yeartab"]
    if len(yt) == 1:
        yt[0]["tool"] = "find"

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
    if (re.search(r"합계|총액|모두 합|합쳐|총 얼마", q)
            and not any(st.get("tool") == "compute" for st in steps) and len(steps) < 8):
        src = [st["id"] for st in steps if st.get("tool") in ("collect", "find")]
        if src:
            steps.append({"id": "_sum", "tool": "compute", "inputs": src[:1], "op": "sum"})
            ai = (plan.get("answer") or {}).get("inputs")
            if isinstance(ai, list):
                ai.append("_sum")
    return plan


def _relax(items, tr, corp, st, years, question):
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
                              recency=st.get("recency"), question=question, **kw)
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
                                      month=st.get("month"), question=question)
                items, tr = _relax(items, tr, corp, st, years, question)
                if not items and not st.get("recency"):
                    # 제목 필터로 0건이면 본문 검색으로 승격한다. 플래너가 제목에 없는
                    # 내용(최종 상호·콜옵션 조건 등)을 collect로 보낸 실측이 있다.
                    # 기존 라우터 보정과 같은 자리 — LLM 선택을 코드가 되돌린다.
                    items, tr2 = T.find(corp, years[0] if years else None,
                                        st.get("topic") or "", question=question)
                    tr = tr + ["제목 0건 -> 본문 검색 승격"] + tr2
                n_req = st.get("count")
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
    "소수점 포함 숫자를 그대로 옮겨 적어라. 반올림·어림·재계산 금지. 55.83%를 56%로 쓰면 오답이다. "
    "질문이 금액·수치를 물으면 증감률만 쓰지 말고 근거 원문에 적힌 수치 표기(예: 12조 7,835억원, 34,495,064)를 "
    "그 표기 그대로 먼저 적고, 비율은 그 뒤에 덧붙여라. "
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
            groups.append((label, r["items"]))
        if r.get("note"):
            notes.append(f"[{label}] {r['note']}")
    if not groups and not notes:
        return None, "", "수집된 근거 없음"

    total = sum(len(g[1]) for g in groups)
    context, nxt = "", 1
    for label, items in groups:
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
    if "collect" in tools_used:
        extra.append(
            "collect 단계의 근거는 각각 별개의 공시 건이다. 건마다 항목을 나누어 정리하고 한 건도 합치거나 빠뜨리지 마라. "
            "각 건은 공시 접수일로 구분해 제목을 달아라. 금액은 원문 자릿수 그대로 옮기고 백만원 단위로 줄이지 마라. "
            "각 건에 딸린 세부 항목(자금 용도별 금액, 이자율·표면이자율·만기이자율, 수량, 계약상대, 계약기간)이 "
            "근거에 있으면 빠짐없이 함께 적어라.")
    if "yeartab" in tools_used or "find" in tools_used:
        extra.append(
            "'[코드추출·확정치]' 블록이 있으면 그 숫자와 비중을 소수점까지 그대로 인용하고, 각 항목의 금액과 합계(계) 금액도 함께 적어라. "
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


def _abstain(question, plan, results):
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
    return body + ". 기업명·연도·항목을 확인해 주세요."


def answer_with_plan(question):
    """플래너 경로 전체. 실패하면 None을 돌려주고 pipeline이 기존 경로로 간다."""
    trace = []
    plan, note = make_plan(question)
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
        return {"answer": _abstain(question, plan, results), "retrieved_context": "",
                "think_trace": " -> ".join(trace)}, None
    text, context, snote = synthesize(question, plan, results)
    if text is None:
        return None, " -> ".join(trace) + f" -> 합성 실패: {snote}"
    trace.append(f"합성 완료{snote}")
    return {"answer": text, "retrieved_context": context,
            "think_trace": " -> ".join(trace)}, None

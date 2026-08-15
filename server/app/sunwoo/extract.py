import json
from pathlib import Path

from hcx import call_hcx

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "slot_cache.json"

ALIAS = {"현대차": "현대자동차", "KT": "케이티", "엔씨소프트": "NC",
         "LIG넥스원": "LIG디펜스앤에어로스페이스", "삼성화재": "삼성화재해상보험",
         "LS ELECTRIC": "엘에스일렉트릭"}

EXTRACT_SYSTEM = (
    "너는 금융 공시 질의 분석기다. 질문에서 정보를 추출해 JSON 하나만 출력하라. "
    "설명 문장, 마크다운, JSON 외 텍스트 절대 금지. 형식: "
    '{"corps": ["기업명"], "year": 연도정수또는null, "qtype": 1~6, "item": "찾는 항목"} '
    "연도가 둘 이상이면 year에 리스트로 넣어라. "
    "qtype 기준: 1=단일 정보 검색(값 하나), 2=단일 문서 정리·요약, "
    "3=서로 다른 기업 두 곳을 비교, 4=여러 건 수집·분류, 5=사건 연결(체결·해지 등 이력), "
    "6=같은 기업의 서로 다른 연도를 비교·서술. "
    "기업이 하나뿐이고 연도가 둘이면 3이 아니라 6이다."
)


def parse_json(text):
    for tail in ("", "}", '"}', '"]}', "]}"):
        try:
            data = json.loads(text.rstrip().rstrip(",") + tail)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "qtype" in data:
            return data
    return None


def strip_fence(text):
    """```json ... ``` 포장을 벗긴다.

    펜스와 JSON이 한 줄로 붙어 오는 경우가 있어 split 결과를 그대로 인덱싱하면
    IndexError로 죽는다. 그 예외는 parse_json 앞에서 터지므로 꼬리 복구도 재시도도
    작동하지 못한다.
    """
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    parts = t.split("\n", 1)
    t = parts[1] if len(parts) > 1 else t.lstrip("`")
    return t.rsplit("```", 1)[0].strip()


def _load_cache():
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_CACHE = _load_cache()


def _save_cache():
    try:
        CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


def extract(question, _retry=True, use_cache=True):
    """질문에서 슬롯을 뽑는다. 같은 질문은 캐시에서 돌려준다.

    HCX는 temperature 0에서도 같은 질문에 다른 JSON을 준다. 실측으로 같은 문항을
    세 번 돌렸을 때 슬롯이 달라져 검색 후보와 채점 결과가 뒤집혔다. 캐시가 없으면
    코드 수정의 효과와 모델 흔들림을 구분할 수 없다. 운영에서도 같은 질문에 같은
    답을 주는 편이 낫고 호출도 아낀다.
    """
    if use_cache and question in _CACHE:
        return dict(_CACHE[question])

    messages = [{"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": question}]
    text, note = call_hcx(messages, max_tokens=200)
    if text is None:
        return {"error": "슬롯 추출 호출 실패", "raw": note}

    slots = parse_json(strip_fence(text))
    if slots is None and _retry:
        return extract(question, _retry=False, use_cache=use_cache)
    if slots is None:
        return {"error": "JSON 파싱 실패", "raw": text}

    raw = slots.get("corps") or []
    if isinstance(raw, str):
        raw = [raw]
    slots["corps"] = [ALIAS.get(str(c).strip(), str(c).strip()) for c in raw if c]

    if use_cache:
        _CACHE[question] = slots
        _save_cache()
    return slots

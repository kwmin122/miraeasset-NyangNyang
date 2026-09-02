
import threading
import time
from pathlib import Path

import requests

import os

ROOT = Path(__file__).resolve().parent


def _up(rels):
    """상위 폴더를 거슬러 올라가며 상대 경로를 찾는다.

    개발 폴더와 팀 레포는 깊이가 다르다. 후보를 몇 개 열거해 두면 배치가
    한 칸만 달라져도 키를 못 찾는다.
    """
    here = ROOT.resolve()
    for base in _bounded(here):
        for rel in rels:
            p = base / rel
            if p.exists():
                return p
    return None


def _bounded(here, max_up=3):
    """올라갈 범위를 프로젝트 안으로 묶는다.

    상한 없이 조상을 다 훑으면 드라이브 루트까지 올라간다. 그러면 이 레포와
    무관한 C:/Users/<계정>/.env 를 집어 그 안의 키로 외부 API를 호출하게 된다.
    공용 계정이나 CI에서는 남의 자격증명을 쓰는 셈이고, 어느 파일을 골랐는지
    로그도 안 남아서 알아챌 방법이 없다.

    .git이 있는 폴더(레포 루트)에서 멈추고, 못 찾으면 세 칸까지만 올라간다.
    서버 배치(server/app/sunwoo)에서 레포 루트까지가 정확히 세 칸이다.
    """
    out = [here]
    for p in here.parents[:max_up]:
        out.append(p)
        if (p / ".git").exists():
            break
    return out


def _from_env_file(p):
    """.env에서 CLOVA_API_KEY 값을 꺼낸다. 줄 끝 주석은 떼어낸다."""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("CLOVA_API_KEY"):
                v = line.split("=", 1)[1] if "=" in line else ""
                return v.split("#")[0].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _load_key():
    """환경변수 -> .env -> 키 파일 순으로 찾는다.

    배포 환경에는 키 파일을 두지 않는 것이 정석이라 환경변수를 먼저 본다.
    팀 레포는 키를 .env에 두는데, 이 모듈이 서버를 거치지 않고 직접 실행될 때도
    있어서(평가 하네스 등) .env를 직접 읽는 경로를 하나 더 둔다.
    """
    k = os.environ.get("CLOVA_API_KEY", "").strip()
    if k:
        return k
    p = _up([".env"])
    if p:
        k = _from_env_file(p)
        if k:
            return k
    p = _up(["mini-rag/api_key.txt", "api_key.txt"])
    return p.read_text(encoding="utf-8").strip() if p else ""


API_KEY = _load_key()
URL = ("https://clovastudio.stream.ntruss.com/v3/chat-completions/"
       + os.environ.get("HCX_MODEL", "HCX-005"))

TIMEOUT = (5, 60)

# requests의 timeout은 (연결, 바이트 사이 간격)이지 전체 시간 상한이 아니다.
# 엔드포인트가 스트리밍(clovastudio.stream)이라 서버가 60초 안에 조금씩만 보내면
# 한 호출이 무한정 늘어난다. 실측으로 한 문항이 16,236초(4.5시간) 걸렸다.
# 평가는 순차 호출이라 그 한 건이 전체를 멈춰 세운다. 벽시계 상한을 따로 건다.
WALL_LIMIT = float(os.environ.get("HCX_WALL_LIMIT", "60"))

RETRY_STATUS = (429, 500, 502, 503, 504)

# 429는 분당 쿼터라 1.5초로는 안 풀린다. 58문 실측(2026-08-30)에서 3개 문항이
# 429를 맞았고 재시도 3회(1.5s+3s)가 전부 다시 429를 받아 2문항이 통째로 죽었다.
# (TR-NAME-001, T4-O-001 — 둘 다 "답변을 생성하지 못했습니다"로 집계돼 -2점)
# 429만 백오프를 길게 잡는다. 다른 사유(5xx·네트워크)는 짧은 재시도가 맞다.
# 백오프 총량은 지연과 맞바꾸는 값이다. SPEC 은 p95 < 20s 를 목표로 잡고
# "응답 속도가 평가 기준일 수 있음"이라 적어뒀다. (4,9,15)=28s 로 재보니
# 429 구간에서 문항 지연이 62초까지 갔다. 회수는 유지하되 최악값을 묶는다.
BACKOFF_429 = (3.0, 7.0, 12.0)
BACKOFF_OTHER = (1.5, 3.0, 4.5)

# 요청 하나의 전체 예산. WALL_LIMIT 은 '호출 하나'의 상한이라 한 문항이 계획·폴백·
# 합성으로 여러 번 부르면 그 배수만큼 늘어난다. 실측(2026-08-31)에서 한 문항이
# 1,056초(17.6분) 걸렸다 — 개별 호출은 다 60초 안이었는데 전체를 묶는 상한이 없었다.
# 평가는 순차 호출이라 한 문항이 늘어지면 뒤가 전부 밀린다.
REQUEST_LIMIT = float(os.environ.get("HCX_REQUEST_LIMIT", "90"))
_req = threading.local()   # 서버가 요청을 스레드풀에서 돌리므로 스레드별로 둔다


def start_request(limit=None):
    """이 요청의 데드라인을 건다. 파이프라인 입구에서 한 번 부른다."""
    _req.deadline = time.monotonic() + (REQUEST_LIMIT if limit is None else limit)


def request_left():
    """남은 요청 예산(초). 데드라인을 안 걸었으면 None."""
    dl = getattr(_req, "deadline", None)
    return None if dl is None else dl - time.monotonic()


def _nap(sched, attempt):
    return sched[min(attempt, len(sched) - 1)]


def _post_bounded(messages, max_tokens, temperature, wall):
    """requests.post를 데몬 스레드에서 돌리고 벽시계로 끊는다.

    끊긴 스레드는 남지만 데몬이라 프로세스 종료를 막지 않고, 결과는 버린다.
    반환 (응답 or None, 예외 or None, 시간초과 여부)
    """
    box = {}

    def run():
        try:
            box["res"] = requests.post(
                URL,
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"messages": messages, "maxTokens": max_tokens,
                      "temperature": temperature},
                timeout=TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001 - 스레드 밖으로 넘겨 호출부가 판단
            box["err"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(wall)
    if th.is_alive():
        return None, None, True
    return box.get("res"), box.get("err"), False


def call_hcx(messages, max_tokens=1000, temperature=0, retries=3, wall=None):
    """HCX를 부르고 (본문, 비고)를 돌려준다. 실패해도 예외를 던지지 않는다.

    반환
      (text, note)  성공. note는 빈 문자열이거나 잘림 경고
      (None, note)  실패. note에 사유가 들어간다. 호출한 쪽이 그대로 trace에 붙이면 된다

    재시도는 일시적 오류(429·5xx·네트워크)에만 적용한다.
    401(키 오류)이나 400(요청 형식 오류)은 몇 번을 다시 걸어도 같으므로 즉시 포기한다.
    """
    last = ""
    budget = wall if wall is not None else WALL_LIMIT
    # 요청 전체 예산이 걸려 있으면 그보다 길게 잡지 않는다.
    _left = request_left()
    if _left is not None:
        if _left <= 1:
            return None, "API 호출 실패(요청 예산 초과)"
        budget = min(budget, _left)
    started = time.monotonic()
    for attempt in range(retries + 1):
        left = budget - (time.monotonic() - started)
        if left <= 1:
            return None, f"API 호출 실패(시간초과 {budget:.0f}s)"
        res, err, timed_out = _post_bounded(messages, max_tokens, temperature, left)
        if timed_out:
            # 재시도해도 같은 프롬프트라 또 늘어질 가능성이 높다. 남은 예산으로만 판단한다.
            return None, f"API 호출 실패(응답 지연 {budget:.0f}s 초과)"
        if err is not None:
            last = f"네트워크 {type(err).__name__}"
            nap = _nap(BACKOFF_OTHER, attempt)
            if attempt < retries and (budget - (time.monotonic() - started)) > nap + 2:
                time.sleep(nap)
                continue
            return None, f"API 호출 실패({last})"

        if res.status_code in RETRY_STATUS and attempt < retries:
            last = f"HTTP {res.status_code}"
            nap = _nap(BACKOFF_429 if res.status_code == 429 else BACKOFF_OTHER, attempt)
            # 남은 예산이 백오프보다 짧으면 헛되이 자지 않고 바로 포기한다.
            # (플래너는 wall=20으로 부르므로 여기서 자면 폴백 경로까지 굶는다)
            if (budget - (time.monotonic() - started)) <= nap + 2:
                return None, f"API오류 {res.status_code}"
            time.sleep(nap)
            continue

        if res.status_code != 200:
            return None, f"API오류 {res.status_code}"

        try:
            body = res.json()["result"]
        except (ValueError, KeyError, TypeError):
            return None, "응답 파싱 실패"

        text = (body.get("message") or {}).get("content")
        if not text:
            return None, "빈 응답"

        note = ""
        if body.get("stopReason") == "length":
            note = " -> [경고] maxTokens 도달로 답변이 잘렸을 수 있음"
        return text, note

    return None, f"재시도 실패({last})"

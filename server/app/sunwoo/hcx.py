
import threading
import time
from pathlib import Path

import requests

import os

ROOT = Path(__file__).resolve().parent


def _find(cands):
    """여러 후보 경로 중 실제로 있는 것을 고른다.

    이 모듈은 두 트리에서 돌아야 한다. 개발용 폴더(미래에셋증권 대회/agent)와
    팀 레포(miraeasset-NyangNyang/server/app)는 데이터 위치가 다르다.
    한쪽에 맞추면 다른 쪽에서 import 단계에 죽는다.
    """
    for p in cands:
        if p.exists():
            return p
    return None


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
WALL_LIMIT = float(os.environ.get("HCX_WALL_LIMIT", "45"))

RETRY_STATUS = (429, 500, 502, 503, 504)


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


def call_hcx(messages, max_tokens=1000, temperature=0, retries=2, wall=None):
    """HCX를 부르고 (본문, 비고)를 돌려준다. 실패해도 예외를 던지지 않는다.

    반환
      (text, note)  성공. note는 빈 문자열이거나 잘림 경고
      (None, note)  실패. note에 사유가 들어간다. 호출한 쪽이 그대로 trace에 붙이면 된다

    재시도는 일시적 오류(429·5xx·네트워크)에만 적용한다.
    401(키 오류)이나 400(요청 형식 오류)은 몇 번을 다시 걸어도 같으므로 즉시 포기한다.
    """
    last = ""
    budget = wall if wall is not None else WALL_LIMIT
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
            if attempt < retries and (budget - (time.monotonic() - started)) > 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None, f"API 호출 실패({last})"

        if res.status_code in RETRY_STATUS and attempt < retries:
            last = f"HTTP {res.status_code}"
            time.sleep(1.5 * (attempt + 1))
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


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
    for base in [here, *here.parents]:
        for rel in rels:
            p = base / rel
            if p.exists():
                return p
    return None


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

RETRY_STATUS = (429, 500, 502, 503, 504)


def call_hcx(messages, max_tokens=1000, temperature=0, retries=2):
    """HCX를 부르고 (본문, 비고)를 돌려준다. 실패해도 예외를 던지지 않는다.

    반환
      (text, note)  성공. note는 빈 문자열이거나 잘림 경고
      (None, note)  실패. note에 사유가 들어간다. 호출한 쪽이 그대로 trace에 붙이면 된다

    재시도는 일시적 오류(429·5xx·네트워크)에만 적용한다.
    401(키 오류)이나 400(요청 형식 오류)은 몇 번을 다시 걸어도 같으므로 즉시 포기한다.
    """
    last = ""
    for attempt in range(retries + 1):
        try:
            res = requests.post(
                URL,
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"messages": messages, "maxTokens": max_tokens,
                      "temperature": temperature},
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            last = f"네트워크 {type(e).__name__}"
            if attempt < retries:
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

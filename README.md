# 공시 Agent 서버 (미래에셋증권 AI Festival)

평가 API 서버 + 평가 질의셋. 담당: 민경욱.

## 평가용 API End-point (주최측 제출)

```
GET http://49.50.143.160/answer
```

| 항목 | 값 |
|---|---|
| End-point | `http://49.50.143.160/answer` |
| 프로토콜 / 포트 | HTTP / 80 (표준 포트 — 주소에 포트 표기 불필요) |
| 메서드 | GET |
| 쿼리 파라미터 | `question_id` (질의 ID), `question` (평가 질의 원문) |
| 응답 | `application/json`, 5필드 **전부 문자열** |
| 인증 헤더 | 없음 (주최측 안내대로 미사용) |

**호출 예시**

```bash
curl -G "http://49.50.143.160/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=SK하이닉스의 신규시설투자 금액은?"
```

```python
import requests
r = requests.get("http://49.50.143.160/answer",
                 params={"question_id": "Q-001", "question": "평가 질의"}, timeout=300)
print(r.json())   # question_id, question, retrieved_context, think_trace, answer
```

**응답 형태**

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "[근거 1] 보고서명 (접수일) | 기업명 | 접수번호 | 섹션\n본문...\n\n[근거 2] ...",
  "think_trace": "[플래너] 계획 1단계 통과 -> ... -> 합성 완료",
  "answer": "최종 생성 답변"
}
```

- `retrieved_context`는 여러 근거를 `[근거 n] 머리말 + 본문` 형식으로 이어붙인 **하나의 문자열**이다 (배열 아님).
- 질의가 코퍼스 범위(2023-01~2026-03) 밖이거나 해당 공시가 없어도 **200 + 5필드**를 반환한다 (거절 사유를 `answer`에 담는다).
- 상태 확인용 보조 엔드포인트: `GET /health` (에이전트 모드 확인), `GET /ready` (워밍업 완료 여부).

**S8v2 배포 실측 (2026-09-04, NCP c2-g3a 2vCPU/4GB x86_64, 임베딩 v2 / 258,459청크)**

| 항목 | 실측 |
|---|---|
| dev 58문 정답률 (외부 경유 채점) | **58/58 (100%)** |
| 최대 응답 지연 | 39.9s (주최측 타임아웃 300s 대비 여유) |
| 콜드 스타트 워밍업 | 64s |
| 재부팅 자동 복구 | `/health` 36s, `/ready` 87s (S7 실측 — `--restart unless-stopped` + `@reboot` 설정이 동일해 재측정하지 않았다) |
| 컨테이너 메모리 | 58문 채점 직후 1.87GB / 3.32GB (OOM 없음, 스왑 2GB 대기) |

## 대용량 제출물 — 임베딩·인덱스 (Google Drive)

과제소개자료 p.8 제출 채널 안내("대용량 제출물은 압축 파일로 범용 클라우드 스토리지
업로드 후 다운로드 공유 링크 제출")에 따라, 8GB급 임베딩 산출물은 git이 아닌 아래
링크로 제출한다. GitHub는 100MB를 넘는 단일 파일을 받지 않는다.

**https://drive.google.com/drive/folders/1zhb-HF2fI0vGDEOwWY-q5Y2d9R6SveJJ**

| 파일 | 내용 |
|---|---|
| `share_embeddings_v2.zip` | 임베딩·인덱스 산출물 압축본 |
| 사용법 (Markdown) | 압축 해제 후 배치 경로와 사용 방법 |

배치 경로는 아래 [처음 세팅](#처음-세팅-팀원-온보딩--위에서-아래로-그대로) 2번과 같다.
**평가용 API 서버(`http://49.50.143.160/answer`)에는 이 v2 산출물이 배치되어 동작 중이므로
(2026-09-04 교체, 외부 경유 58/58 확인), 평가를 위해 별도로 내려받을 필요는 없다.**
재현·검증용 자료다.

## 팀원용 — 지금 상태와 각자 할 일

**현재 상태판은 [`docs/CONTEXT.md`](docs/CONTEXT.md) 하나만 보면 된다** (뭐가 동작하고, 뭐가 문제고, 왜 그렇게 결정했는지). 요약:

- 서버는 4GB 대회 스펙에서 검증 완료, 평가셋 58문 + 자동 채점기 완비. HCX 연동 기준선 26/38 (S5b, S11·S12 신규 20문 미측정 — 폴백은 17/38).
- **선우**: 아래 [에이전트 모드](#에이전트-모드-env의-agent_mode)의 계약대로 `sunwoo_agent.py`를 구현하면 꽂힌다. 완성되면 같은 58문으로 baseline과 성적 대조 → 높은 쪽으로 제출. 튜닝은 `evalset/questions_v1.jsonl`(dev 58문)로만 — **별도 blind 14문이 비공개로 있고(과적합 검증용, 민경욱 보관) 제출 모드 결정 때만 투입된다.** S10 고난도 8문에 이어 S11·S12에서 복합질의 20문 추가(전건조회+최대값·recency·조건필터·동일일자 분리·시계열비교·교차비교·정정추적) — v2 튜닝은 이 복합질의가 핵심.
- **명섭**: `docs/CONTEXT.md`의 "알려진 문제" #1~#3이 명섭 몫 — ① LIG넥스원 옛 사명 검색 실패 ② chunk_id 중복 11,048건 ③ correction_map `superseded_by` 리스트값 599건.
- 코퍼스·임베딩(`data/`)은 git에 없다 — 위 [대용량 제출물](#대용량-제출물--임베딩인덱스-google-drive) 링크에서 받아 아래 경로에 복사.

## 처음 세팅 (팀원 온보딩 — 위에서 아래로 그대로)

**1. 받아오기** (private 레포 — GitHub 초대 수락 후)

```bash
git clone https://github.com/kwmin122/miraeasset-NyangNyang.git
cd miraeasset-NyangNyang
```

**2. 데이터 넣기** — git에 없음(8GB), 각자 가진 것을 아래 구조 그대로 배치. 폴더 이름·구조가 다르면 서버가 못 찾는다.

```
miraeasset-NyangNyang/
└── data/
    ├── corpus/             ← 대회 제공 공시 원문 XML (5.2GB)
    └── share_embeddings/   ← 명섭 산출물: 임베딩·FAISS 인덱스 (2.9GB)
```

명섭이 배포한 폴더 구조 그대로면 된다. `data/`는 **읽기 전용** — 수정·삭제·이동 금지.

`search_patch.py`(검색 보정 패치)는 명섭 산출물이 아니라 민경욱 작성분이라 명섭 배포본에 없을 수 있는데, **레포의 `server/vendor/`에 동봉돼 있어 없어도 자동으로 동작한다**. 서버가 `No module named 'search_patch'`로 죽으면 레포가 옛 버전인 것 — `git pull` 하면 해결.

**3. 환경 세팅 + 실행** (Python 3.11)

```bash
uv venv --python 3.11 && uv pip install -r server/requirements.txt
cp .env.example .env
```

`.env`를 열어 `AGENT_MODE=mock` → **`AGENT_MODE=baseline`으로 변경** (mock은 더미 응답이라 실검색이 안 됨). `CLOVA_API_KEY`는 비워둬도 동작한다(추출형 폴백).

```bash
cd server && ../.venv/bin/uvicorn app.main:app --port 8000
```

**4. 제대로 됐는지 확인 (이게 핵심)**

```bash
curl http://localhost:8000/ready   # {"status":"ready"} 나올 때까지 ~20초
# 레포 루트에서, 서버 띄운 채로:
python3 evalset/run_eval.py
```

채점 합계가 **17/30**(HCX 미연동 폴백 기준선 — 최신 수치는 `docs/CONTEXT.md`)이면 민경욱 환경과 동일하게 세팅된 것. 숫자가 다르거나 `/ready`가 계속 503이면 십중팔구 `data/` 경로·구조 문제다.

## API (대회 규격)

`GET /answer?question_id={id}&question={질의}` → 항상 200:

```json
{"question_id": "...", "question": "...", "retrieved_context": "...", "think_trace": "...", "answer": "..."}
```

모든 요청/응답은 `logs/requests.jsonl`에 기록된다 (평가 기간에 실제 평가 질의가 여기 쌓임).

## 에이전트 모드 (`.env`의 AGENT_MODE)

| 모드 | 내용 |
|---|---|
| `mock` | 스키마만 맞춘 더미 응답 (서버 개발용) |
| `baseline` | PatchedSearcher top-k → HCX-005 합성 (키 없으면 추출형 폴백) |
| `sunwoo` | **강선우 에이전트 모듈** — `server/app/sunwoo_agent.py`에 아래 계약으로 구현해 넣으면 연결됨 |

```python
# server/app/sunwoo_agent.py
class SunwooAgent:
    def answer(self, question: str) -> dict:
        return {"answer": str, "retrieved_context": str, "think_trace": str}
```

검색은 반드시 `search_patch.py`의 `PatchedSearcher.search(query, k)` 경유
(Nemotron "query: " 프리픽스 자동 처리, 뷰어청크 제거, 정정공시 최신본 판정 포함).
파일은 레포 `server/vendor/`에 동봉 — `data/share_embeddings/`에 사본이 있으면 그쪽이 우선.

## 평가셋

```bash
# 서버 띄운 상태에서
python3 evalset/run_eval.py            # questions_v1.jsonl 전체 채점
```

- `evalset/questions_v1.jsonl` — dev 58문, 전부 코퍼스 원문으로 골드 검증 (6유형·8평가기준·함정 7종 커버: 기간밖/상장전/정정공시/사명변경/프롬프트공격/부재/혼합)
- blind 14문은 git·이 파일 목록에 없음 (선우 튜닝 비노출 — 과적합 방지 홀드아웃)
- 유형별 정답률·근거 recall·지연시간 요약 출력, 상세는 `results_*.jsonl`

## 배포 (NCP)

2vCPU/4GB (주최측 권장 스펙), Docker + 볼륨 마운트, HTTP 우선 (HTTPS는 주최측 재공지 대기).
자세한 계획: `docs/PLAN.md`, 경계 계약: `docs/SPEC.md`. 사수 기간 매뉴얼: `docs/RUNBOOK.md`.
작업 방식(Fable=계획·검증/Sonnet=구현): `docs/WORKFLOW.md`, 작업 큐: `docs/SLICES.md`, 일지: `docs/LOG.md`.

### 서버에는 `data/`를 통째로 올리지 않는다 (S7에서 확정)

로컬 `data/`는 8.1GB지만 **서버에 필요한 건 3.2GB뿐**이다. 서버 디스크가 30GB라
`docker build` 중에 꽉 차는 게 실제 위험이었고, 나머지는 실행 중에 아무도 읽지 않는다.

| 파일 | 로컬 | 서버 | 왜 |
|---|---|---|---|
| `data/corpus/**.xml` (공시 원문) | 5.2GB | **안 올림** | 실행 중 XML을 여는 코드가 한 줄도 없다. 공시 본문은 이미 `chunk_meta.jsonl` 안에 들어 있다 |
| `data/corpus/manifest.jsonl` | 2.4MB | 올림 | `server/app/sunwoo/slice4.py`가 공시 목록을 여기서 읽는다 |
| `data/share_embeddings/out/chunk_meta.jsonl` | 1.05GB | 올림 | 검색 결과의 본문(258,459청크). `search_patch.py`의 `Resolver`가 통째로 훑는다 |
| `data/share_embeddings/out/correction_map.json` | 0.7MB | 올림 | 정정공시 계보 판정 |
| `data/share_embeddings/out/index.faiss` | 2.1GB | **안 올림** | `server/artifacts/index_sq8.faiss`(0.53GB, 같은 인덱스의 압축본)로 대체된다 |
| `data/share_embeddings/search_lib.py`, `search_patch.py` | 8KB | 올림 | 검색 라이브러리 본체 |
| HF 모델 캐시 `models--nvidia--Nemotron-3-Embed-1B-BF16` | 2.1GB | 올림 | 질문을 벡터로 바꾸는 임베딩 모델. 미리 올려두고 `HF_HUB_OFFLINE=1`로 고정 — 심사 중 다운로드 실패 여지를 없앤다 |

**주의 — 서버에서 공시 XML이 안 보인다고 "빠졌네" 하고 채워 넣지 말 것.** 의도적으로 뺀 것이다.
`index.faiss`도 마찬가지다. 되돌리려면 `server/artifacts/index_sq8.faiss`를 지워야
`server/app/search.py`의 폴백이 원본 인덱스를 다시 찾는다.

### 임베딩을 새 버전으로 교체할 때 (명섭 → 선우 → 민경욱 순서)

임베딩은 공시 4,204건을 검색 가능한 형태로 미리 변환해 둔 색인이다. **만드는 건 명섭,
쓰는 건 선우, 제출 서버에 올리는 건 민경욱**이다. 새 임베딩이 와도 바로 서버에 넣지 않는다:

1. 명섭이 새 임베딩을 공유한다
2. **선우가 로컬에서 dev 58문을 다시 돌려 점수가 떨어지지 않는지 확인한다**
3. 통과하면 민경욱이 서버 파일을 교체하고 `docker restart` → `/ready` 확인
4. 통과 못 하면 **올리지 않는다.** 현재 서버는 v2 임베딩(258,459청크) 기준이고, 58/58도 그 기준이다

검증 안 된 임베딩을 올리면 점수가 떨어져도 원인을 모른다. 9/6 프리즈 후에는 교체 자체가 금지다.

**주의:** 9/6 제출 프리즈 후 재배포 금지, 9/7~9/20 서버 사수, 9/30 전 NCP 리소스 전부 삭제.

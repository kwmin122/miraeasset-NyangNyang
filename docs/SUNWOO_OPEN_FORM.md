# Open형 방어 설계 (Open-Form Defense)

> **대상 코드베이스:** `server/app/sunwoo/` (extract.py / pipeline.py / slice1.py / slice3.py / slice4.py / slice5.py / slice6.py / verify.py) + `data/share_embeddings/search_lib.py`
> **작성 기준일:** 2026-08-09 · **예선 마감:** 9/6
> **전제 확인 완료:** 전 slice `temperature=0` (재현성 선결 조건 충족) / `verify.check_dates`에 8자리 날짜 패턴 반영됨 / `slice4`만 `[근거 idx/n]` 번호표 보유

---

## 1. 문제 정의

### 1.1 실측된 세 가지 실패

**(A) 근거 밖 서사 조작 — 유형⑥**

질문은 "LG에너지솔루션 2023년과 2024년 매출액을 비교하고 변화 배경을 설명해줘"였다. 수치는 완벽했다. 2023년 33조7455억, 2024년 25조6196억, 차이 8조1259억이 전부 원문과 일치했다. 문제는 배경 서술이었다. 답변은 "전기차 수요 둔화, 고금리·고물가로 소비 심리 위축, 리튬·니켈 등 원자재 가격 하락"이라고 썼다. 투입된 근거와 문자열로 대조한 결과 실존하는 표현은 '메탈 가격'과 '북미' 둘뿐이었고, '전기차 수요', '수요 둔화', '고금리', '고물가', '구매 심리', '니켈', '원자재 가격'은 전부 근거에 없었다. HCX-005가 근거의 진짜 사실 하나에 자기 세계 지식을 엮어 그럴듯한 서사를 만든 것이다. "영향을 미쳤을 가능성이 있습니다"라는 추측 어투가 그 신호였다.

**(B) 과잉 기권 — 같은 질문, 프롬프트 강화 후**

(A)를 막으려고 SYSTEM6에 금지 항목을 열거하고, 추측 어투를 금지하고, 문장별 출처 표기를 강제했다. 그랬더니 모델이 배경 전체를 "공시에서 확인되지 않습니다"로 포기했다. 근거에 '메탈 가격 하락'과 '북미 수요'가 멀쩡히 실존하는데도 쓰지 않았다. 원인 가설은 단순하다. 근거를 서술하는 비용(정확 인용·출처 표기·금지어 회피)만 올리고 기권의 비용은 0으로 뒀기 때문에, 모델이 가장 싼 길을 택한 것이다.

**(C) 귀속(attribution) 부재 — 유형②**

"LG에너지솔루션의 2024년 주요 사업 내용을 설명해줘"에 대한 답변에 "글로벌 에너지 솔루션 산업의 선도 기업으로 자리매김", "지속 가능성을 추구" 같은 홍보성 서술이 다수 들어갔다. 코퍼스 전체를 검색하면 유사 문구가 실존한다('선도기업' 21청크, '지속가능' 22청크). 그러나 **실제로 투입된 5개 청크에 있었는지는 확인할 수단이 없었다.** 답변 문장과 근거 청크를 잇는 연결선이 코드 어디에도 없기 때문이다.

### 1.2 지금 방어선이 못 막는 이유

현 파이프라인의 방어선은 세 개이고, 셋 다 (A)(B)(C)와 축이 다르다.

| 기존 방어선 | 파일·함수 | 막는 것 | (A)(B)(C)를 못 막는 이유 |
|---|---|---|---|
| 개수 계약 | `slice4.py` user 메시지 `"총 {n}건 … 전부 반영하라"` | 근거 **누락**(재현율) | 셀 수 있는 단위가 있을 때만 작동한다. 배경 서술은 "몇 건"이라는 단위가 없어 계약을 걸 대상 자체가 없다. |
| 정정 체인 그룹핑 | `slice4.py` `group_chains()` | 근거 안의 **폐기값** 인용 | 컨텍스트 진입을 통제하는 장치다. 컨텍스트에 아예 없는 세계 지식이 답변에 들어오는 것은 통제 범위 밖이다. |
| 날짜 실존 대조 | `verify.py` `check_dates()` | 근거에 없는 **날짜** | 정규식이 날짜만 본다. (A)의 미실존 표현 7개는 전부 날짜가 아니어서 무탐지였다. |

핵심은 이것이다. 세 방어선 모두 **셀 수 있는 것 또는 정규식으로 뽑히는 것**을 막는다. Open형의 서술 부분은 세지도 못하고 정규식에도 안 걸린다. 그래서 프롬프트로 막으려 했고, 그 결과가 (B)다.

### 1.3 이 문서가 세우는 명제

> **환각(A)과 과잉 기권(B)은 같은 손잡이의 양끝이다. 프롬프트로 손잡이를 어느 쪽으로 밀어도 반대쪽이 튄다. 진자를 세우려면 "어디까지 말할지"의 결정권을 모델에서 코드로 옮겨야 한다. 그리고 그 코드가 옳게 결정하는지는 반드시 측정한 뒤에 켜야 한다.**

이 명제에서 세 가지 설계 원칙이 나온다.

1. **추가 LLM 호출 0회를 기본값으로 한다.** 응답 속도가 평가에 반영될 수 있고 HCX 호출은 1회당 지연이 실재한다. 호출이 드는 처방은 전부 조건부·후순위다.
2. **생성 측과 검증 측을 반드시 한 세트로 배포한다.** 생성 프롬프트를 완화(기권 비용 인상)하면서 검증 코드를 안 넣으면 (A)가 재발하고, 검증만 넣고 프롬프트를 그대로 두면 (B)가 남는다. 순서가 생명이다.
3. **삭제·차단은 오탐률을 측정한 뒤에만 켠다.** 검증기의 오탐은 곧 (B)의 코드 버전이다. v1은 전부 경고 모드로 배포한다.

---

## 2. 해결 아키텍처 — 4단 방어선

### 2.1 전체 흐름도

```
[질문]
  |
  v
extract.py  extract()            슬롯 {corps, year, qtype, item}
  |
  v
main.py  answer_question()       qtype 라우팅
  |
  +---------------------------------------------------------------+
  |                    ★ 1단 — 생성 예방 (Prevention)               |
  |  slice1/3/6 컨텍스트 조립                                       |
  |   0) 근거 번호표 통일  [근거 i/N]         <- slice4 방식 이식     |
  |   1) slice6: gather_bg() -> pick_cause_sentences()             |
  |        원인 후보 문장 화이트리스트 (B1)(B2)...                   |
  |   2) 충분성 게이트 bg_sufficiency()                             |
  |        충분 -> "기권 금지" 계약 / 불충분 -> "추측 금지 + 역질문"   |
  |   3) SYSTEM6 대칭 채점표 (근거 초과 = 근거 방기 = 같은 오답)       |
  |   4) 문단 단위 (근거 N) 표기 요구  <- 문장 단위 강제는 (B)의 원인  |
  |   5) slice1 유형②: [검토]/[답변] 읽기 노트 (같은 1회 호출 안에서) |
  +---------------------------------------------------------------+
  |                       HCX-005 호출 (1회, 기존과 동일)
  v
  +---------------------------------------------------------------+
  |                    ★ 2단 — 사후 귀속 판정 (Detection)            |
  |  main.clean(r, qtype, question)                                |
  |   attribute.py  attribute()      문장 x 근거 트리아지            |
  |        1차 어휘: 조사절단 토큰중첩 + 문자 바이그램 Dice (비용 0)   |
  |        2차 임베딩: 1차 미해결 문장만 s.model 배치 인코딩 (조건부)  |
  |        -> 문장별 라벨 {지지 / 애매 / 미검증} + 최적 근거ID         |
  |   verify.py  check_dates()  (기존)                              |
  |             check_facts()   수치 정규화 + 배경문장 내용어 대조     |
  |             check_promo()   홍보 상용구 x 귀속 교차 판정          |
  |             check_citation_format()  유령 근거번호 검출          |
  |             tag_abstention() none/partial/full                 |
  |   -> 전부 think_trace 경고. v1은 answer를 건드리지 않는다.        |
  +---------------------------------------------------------------+
  |
  v
  +---------------------------------------------------------------+
  |          ★ 3단 — 교정 (Correction) [평가셋 후에만 활성화]         |
  |   revise.py  targeted_revise()                                 |
  |     미지지 문장만 제거 -> 근거 실존 문장을 재료로 1회 재작성        |
  |     재검증 실패 시 원본 유지 (실패 비용 0)                        |
  |     재료 자체가 없으면 부분 기권 문구로 마감 (전면 기권 금지)       |
  +---------------------------------------------------------------+
  |
  v
[제출 스키마] {question_id, question, retrieved_context, think_trace, answer}
  |
  +---------------------------------------------------------------+
  |              ★ 4단 — 계측 (Measurement) [오프라인]              |
  |   eval/run_eval.py     18문항 배치 실행 + 지연 기록              |
  |   eval/score_nuggets.py  vital 너겟 재현율 V_recall             |
  |   attribution.py       문장 라벨 TSV -> 사람 라벨링              |
  |   eval/stats.py        McNemar + 문항 군집 표준오차              |
  |   -> 2x2 스코어카드: [근거있음 x 기권]=(B) / [근거없음 x 서술]=(A) |
  +---------------------------------------------------------------+
```

### 2.2 단계별 요약

| 단 | (a) 무엇을 하는가 | (b) 논문 근거 | (c) 파일 | (d) 추가 비용 |
|---|---|---|---|---|
| **1단 생성 예방** | 코드가 원인 후보 문장을 추려 넣고, 기권 조건을 계약으로 박고, 근거 초과와 근거 방기에 같은 벌점을 명시한다. 유형②는 청크별 읽기 노트를 같은 호출 안에서 먼저 쓰게 한다. | Kalai et al. Observation 1(채점 비대칭) / Joren et al. Sufficient Context(기권 판단의 외부화) / RECOMP(추출형 압축) / Chain-of-Note(읽기 노트) / According-to prompting | `slice1.py` `slice3.py` `slice6.py` | **LLM 호출 +0회.** 프롬프트 입력 토큰 +500~800자, 출력 토큰 +노트 5줄(유형②만) |
| **2단 사후 귀속 판정** | 답변을 문장 단위로 쪼개 실제 투입된 청크와 대조해 지지/애매/미검증 라벨과 근거ID를 붙인다. 수치·홍보 상용구·유령 근거번호를 별도 축으로 검사한다. | ALCE(문장 단위 인용 채점) / AttrScore(Extrapolatory 유형) / SummaC(입도 불일치) / AlignScore(쪼개서 정렬) / RAGTruth(구간 판정을 LLM에 맡기지 말 것) | 신설 `attribute.py`, `verify.py` 확장, `main.py` `clean()` | **LLM 호출 +0회.** 어휘 1차는 밀리초. 임베딩 2차는 조건부 발동이며 Nemotron-1B CPU 기준 **초 단위** — 반드시 실측 후 켠다 |
| **3단 교정** | 미지지 문장만 걷어내고, 근거에 실존하는 문장을 재료로 1회 재작성한다. 재검증 실패 시 원본 유지, 재료 없으면 부분 기권. | RARR(사후 편집, 원 의도 보존) / FinGround(verify-then-ground) / CEG(미지지 문장 재생성) / Conformal Factuality(백오프) | 신설 `revise.py`, `main.py` | **조건부 LLM 호출 +1회** (미지지 검출 시에만, Open형 한정). 평균 +0.3~0.6회 추정 |
| **4단 계측** | 18문항 평가셋에 사람이 만든 너겟 정답표를 붙여 근거완전성과 환각을 한 화면에서 본다. 프롬프트 수정마다 진자가 어느 쪽으로 튀는지 수치로 확인한다. | AutoNuggetizer(vital/okay 너겟) / FActScore(정밀도만 재면 기권이 만점) / Adding Error Bars(문항 군집 SE) / With Little Power(저전력 실험의 함정) | 신설 `eval/` 폴더 | **서빙 호출 +0회.** 오프라인 배치라 지연 제약 무관 |

### 2.3 왜 4단인가 — 각 단이 막는 실패가 다르다

- 1단만 있으면 (A)가 확률적으로 새어 나온다. 프롬프트 준수율은 100%가 아니고, 우리는 이미 SYSTEM의 근거 표기 지시를 HCX가 불이행하는 것을 실측했다.
- 2단만 있으면 (B)가 그대로 남는다. 검증은 나간 답변을 채점할 뿐 안 쓴 근거를 되살리지 못한다.
- 3단은 2단의 판정 정밀도에 전적으로 의존한다. 정밀도가 낮은 상태에서 켜면 멀쩡한 문장을 지워서 (B)를 코드로 재현한다.
- 4단이 없으면 나머지 셋의 효과를 알 수 없다. 지금 (A)→(B) 진자 사고를 사후에야 눈치챈 이유가 이 계기판의 부재다.

### 2.4 상충 지점과 채택 결정

리서치 6개 각도가 정면으로 충돌한 지점이 세 곳이다. 어느 쪽을 왜 택했는지 밝힌다.

**충돌 1 — 생성 중 인용을 강제할 것인가.**
한쪽은 문장 말미 `[E1]` 마커를 전 문장에 강제하자고 했고, 다른 쪽은 LongCite를 근거로 "생성 중 인용 강제는 항상 정확성을 떨어뜨리니 사후에 붙여라"고 했다. 둘 다 LongCite를 인용하면서 결론이 반대다.
**채택:** 절충한다. ①생성 단계에서는 **문단 단위 `(근거 N)`** 만 요구한다 — 표기 비용을 문장에서 문단으로 낮춰 (B) 유발 압력을 줄인다. ②**진짜 귀속 판정은 모델의 표기를 믿지 않고 `attribute.py`가 독립 재계산한다.** 모델 표기는 힌트일 뿐 판정 근거가 아니므로, HCX가 표기를 불이행해도 검증은 전 근거 대조로 폴백해서 그대로 돈다. 문장 단위 강제는 **기각한다** — 그것이 (B)를 만든 바로 그 개입이기 때문이다.

**충돌 2 — 미지지 문장을 삭제할 것인가 재작성할 것인가.**
삭제는 호출 0회지만 논리 흐름이 끊기고 오탐 시 정보가 영구 손실된다. 재작성은 +1 호출이지만 근거 실존 표현으로 후퇴시킬 수 있다.
**채택:** 재작성을 주 경로로, 삭제를 폴백으로 둔다. 근거는 FinGround의 실측이다 — 검증 단독이 환각 68% 감소인데 재작성까지 붙이면 78% 감소다. "검증 후 삭제"가 아니라 "검증 후 재작성"이 진자 문제의 정답이라는 것이 그 논문의 직접적인 결론이다. 다만 **삭제·재작성 어느 쪽도 평가셋으로 판정 정밀도 0.9를 확인하기 전에는 켜지 않는다.**

**충돌 3 — 서빙에 형태소 분석기·NLI 모델을 넣을 것인가.**
한쪽은 kiwipiepy(수십 MB)와 klue-roberta NLI(INT8 70~130MB)를 넣자고 했고, 다른 쪽은 4GB 제약 때문에 순수 정규식으로 가자고 했다.
**채택:** **서빙에는 둘 다 안 넣는다.** TODO.md에 이미 "4GB 서버 OOM 리스크(추정 5.8GB)"가 미해결로 적혀 있다. 조사 분리는 정규식 최장일치로 근사하고, 필요하면 **오프라인 채점기에서만** 형태소 분석기를 쓴다. NLI는 도입 게이트(검증셋에서 임베딩 단독 대비 이득 실측)를 통과하지 못하면 아예 버린다.

### 2.5 과잉 기권 재발 방지 — 진자 고정 장치 (별도 취급)

이것을 별도 절로 뺀 이유는, 환각을 막다가 (B)를 재발시키면 그 순간 이 설계 전체가 실패이기 때문이다. 아래 다섯 개가 (B) 전용 안전장치이며, 하나라도 빠지면 배포하지 않는다.

1. **기권에 조건을 건다.** SYSTEM6에서 "없으면 확인되지 않음이라고 밝혀라"라는 무조건 기권 허용을 삭제하고, "원인 후보 문장이 0건일 때만 기권이 정답"으로 바꾼다. 기권의 비용을 0에서 끌어올리는 유일한 프롬프트 조치다.
2. **벌점을 대칭으로 명시한다.** "근거에 없는 것을 쓰면 오답"과 "근거에 있는데 안 쓰면 같은 크기의 오답"을 한 문단에 나란히 적는다.
3. **후보 0건이면 계약 자체를 뺀다.** 화이트리스트가 비었는데 "반드시 써라"를 걸면 억지 서술(신종 환각)이 생긴다. 후보 0건이면 계약 블록을 생략하고 배경 근거 원문 직투입으로 폴백한다.
4. **삭제·차단은 정밀도 0.9 게이트 뒤에 둔다.** `attribute.py`의 미지지 판정이 사람 라벨 대비 정밀도 0.9를 못 넘으면 3단을 켜지 않는다. 못 넘으면 도입 포기가 정답이다.
5. **계기판에서 (B)를 직접 센다.** `[충분 판정 & 기권]` 조합의 건수가 (B)형 오류율이다. 이 칸이 0이 아니면 프롬프트 수정이 실패한 것이다. 프롬프트를 고칠 때마다 이 칸과 (A)형 칸을 나란히 본다.

---

## 3. 즉시 착수 (v1에 오늘 넣을 것)

**배포 순서를 반드시 지켜라.** I1 → I2는 같은 커밋으로 나가야 한다. I2(프롬프트 완화·계약)만 먼저 넣으면 (A)가 재발하고, I3·I4(검증)만 넣으면 (B)가 그대로 남는다.

---

### I1. 근거 번호표 통일 + 문단 단위 귀속 표기

**파일:** `slice1.py`, `slice3.py`, `slice6.py` (`slice4.py`는 이미 적용됨 — 건드리지 마라)
**신설 함수:** `verify.check_citation_format()`

**무엇을:** `slice4`가 이미 쓰는 `[근거 idx/n]` 형식을 나머지 slice의 컨텍스트 조립부에 통일 적용한다. 현재 `slice1.py:38`은 `[근거]`라고만 쓰고 번호가 없어서, 답변 문장이 어느 청크에서 왔는지 지목할 대상 자체가 없다. 이것이 (C)의 물리적 원인이다. SYSTEM에는 **문단 단위** 표기만 요구한다.

```python
# ---------- slice1.py ----------
def build_context(hits):
    n = len(hits)
    ctx = ""
    for i, h in enumerate(hits, 1):
        ctx += f"[근거 {i}/{n}] {h['report_nm']} ({h['rcept_dt']})\n{h['text']}\n\n"
    return ctx, n

SYSTEM = (
    "너는 DART 공시 기반 분석 비서다. 근거 자료는 [근거 1/N] ... [근거 N/N] 번호가 붙어 제공된다. "
    "반드시 제공된 근거 자료의 내용만으로 답하라. "
    "각 문단 끝에 그 문단이 사용한 근거 번호를 (근거 1) 형식으로 표기하라. "
    "어떤 근거 번호도 붙일 수 없는 내용은 쓰지 마라. "
    "근거에 있는 내용을 번호와 함께 쓰는 것은 득점이고, 근거에 있는데 쓰지 않는 것은 감점이다. "
    "수치는 원문 그대로 사용하고 반올림하지 마라. URL을 생성하지 마라. "
    "마크다운 서식 금지. 답변 끝에 사용한 근거를 보고서명(접수일) 형식으로 표기하라."
)

def answer_type1(question, corp=None, year=None, k=5, notes=False):
    ...  # 검색·필터는 기존 그대로
    context, n = build_context(hits)
    messages = [{"role": "system", "content": SYSTEM_NOTE if notes else SYSTEM},
                {"role": "user", "content": f"근거 자료 (총 {n}건):\n{context}질문: {question}"}]
    ...
    return {"answer": ..., "retrieved_context": context,
            "think_trace": " -> ".join(trace),
            "chunks": [{"tag": f"근거 {i}/{n}", "text": h["text"], "rcept_no": h["rcept_no"]}
                       for i, h in enumerate(hits, 1)]}   # 2단 검증용 원본 청크 동봉

# ---------- slice3.py ----------
# [기업A 근거] 라벨은 유지하되 그 안에서 번호를 이어 매긴다
context, eid = "", 0
for label, hs in ((a, hits_a), (b, hits_b)):
    context += f"[{label} 근거]\n"
    for h in hs:
        eid += 1
        context += f"[근거 {eid}] {h['report_nm']} ({h['rcept_dt']})\n{h['text']}\n\n"

# ---------- slice6.py ----------
# 수치/배경 구분을 번호표 안의 태그로 유지 (그룹 라벨 제거하지 말 것)
context, eid, bg_n = "", 0, 0
for y in (y1, y2):
    for h in gather(corp, y, item):
        eid += 1
        context += f"[근거 {eid} | {y}년 수치] {h['report_nm']} ({h['rcept_dt']})\n{h['text']}\n\n"
    for h in gather_bg(corp, y, item):
        eid += 1; bg_n += 1
        context += f"[근거 {eid} | {y}년 배경] {h['report_nm']} ({h.get('section_path')})\n{h['text'][:900]}\n\n"

# ---------- verify.py 추가 ----------
def check_citation_format(answer, n_evidence):
    """모델이 존재하지 않는 근거 번호를 인용했는지, 무귀속 문단이 몇 개인지 센다."""
    cited = [int(x) for x in re.findall(r"\(근거\s*(\d+)", answer or "")]
    ghost = sorted({c for c in cited if c > n_evidence or c < 1})
    paras = [p for p in (answer or "").split("\n") if len(p.strip()) >= 30]
    unattributed = [p[:40] for p in paras if not re.search(r"\(근거\s*\d+", p)]
    return {"cited": sorted(set(cited)), "ghost": ghost, "unattributed": unattributed}
```

**기대효과:** (C)를 직접 해소한다. '선도 기업으로 자리매김' 문단에 `(근거 3)`이 붙으면 그 청크와 내용어를 대조해 "코퍼스엔 있지만 투입 청크엔 없는" 서술을 잡을 수 있고, 번호가 아예 없으면 무귀속 문단으로 걸린다. 동시에 I3의 트리아지가 겨냥할 앵커가 생겨 나머지 모든 검증의 전제 조건이 된다. `chunks` 필드를 반환 dict에 실은 것도 여기서다 — 2단이 `retrieved_context` 문자열을 다시 파싱하지 않고 원본 청크를 직접 받게 하려는 것이다.

**부작용:**
- 이것은 **근거 서술의 비용을 올리는 개입**이다. (B)를 만든 것과 같은 계열이다. 반드시 I2(기권 비용 인상)와 같은 커밋으로 나가야 하고, 적용 후 LG엔솔 ② 케이스로 답변 길이와 기권 태그를 전후 비교해야 한다.
- TODO.md에 기록된 대로 HCX가 근거 표기 지시를 간헐 불이행한다(답변 중간 1회만 표기, 끝에는 없음). 무귀속 문단 경고가 다수 발생하면 표기 지시를 SYSTEM에서 user 메시지 계약으로 옮겨 재시도한다 — slice4의 개수 계약이 user 메시지에서 먹혔던 실측과 같은 방향이다.
- `(근거 N)` 마커가 채점자에게 기계적으로 보일 위험. 근거완전성 지표가 있으므로 v1은 남기되, `clean()`에 `re.sub(r"\s*\(근거 \d+\)", "", ...)` 스트립 스위치를 만들어 두고 제출 직전에 판단한다.
- **비용:** LLM 호출 +0회, 지연 사실상 0.

---

### I2. slice6 원인 후보 화이트리스트 + 충분성 게이트 + 대칭 채점표

**파일:** `slice6.py`
**신설 함수:** `pick_cause_sentences()`, `bg_sufficiency()` / **수정:** `SYSTEM6`, `answer_type6()`, `gather_bg()`

**무엇을:** 세 가지를 한 번에 넣는다. 이것이 (B)를 정면으로 겨냥하는 주 처방이다.

1. **원인 후보 화이트리스트** — 코드가 `gather_bg()`로 확보한 배경 청크에서 인과·변화 표지어가 든 문장만 추려 `(B1)(B2)...` 번호를 붙여 프롬프트에 명시하고, "원인 설명은 이 후보 문장의 내용만 재료로 써라"를 건다. RECOMP의 추출형 압축을 순수 문자열 처리로 구현한 것이다.
2. **충분성 게이트** — 후보가 1개 이상이면 "기권 금지" 계약을, 0개면 "추측 금지 + 역질문" 지시를 건다. 기권 판단권을 모델에서 코드로 옮긴다.
3. **대칭 채점표** — SYSTEM6의 무조건 기권 허용("없으면 확인되지 않음이라고 밝혀라")을 4항 채점표로 교체한다. 항목 (3)이 이번에 새로 들어가는 핵심이다.

```python
# ---------- slice6.py ----------
CAUSE_MARKERS = ("증가", "감소", "하락", "상승", "둔화", "부진", "확대", "축소", "개선", "악화",
                 "영향", "인해", "기인", "때문", "따라", "수요", "가격", "판가", "환율", "원가",
                 "출하", "판매", "회복", "호조")

def split_sents(text):
    return [t.strip() for t in re.split(r"(?<=다)\.\s*", text or "") if t.strip()]

def pick_cause_sentences(bg_hits, item, max_n=6):
    """배경 청크에서 원인 서술 후보 문장을 코드가 추린다. LLM 호출 0회."""
    item = item or ""            # gather_bg가 item=None이면 정렬에서 TypeError — 같은 가드
    cands, seen = [], set()
    for h in bg_hits:
        for sent in split_sents(h["text"]):
            key = re.sub(r"\s+", "", sent)[:50]
            if not (15 <= len(sent) <= 200) or key in seen:
                continue
            if any(nz in sent for nz in BG_NOISE):
                continue
            score = sum(m in sent for m in CAUSE_MARKERS) + (2 if item and item in sent else 0)
            if score >= 1:
                seen.add(key)
                cands.append((score, sent, h["report_nm"]))
    cands.sort(key=lambda x: -x[0])
    return cands[:max_n]

SYSTEM6 = (
    "너는 DART 공시 기반 분석 비서다. 근거는 [근거 N | YYYY년 수치] 또는 "
    "[근거 N | YYYY년 배경] 번호가 붙어 제공된다. "
    "답변 순서: 1) 연도별 해당 수치를 수치 근거에서 찾아 각각 명시, "
    "2) 두 연도의 변화를 증가/감소와 폭으로 제시, "
    "3) [원인 후보 문장] 목록에 적힌 내용으로 변화의 배경을 설명하고 각 원인 문장 끝에 "
    "사용한 후보 번호를 (B1) 형식으로 붙여라. "
    "배경 서술 채점 규칙 — 아래 네 항목의 감점 크기는 전부 같다. "
    "(1) 원인 후보 문장에 있는 표현을 인용해 서술하면 득점이다. "
    "(2) 원인 후보 문장과 배경 근거에 없는 시장 상황, 금리, 물가, 소비 심리, 업계 동향을 쓰면 오답이다. "
    "(3) 원인 후보 문장이 제공되었는데도 '확인되지 않음'이라고 쓰는 것 역시 (2)와 같은 크기의 오답이다. "
    "(4) 원인 후보 문장이 하나도 제공되지 않았을 때만 '확인되지 않음'이 정답이다. "
    "'가능성이 있습니다', '보입니다', '것으로 판단됩니다' 같은 추측 표현을 쓰지 마라. "
    "근거에 있으면 단정해서 쓰고, 각 문단 끝에 사용한 근거 번호를 (근거 N) 형식으로 표기하라. "
    "수치는 원문 그대로 사용하고 반올림하지 마라. 마크다운 서식 금지. "
    "답변 끝에 사용한 근거를 보고서명(접수일) 형식으로 표기하라."
)

def answer_type6(question, corp, year, item):
    ...  # corp/연도 검문, context 조립(I1 형식)은 위와 동일
    bg_all = gather_bg(corp, y1, item) + gather_bg(corp, y2, item)
    cands = pick_cause_sentences(bg_all, item)

    if cands:                                    # 충분 — 기권을 금지한다
        context += "[원인 후보 문장 — 변화 원인 설명에는 아래 문장의 내용만 사용할 것]\n"
        for i, (sc, sent, rn) in enumerate(cands, 1):
            context += f"(B{i}) {sent} — 출처: {rn}\n"
        context += "\n"
        contract = (f"원인 후보 문장이 {len(cands)}건 제공되었다. "
                    "질문과 관련된 후보는 각각 최소 한 문장으로 반영하라. "
                    "관련 없는 후보를 억지로 넣지는 마라. "
                    "반영하지 않은 후보는 답변 맨 끝에 '(B번호 미반영: 한 줄 사유)'로 적어라. "
                    "후보가 하나라도 있는 한 원인 설명 전체를 '확인되지 않음'으로 끝내지 마라.")
    else:                                        # 불충분 — 추측을 금지하고 역질문을 시킨다
        contract = ("배경 근거에서 변화 원인 서술을 찾지 못했다. 수치 비교만 답하고, "
                    "변화 배경은 '투입된 공시에서 확인되지 않는다'고 명시하라. "
                    "이어서 어떤 공시 항목(예: 사업보고서의 '이사의 경영진단 및 분석의견')을 "
                    "확인하면 되는지 한 문장으로 안내하라. 원인을 추측해 쓰지 마라.")
    trace.append(f"배경 충분성 게이트: {'충분' if cands else '불충분'} "
                 f"(배경 청크 {len(bg_all)}건 -> 원인 후보 {len(cands)}건)")

    messages = [{"role": "system", "content": SYSTEM6},
                {"role": "user", "content": f"근거 자료:\n{context}질문: {question}\n"
                 f"{y1}년과 {y2}년 각각의 수치를 반드시 모두 제시한 뒤 비교하라. {contract}"}]
    ...
    ans = res.json()["result"]["message"]["content"]
    # 미반영 사유 줄은 채점 노이즈이므로 답변에서 떼어 trace로 옮긴다
    tail = re.findall(r"\(B\d+ 미반영:[^)]*\)", ans)
    if tail:
        ans = re.sub(r"\s*\(B\d+ 미반영:[^)]*\)", "", ans).strip()
        trace.append("[커버리지] " + " / ".join(tail))
    return {"answer": ans, "retrieved_context": context,
            "think_trace": " -> ".join(trace),
            "bg_sufficient": bool(cands), "bg_n": len(bg_all),
            "chunks": [...]}          # I1과 동일 형식
```

**기대효과:** (B)를 직격한다. LG엔솔 케이스에서 '영업이익은 메탈 가격 하락으로'와 북미 수요 문장은 `CAUSE_MARKERS`의 '가격'·'하락'·'수요'에 걸려 후보 목록에 들어가므로, 배경 전체를 포기하는 경로가 명시적 계약 위반이 된다. 동시에 (A)도 절반 막힌다 — 후보 목록 밖의 '전기차 수요 둔화'·'고금리'는 재료 자체가 없어진다. 메커니즘이 이미 실측으로 검증된 개수 계약(신한지주 2/6→3/3, 두산퓨얼셀 3/4→4/4)과 동일하므로 재현 가능성이 가장 높은 처방이다.

**부작용:**
- **가장 큰 위험은 `CAUSE_MARKERS`의 재현율이다.** 진짜 원인 문장이 마커에 안 걸리면 후보가 비어 정당한 배경까지 기권 처리된다. 즉 코드가 (B)를 대신 저지른다. 안전판은 두 개다. 후보 0건이면 화이트리스트 계약 블록 자체를 생략하고, 계약 문구를 "후보 문장과 배경 근거에 없는"으로 써서 배경 근거 원문 전체를 허용 범위에 남긴다. 마커 목록은 평가셋 18문항의 배경 근거에서 사람이 원인 문장을 표시한 것과 대조해 보강해야 한다.
- slice4에서 실측된 **개수 채우기(padding) 부작용**이 재발할 수 있다. 두산퓨얼셀에서 개수를 맞추려고 해당 없는 건을 끼워넣은 것과 같은 방식으로, 무관한 후보를 억지로 원인인 것처럼 쓸 수 있다. "관련 없는 후보는 억지로 넣지 마라" + 미반영 사유 허용이 완충이고, `max_n`을 6 이하로 제한한다.
- 항목 (3)이 과하게 작동하면 진자가 (A) 쪽으로 되튄다. 근거 구절 자체는 실존하므로 I3의 어휘 대조로는 안 잡히는 유형(**인과 관계 왜곡**)이다. 평가셋에서 답변을 눈으로 읽고 "구절은 인용했는데 인과를 뒤집었는지"를 확인해야 한다.
- 표가 많은 청크는 `(?<=다)\.` 문장 분리가 깨져 한 문장으로 뭉친다. 길이 상한 200자가 부분 방어다.
- 프롬프트 지시가 늘어 다른 지시(수치 전부 제시)의 준수율이 떨어질 수 있다.
- **비용:** LLM 호출 +0회. 문자열 처리 밀리초. 입력 토큰 +500~800자.

---

### I3. `attribute.py` 신설 — 문장-근거 귀속 트리아지 (경고 전용)

**파일:** 신설 `attribute.py` + `main.py` `clean()` 연결
**함수:** `split_sents()`, `norm_tokens()`, `lexical_score()`, `attribute()`

**무엇을:** 답변을 문장 단위로 쪼개고, 실제 투입된 청크(`r["chunks"]`)와 대조해 문장마다 `지지/애매/미검증` 라벨과 최적 근거ID를 붙인다. **1차는 어휘 대조**(조사 최장일치 절단 후 토큰 중첩 + 문자 바이그램 Dice), **2차는 임베딩**이되 1차에서 확정 안 된 문장에만 조건부로 발동한다. 순서를 이렇게 뒤집은 이유는 실제 인코더가 리서치 전제(multilingual-e5)와 달리 **Nemotron-3-Embed-1B이고 CPU에서 질의당 1~3초**이기 때문이다(`search_lib.py` 도크스트링에 명시). 임베딩을 1차로 두면 응답 속도 평가에서 손해를 본다.

v1에서는 **답변을 절대 건드리지 않고 think_trace 경고만 남긴다.** PAPERS.md 8번 원칙("탐지 먼저, 차단은 오탐률 실측 후")을 그대로 따른다.

```python
# ---------- attribute.py (신설) ----------
import re
import numpy as np
from slice1 import s          # 상주 Searcher 재사용 — 추가 메모리 0

# 조사는 반드시 긴 것부터. 최장일치로 한 번만 떼어낸다.
JOSA = ("으로써", "으로서", "에서는", "에서도", "이라는", "에서", "에게", "부터", "까지",
        "으로", "와의", "과의", "보다", "마다", "라는", "은", "는", "이", "가", "을", "를",
        "에", "의", "와", "과", "도", "만", "로")
FUNC = {"경우", "대한", "대해", "관련", "위해", "통해", "기준", "이후", "이전", "당시", "해당",
        "다음", "또한", "반면", "결론", "답변", "질문", "내용", "자료", "근거", "공시",
        "보고서", "확인", "각각", "전년", "대비"}
_CITE = re.compile(r"\((?:근거|B)\s*\d+\)")
EXEMPT = ("확인되지 않", "확인할 수 없", "찾지 못했", "근거:", "출처:")
CALC_PAT = ("차이", "증가", "감소", "늘어", "줄어", "변화", "폭")

LEX_HI, EMB_HI, EMB_LO = 0.55, 0.80, 0.70   # 전부 임시 초기값 — 평가셋 라벨로 보정 전까지 차단 금지

def _norm(t):
    return re.sub(r"[\s　,\.\(\)\[\]'\"·:;\-]+", "", t or "")

def split_sents(text):
    parts = re.split(r"(?<=[다요음함])\.\s*|\n+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 5]

def strip_josa(w):
    for j in JOSA:                          # JOSA는 길이 내림차순으로 정렬해 둘 것
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[:-len(j)]
    return w

def content_tokens(sent):
    sent = _CITE.sub("", sent)
    out = []
    for t in re.findall(r"\d[\d,\.]*|[A-Za-z][A-Za-z\-]+|[가-힣]{2,}", sent):
        if t[0].isdigit():
            out.append(t.replace(",", "")); continue
        t = strip_josa(t)
        if len(t) < 2 or t in FUNC:
            continue
        if re.fullmatch(r".+(니다|습니다|하다|되다|이다|였다|된다|한다|했다|하며|되며)", t):
            continue                        # 용언은 어미 변형 오탐이 많아 제외
        out.append(t)
    return out

def char_bigrams(t):
    t = _norm(t)
    return {t[i:i+2] for i in range(len(t) - 1)}

def lexical_score(sent, ev_text):
    toks = content_tokens(sent)
    tok_hit = sum(1 for w in toks if _norm(w) in _norm(ev_text)) / max(len(toks), 1)
    ba, be = char_bigrams(sent), char_bigrams(ev_text)
    dice = 2 * len(ba & be) / max(len(ba) + len(be), 1)
    return max(tok_hit, dice)

def is_calc_sentence(sent, ctx_digits):
    """모델이 계산한 파생 수치(차이 8조1259억)는 원문에 없는 게 정상 -> 면제"""
    nums = re.findall(r"[\d,]{2,}", sent)
    in_ctx = [x for x in nums if re.sub(r"[^0-9]", "", x) in ctx_digits]
    return bool(nums) and any(p in sent for p in CALC_PAT) and len(in_ctx) >= len(nums) - 1

def attribute(answer, chunks):
    """chunks: [{'tag':'근거 1/5','text':...}]  반환: 문장별 라벨 목록"""
    if not answer or not chunks:
        return []
    ctx_digits = re.sub(r"[^0-9]", "", " ".join(c["text"] for c in chunks))
    labels, pend = [], []
    for sent in split_sents(answer):
        if any(p in sent for p in EXEMPT) or is_calc_sentence(sent, ctx_digits):
            labels.append({"sent": sent, "status": "면제", "score": 1.0, "ev": None}); continue
        toks = content_tokens(sent)
        if len(toks) < 3:                    # 도입·연결 문장은 판정 제외 (LongCite 면제 규칙)
            labels.append({"sent": sent, "status": "면제", "score": 1.0, "ev": None}); continue
        sc, tag = max(((lexical_score(sent, c["text"]), c["tag"]) for c in chunks),
                      key=lambda x: x[0])
        if sc >= LEX_HI:
            labels.append({"sent": sent, "status": "지지", "score": round(sc, 3), "ev": tag})
        else:
            pend.append((sent, sc, tag))

    if pend:                                 # 2차 임베딩 — 미해결 문장이 있을 때만
        va = s.model.encode(["query: " + x for x, _, _ in pend],
                            normalize_embeddings=True, convert_to_numpy=True, batch_size=8)
        ve = s.model.encode(["query: " + c["text"] for c in chunks],
                            normalize_embeddings=True, convert_to_numpy=True, batch_size=8)
        sim = va @ ve.T
        for i, (sent, lex, tag) in enumerate(pend):
            j = int(sim[i].argmax()); cos = float(sim[i][j])
            st = "지지" if cos >= EMB_HI else ("미검증" if cos < EMB_LO else "애매")
            labels.append({"sent": sent, "status": st, "score": round(cos, 3),
                           "ev": chunks[j]["tag"], "lex": round(lex, 3)})
    return labels
```

```python
# ---------- main.py clean() 재배선 ----------
from attribute import attribute
from verify import (check_dates, check_facts, check_promo,
                    check_citation_format, tag_abstention, log_metrics)

def clean(r, qtype=None, question=""):
    if not r.get("answer"):
        return r
    r["answer"] = r["answer"].replace("**", "")
    ctx = r.get("retrieved_context", "")

    bad = check_dates(r["answer"], ctx)
    if bad:
        r["think_trace"] += f" -> [검증경고] 근거에 없는 날짜: {', '.join(bad)}"

    labels = attribute(r["answer"], r.get("chunks") or [])
    r["_labels"] = labels                              # 3단·계측용 (제출 직전 _ 접두 필드 제거)
    if labels:
        n = {k: sum(1 for L in labels if L["status"] == k)
             for k in ("지지", "애매", "미검증", "면제")}
        r["think_trace"] += (f" -> [귀속] 지지 {n['지지']}/애매 {n['애매']}"
                             f"/미검증 {n['미검증']}/면제 {n['면제']}")
        for L in labels:
            if L["status"] == "미검증":
                r["think_trace"] += f" | 미검증: {L['sent'][:40]}"
    return r

# answer_question의 다섯 개 return clean(r) -> return clean(r, qtype, question)
```

**기대효과:** (A)를 처음으로 자동 검거한다. '고금리·고물가로 소비 심리 위축'은 내용어가 근거에 하나도 없어 어휘 1차에서 낮게 나오고 임베딩 2차에서도 미검증으로 떨어진다. (C)도 해결된다 — 대조 상대가 코퍼스 전체가 아니라 **실제 투입된 청크**이므로 "코퍼스엔 있는데 투입분에 있었는지 모름"이라는 상황 자체가 성립하지 않는다. 강선우가 손으로 한 문자열 대조(미실존 7개 검거, 실존 2개 통과)가 그대로 자동화된다.

**부작용:**
- **임계값 0.55 / 0.80 / 0.70은 전부 임의 초기값이다.** 이 상태로 차단이나 삭제에 쓰면 (B)를 코드로 재현한다. 그래서 v1은 경고 전용이고, 3단은 평가셋 라벨링 후에만 켠다.
- 정상 패러프레이즈('줄었다'↔'감소')가 미검증으로 잡힌다. 용언 제외 규칙으로 상당수 걸러지지만 명사형 전환('둔화')은 남는다.
- 단위 변환된 수치(3,374,550백만원 vs 33조7455억)는 문자열이 달라 대조에 실패한다. 그래서 계산 문장 면제 규칙(`is_calc_sentence`)을 넣었고, 수치는 I4의 `check_facts`가 별도 축으로 담당한다.
- 조사 최장일치가 실제 명사 일부를 자를 수 있다('실로암' 류 고유명사).
- **임베딩 2차의 지연이 진짜 위험이다.** Nemotron-1B CPU에서 답변 8문장 + 청크 5~12개 인코딩이 수 초가 될 수 있다. 반드시 실측하고, 초과하면 (ㄱ) 어휘 전용 모드로 강등하거나 (ㄴ) 임베딩 2차를 오프라인 채점기 전용으로 내린다.
- `s.model.encode`의 passage 프리픽스 규약을 명섭 형에게 확인해야 한다. `search_lib.py`는 질의에 `"query: "`만 붙이는데, 색인 시 passage 쪽에 무엇을 붙였는지에 따라 코사인 절대값이 통째로 밀려 임계값이 무의미해진다.
- **비용:** LLM 호출 +0회, 메모리 +0(상주 모델 재사용).

---

### I4. `verify.py` 확장 — 수치·내용어·홍보 상용구·기권 태깅

**파일:** `verify.py`
**신설 함수:** `parse_kor_number()`, `context_numbers()`, `check_facts()`, `check_promo()`, `tag_abstention()`, `log_metrics()`

**무엇을:** `check_dates()`가 날짜만 보는 것을 네 축으로 넓힌다. 각 축의 역할이 다르다.

- `check_facts()` — 큰 금액·비율을 한국식 표기째 정수로 정규화해 대조하고, 두 근거 값의 합·차로 유도되는 값은 통과시킨다(증감폭 오탐 방지). 그리고 **인과 표지어가 있는 배경 문장에 한해서만** 내용어를 대조한다. 검증 대상을 좁힌 것은 VeriScore의 "검증 가능한 주장만 골라 검증하라"를 따른 것이다.
- `check_promo()` — (C)의 홍보 상용구를 사전으로 잡되, **컨텍스트에 실존하면 통과시킨다.** 무조건 삭제가 아니라 귀속 검사다. 사업보고서가 실제로 '선도 기업'이라고 썼다면 그 인용은 정당하다.
- `tag_abstention()` — 기권을 `none/partial/full` 3단으로 태깅한다. (B) 계측의 원재료다.
- `log_metrics()` — 질문마다 라벨 집계를 `runs/verify_log.jsonl`에 적재한다.

```python
# ---------- verify.py 확장 ----------
import re, os, json

_UNIT = {"조": 10**12, "억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4, "천": 10**3}
CAUSAL = ("인해", "영향", "때문", "따라", "배경", "원인", "둔화", "위축", "하락", "상승",
          "확대", "축소", "부진", "호조", "기인", "회복")
PROMO = ("선도 기업", "선도기업", "자리매김", "지속 가능성", "지속가능성", "글로벌 리더",
         "시장을 선도", "경쟁력을 확보", "입지를 강화", "위상을 확립", "성장 동력",
         "결론적으로", "종합하면")
ABSTAIN = re.compile(r"확인되지 않|확인할 수 없|자료가 없|찾지 못했|파악되지 않|알 수 없")

def parse_kor_number(tok):
    tok = tok.replace(",", "").replace("원", "")
    parts = re.findall(r"(\d+(?:\.\d+)?)(조|억|천만|백만|만|천)?", tok)
    total, ok = 0.0, False
    for num, unit in parts:
        if not num:
            continue
        total += float(num) * _UNIT.get(unit, 1); ok = True
    return int(total) if ok else None

def context_numbers(text):
    out = set()
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?(?:조|억|천만|백만|만|천)?[\d,조억만천]*", text or ""):
        v = parse_kor_number(tok)
        if v is not None and v >= 1000:
            out.add(v)
    return out

def check_facts(answer, context):
    """미귀속 항목 목록 [{sentence, missing:[...]}] 반환. 경고 전용, 차단하지 않는다."""
    from attribute import split_sents, content_tokens, _norm
    if not answer or not context:
        return []
    nctx, ctx_nums = _norm(context), context_numbers(context)
    big = sorted(v for v in ctx_nums if v >= 10**8)     # 유도값 검사는 큰 수만 (O(n^2) 통제)
    bad = []
    for sent in split_sents(answer):
        missing = []
        for tok in re.findall(r"\d[\d,]*(?:\.\d+)?(?:조|억|천만|백만|만|천)?[\d,조억만천]*(?:원)?", sent):
            v = parse_kor_number(tok)
            if v is None or v < 1000 or v in ctx_nums:
                continue                                 # 연도·회차 등 작은 수는 면제
            if not any(abs(a - b) == v or a + b == v for a in big for b in big):
                missing.append(f"수치:{tok}")
        if any(c in sent for c in CAUSAL):               # 배경 문장만 내용어 대조
            for w in content_tokens(sent):
                if not w[0].isdigit() and _norm(w) not in nctx:
                    missing.append(w)
        if missing:
            bad.append({"sentence": sent[:80], "missing": sorted(set(missing))})
    return bad

def check_promo(answer, context, labels=None):
    """컨텍스트에 없는 홍보 상용구 문장. labels가 있으면 '지지' 판정 문장은 통과(화이트리스트)."""
    from attribute import split_sents, _norm
    nctx = _norm(context)
    supported = {L["sent"] for L in (labels or []) if L["status"] in ("지지", "면제")}
    bad = []
    for sent in split_sents(answer):
        if sent in supported or re.search(r"\d{3,}", sent):   # 수치 문장은 홍보 판정 면제
            continue
        hits = [p for p in PROMO if p in sent and _norm(p) not in nctx]
        if hits:
            bad.append({"sentence": sent[:80], "missing": [f"홍보구:{h}" for h in hits]})
    return bad

def tag_abstention(answer):
    if not answer:
        return "empty"
    if not ABSTAIN.search(answer):
        return "none"
    has_fact = bool(re.search(r"\d[\d,\.]*\s*(원|억|조|%|건|주)", answer))
    return "partial" if has_fact else "full"

def log_metrics(question, row, path="runs/verify_log.jsonl"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"q": question[:60], **row}, ensure_ascii=False) + "\n")
```

**기대효과:** (A)의 배경 문장이 `check_facts`의 내용어 대조에서 걸린다 — '둔화'·'위축'·'하락'이 `CAUSAL` 표지에 걸려 검사 대상이 되고, '전기차'·'고금리'·'니켈'·'원자재'가 미실존으로 잡힌다. 손 대조에서 검거율 100%를 이미 확인했으므로 판정 로직 자체는 검증된 상태다. (C)의 홍보 문구는 `check_promo`가 투입 컨텍스트 기준으로 판정한다. 날짜에서 이미 조작 2건을 검거한 방식이 금액·비율·내용어·상용구로 확장되는 것이다.

**부작용:**
- 금액은 단위 변환(백만원 단위 표 vs 억 단위 서술)과 반올림 때문에 오탐이 구조적으로 발생한다. TODO.md에 이미 "금액 검증 오탐 위험"으로 적혀 있는 문제다. 합·차 유도값 통과 규칙이 1차 방어이지만, 곱셈·비율로 유도된 값('전년 대비 24.1% 감소')은 여전히 오탐이다. **그래서 수치는 단독으로 무근거 판정의 근거로 쓰지 않는다.**
- `PROMO` 사전은 커버리지가 좁다. 사전에 없는 새 홍보 표현은 통과한다. 완전 방어가 아니라 **실측된 패턴의 재발 방지**로 이해해야 한다. 반대로 사전을 넓히면 정상 요약 문장까지 걸리므로 수치 포함 문장 면제 예외를 둔다.
- `ABSTAIN` 정규식이 불완전하면 기권을 놓친다. 실측 답변에서 새 기권 표현을 볼 때마다 사전에 추가하는 운영이 필요하다.
- **비용:** LLM 호출 +0회. 순수 정규식이라 밀리초 단위.

---

### I5. `main.clean()` 재배선 + 2×2 스코어카드

**파일:** `main.py`, `verify.py`
**변경:** `clean(r)` → `clean(r, qtype=None, question="")`, 다섯 개 호출부 수정

**무엇을:** 지금 `clean(r)`은 인자가 r 하나뿐이라 qtype별 분기도, 질문 기반 재작성도 불가능하다. 시그니처를 넓히고, 여기에 **(A)와 (B)를 한 화면에서 보는 2×2 스코어카드**를 붙인다. 이 계기판이 없어서 (A)→(B) 진자 사고를 사후에야 알았다.

```python
# ---------- verify.py 추가 ----------
def scorecard(labels, answer, bg_sufficient=None, bg_n=0):
    """(A)형과 (B)형을 같은 표에서 센다."""
    tag = tag_abstention(answer)
    n_unver = sum(1 for L in labels if L["status"] == "미검증")
    row = {"기권태그": tag, "미검증문장": n_unver,
           "지지문장": sum(1 for L in labels if L["status"] == "지지"),
           "배경근거": bg_n, "충분판정": bg_sufficient}
    # (B)형: 코드가 충분하다고 판정했는데 모델이 기권
    row["B형_과잉기권"] = int(bg_sufficient is True and tag in ("partial", "full"))
    # (A)형: 코드가 불충분하다고 판정했는데 모델이 서술을 강행
    row["A형_환각위험"] = int(bg_sufficient is False and tag == "none" and n_unver > 0)
    return row

# ---------- main.py clean() 말미 ----------
    row = scorecard(labels, r["answer"], r.get("bg_sufficient"), r.get("bg_n", 0))
    r["think_trace"] += f" -> [계기판] {row}"
    log_metrics(question, {"qtype": qtype, **row})

    fmt = check_citation_format(r["answer"], len(r.get("chunks") or []))
    if fmt["ghost"]:
        r["think_trace"] += f" -> [귀속경고] 존재하지 않는 근거번호 인용: {fmt['ghost']}"
    if fmt["unattributed"]:
        r["think_trace"] += f" -> [귀속경고] 무귀속 문단 {len(fmt['unattributed'])}개"

    for f in check_facts(r["answer"], ctx) + check_promo(r["answer"], ctx, labels):
        r["think_trace"] += (f" -> [사실검증] '{f['sentence'][:40]}' "
                             f"미실존 {f['missing'][:5]}")
    return r

# ---------- 제출 직전 (make_submission 등) ----------
# r = {k: v for k, v in r.items() if not k.startswith("_")
#      and k in ("question_id", "question", "retrieved_context", "think_trace", "answer")}
```

**기대효과:** 프롬프트를 고칠 때마다 `runs/verify_log.jsonl`에서 `B형_과잉기권` 합계와 `미검증문장` 합계를 나란히 볼 수 있다. 진자가 어느 쪽으로 튀었는지가 실행 몇 분 만에 수치로 나온다. 부수적으로 think_trace에 실린 검증 기록 자체가 평가지표 '환각방지'와 '정보한계 대응'의 제출 증거가 된다.

**부작용:**
- `bg_sufficient`, `bg_n`, `chunks`, `_labels` 같은 내부 필드가 제출 JSON에 새어 나가면 안 된다. 제출 스키마는 5개 키뿐이므로 화이트리스트 방식으로 필터링해야 한다.
- `slice1`·`slice4`의 부재 즉답 경로(HCX 미호출)는 `chunks`가 비어 있어 라벨이 안 생긴다. 이 경우 계기판 분류를 건너뛰어야 정당한 기권이 (B)로 오분류되지 않는다.
- think_trace가 길어진다. 채점자가 읽는 필드이므로 문구를 중립적으로 다듬어야 한다('제거함'이 아니라 '근거 미확인으로 미기재').
- **비용:** LLM 호출 +0회, 지연 0.

---

### I6. 유형② 읽기 노트 — 같은 1회 호출 안에서 [검토]/[답변] 분리

**파일:** `slice1.py`, `main.py` 라우팅 1줄

**무엇을:** 호출 수를 늘리지 않고 한 번의 출력을 두 부분으로 나눈다. 앞부분에서 근거 1~N 각각에 관련 유무와 핵심 사실 한 줄을 쓰게 하고, 뒷부분에서 검토에 적은 사실만으로 답하게 한다. 코드가 `[답변]` 마커로 잘라 노트는 think_trace에, 뒷부분만 answer에 넣는다. 유형①(값 하나 찾기)에는 낭비이므로 `qtype == 2`일 때만 켠다.

```python
# ---------- slice1.py ----------
SYSTEM_NOTE = SYSTEM + (
    " 답하기 전에 근거를 하나씩 검토하라. 아래 출력 형식을 반드시 지켜라.\n"
    "[검토]\n근거1: 관련있음 - 핵심 사실 한 줄 (관련 없으면 '관련없음'만)\n"
    "근거2: ... (제공된 근거 번호 전부에 대해 반복)\n"
    "[답변]\n검토에서 관련있음으로 적은 사실만 사용해 답하라. "
    "검토에 적지 않은 사실을 답변에 쓰지 마라. "
    "검토 전체가 질문에 답하기에 부족하면 부족하다고 답하라."
)

    # answer_type1 응답 처리부
    out = res.json()["result"]["message"]["content"]
    if notes:
        m = re.search(r"\[\s*답변\s*\]|^답변\s*[:：]", out, re.M)   # 마커 변형 허용
        if m:
            trace.append("읽기노트: " + out[:m.start()].replace("[검토]", "").strip()[:300])
            out = out[m.end():].strip()
        else:
            trace.append("읽기노트 마커 누락 -> 전체를 답변으로 폴백")
    # maxTokens: notes면 1000, 아니면 700

# ---------- main.py ----------
    if qtype in (1, 2):
        r = answer_type1(question, corp=corp, year=year, notes=(qtype == 2))
```

**기대효과:** (C)의 감사 불능이 해소된다. 답 이전에 청크별 검토가 강제되므로 노트에 없는 사실을 답에 쓰면 자기모순이 되어 홍보성 서사의 삽입 비용이 오른다. Lost in the Middle의 중간 청크 무시도 완화된다. 부수적으로 think_trace에 실릴 사고 과정이 공짜로 생겨 논리성 지표에 기여한다. TODO.md에 기록된 유형② 결함(마지막 '결론적으로' 문단이 정보 없는 반복)도 줄어들 것으로 기대한다.

**부작용:**
- HCX가 `[답변]` 마커 형식을 안 지키면 노트가 답변에 섞여 나간다. 폴백(전체를 answer로)과 마커 변형 허용 파싱이 방어선이며, 형식 준수율을 평가셋에서 실측해야 한다.
- 노트 단계에서 '관련없음'을 남발하면 과잉 기권 방향의 압력이 생긴다. 원 논문(Chain-of-Note)이 보고한 기권율 +10.5포인트가 우리에겐 양날이다. **유형②에만 켜고 기권 빈도를 전후 비교해야 한다.**
- 원 논문의 수치는 LLaMA-2 7B SFT 결과다. 프롬프트만으로 같은 폭이 나온다는 보장은 없다.
- **비용:** LLM 호출 +0회. 출력 토큰 +노트 5줄만큼 지연이 소폭 증가(`maxTokens` 700→1000).

---

### I7. Open형 평가셋 18문항 + 너겟 정답표 (§7에서 상세)

**파일:** 신설 `eval/evalset.jsonl`, `eval/run_eval.py`, `eval/score_nuggets.py`, `attribution.py`(오프라인 감사기)

TODO.md의 착수 조건("평가셋 6유형×3문 제작 → v1 baseline 측정")을 이 문서 §7의 설계대로 만든다. **이것이 I1~I6의 효과를 판정하는 유일한 수단이며, 3단(교정)을 켤지 말지의 게이트다.** 상세 설계는 §7 참조.

---

## 4. 평가셋 이후 (측정하고 넣을 것)

각 항목에 **켜기 위한 게이트 조건**을 명시했다. 조건을 못 넘으면 도입 포기가 정답이다.

### E1. 표적 재작성 `revise.py` — 삭제 대신 후퇴

**게이트:** `attribute.py`의 '미검증' 판정이 사람 라벨 대비 **정밀도 0.9 이상**.

`check_grounding`/`attribute`가 미검증 문장을 잡았을 때만, 그리고 Open형(qtype 2·4·6)일 때만 HCX를 1회 더 호출한다. 프롬프트는 세 가지를 못박는다. 통과 문장은 글자 하나 바꾸지 말 것, 문제 문장은 근거 실존 표현만 남겨 다시 쓰거나 삭제할 것, 그리고 **코드가 뽑은 '실존 표현 목록'을 제시하며 "이 목록의 내용을 빠뜨리면 감점"이라고 고지할 것.** 세 번째가 (B)의 재발 방지 조항이다. 교정기는 제3자 프레임("다른 작성자가 쓴 답변을 검수한다")으로 제시한다 — CoVe가 지적한 자기 오답 반복 함정의 회피책이다.

```python
def targeted_revise(question, answer, context, bad, whitelist, trace):
    ...
    new = res.json()["result"]["message"]["content"].replace("**", "")
    # 안전장치 1: 재검증에서 미귀속이 안 줄면 원본 유지
    if len(check_facts(new, context)) >= len(bad):
        return answer, trace + " -> [재작성] 개선 없음, 원본 유지"
    # 안전장치 2: 통과 문장에 있던 수치가 사라졌으면 원본 유지 (과잉 삭제 방어)
    if context_numbers(" ".join(passed)) - context_numbers(new):
        return answer, trace + " -> [재작성] 통과 수치 소실, 원본 유지"
    return new, trace + f" -> [재작성] 미귀속 {len(bad)}문장 표적 교정"
```

재료(실존 표현 목록)가 아예 없으면 재작성을 하지 않고 **부분 기권 문구**로 마감한다("그 밖의 배경 요인은 공시에서 확인되지 않습니다"). 어느 경로에서도 답변 전체가 죽지 않는다는 것이 이 설계의 핵심이다.
**비용:** 검출 시에만 +1회, Open형 평균 +0.3~0.6회. **위험:** 오탐이 멀쩡한 문장을 재작성 대상으로 보내는 것, 그리고 실존 표현 목록에 질문과 무관한 원인 서술이 섞이면 "감점 고지" 때문에 무관한 내용을 억지로 끼워넣는 역방향 환각.

### E2. 서빙 게이트 임계값 캘리브레이션

**게이트:** 사람 라벨 30~50문장으로 임계값 스윕. 정밀도 0.9를 만족하는 지점이 없으면 게이트 도입 포기.

```python
def pick_threshold(rows, target_precision=0.9):
    best = None
    for th in [x / 100 for x in range(50, 95)]:
        pred = [r["score"] < th for r in rows]
        gold = [r["human"] in ("셀프지식", "환각") for r in rows]
        tp = sum(p and g for p, g in zip(pred, gold))
        fp = sum(p and not g for p, g in zip(pred, gold))
        fn = sum((not p) and g for p, g in zip(pred, gold))
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        if prec >= target_precision and (best is None or rec > best[2]):
            best = (th, prec, rec)
    return best     # None이면 도입 포기가 정답
```

일치율 하나만 보면 클래스 불균형에서 오도된다. **정밀도와 재현율을 반드시 따로 본다.**

### E3. 렉시컬 재귀속 `reattribute()` — 인용 정밀도 축

모델이 붙인 `(근거 N)`을 믿지 않고, 문장 내용어를 쿼리로 삼아 각 청크에 중첩 점수를 매겨 코드가 보기에 가장 그럴듯한 출처를 독립 산출한다. 코드의 최선과 모델의 인용이 다르고 점수 차가 0.3 이상이면 '인용불일치'로 경고한다. I3의 실존율 검사가 "전체 근거 어디에도 없는 표현"(재현율 축)을 잡는다면, 이것은 "근거엔 있는데 엉뚱한 근거에 붙인 인용"(정밀도 축)을 잡는다. 근거가 최대 12개뿐이라 인덱스 없이 매번 계산해도 밀리초다.
**중요:** 이것은 **경고 전용이며 절대 삭제 트리거로 쓰지 않는다.** 투입 청크들이 내용상 겹칠 때 오탐이 구조적으로 발생하기 때문이다.

### E4. quote-then-write / 원문 인용구 검증 (slice6 배경 한정 A/B)

배경을 쓰기 전에 `[배경 인용]` 섹션에 근거에서 그대로 복사한 구절을 3개 이하로 옮겨 적게 하고, `[답변]` 섹션에서 그 구절만 재료로 서술하게 한다. 코드는 정규화 후 substring 검증을 하고, 실패하면 `difflib` 슬라이딩으로 가장 가까운 실제 스팬을 찾아(유사도 0.7 이상) 치환하며, 그마저 안 되면 조작 인용으로 보고 제거한다. 인용 텍스트가 LLM을 통과하지 않게 만드는 것이 핵심이다.

**반드시 A/B로만 붙여라.** Show Your Work가 실측한 그대로 **인용구 요구 자체가 기권을 늘리고**(GPT-5.2 13.8%→15.7%) 일부 모델의 정확도를 유의하게 떨어뜨렸다(Claude 78.5%→72.5%, p=0.009). 이것이 (B)의 재발 경로다. 그리고 substring 통과가 의미적 타당성을 보장하지 않는다는 것(같은 연구에서 48.0~78.8%)을 잊지 마라. **이것은 감사 가능성 장치이지 정확성 보증이 아니다.**

### E5. 3슬롯 부분 기권 형식

답변을 `[확인된 사실]` / `[배경 설명]` / `[확인 불가]` 세 슬롯으로 고정한다. 불확실성을 담을 전용 공간이 생기므로 불확실하다는 이유로 답변 전체를 포기할 유인이 구조적으로 사라지고, 평가지표 '정보한계 대응'을 형식 자체로 확보한다. JSON 잘림 실측이 있으므로 한글 마커 평문 + 정규식 분할로 받고, 마커 누락 시 전체를 슬롯1로 간주하는 폴백을 둔다.
**주의:** I2의 불충분 분기와 지시가 충돌한다(한쪽은 "배경 쓰지 마라", 한쪽은 "배경 슬롯을 채워라"). 분기별 문구를 맞춰야 하며, E4와 동시에 걸면 지시 과부하다. 평가셋에서 승자를 하나만 고른다.

### E6. 배경 별도 호출 분리 또는 map-reduce

**게이트:** I2(화이트리스트)가 평가셋에서 잔여 환각을 못 잡을 때만.

두 갈래다. (ㄱ) `answer_type6`을 두 호출로 쪼개 1차는 수치 대조만, 2차는 원인 후보 목록만 주고 "이 문장들을 2~4문장으로 재구성하라"는 좁은 과제만 준다. 2차 입력에 배경 근거 원문 자체가 없으므로 세계 지식이 끼어들 표면적이 입력 구조 수준에서 제거된다. (ㄴ) 청크 N개 각각에 독립 호출로 사실을 추출하고(map, `ThreadPoolExecutor` 병렬) 마지막 1회에서 사실 풀만으로 합성한다(reduce). (ㄴ)은 최대 +12회 호출이라 크레딧과 지연 부담이 크므로 (ㄱ)을 먼저 시도한다.
**공통 위험:** 지연이 명백한 비용이다. 응답 속도가 평가에 반영될 수 있으므로, I2로 충분하면 도입하지 않는 것이 옳다.

### E7. 통계 유틸 `eval/stats.py` — McNemar + 문항 군집 SE

프롬프트 v1 대 v2 비교를 "평균 점수 두 개 나란히 놓기"가 아니라 대응표본 McNemar 검정으로 판정하고, 지지율의 표준오차는 문항을 군집으로 잡아 계산해 `지지율 62% ± 9%p (n=18문항/187문장)` 형식으로만 보고한다. **18문항 규모에서 문항 단위 이진 지표의 95% 신뢰구간은 대략 ±23%p다.** 그보다 작은 차이를 "개선했다"고 말하면 통계적으로 거짓말이다. 대응은 문장 키가 아니라 **너겟 ID**로 잡는 편이 안전하다 — v1과 v2의 답변 문장은 1:1로 대응하지 않지만 너겟 ID는 두 버전에서 같기 때문이다.

### E8. `extract.py` Function Calling 전환

HCX-005는 Structured Outputs 미지원(HCX-007 전용)이 공식 문서로 확정됐으므로, 스키마 강제의 유일한 우회로가 `tools` 파라미터다. `set_slots` 함수 스키마로 corps/year/qtype/item을 정의하고 `toolChoice`로 강제 호출시킨다. 실패하거나 `toolCalls`가 비면 기존 v1.5 경로로 폴백하므로 전환 리스크가 없다.
**주의:** Function Calling은 `maxTokens` 최소 1,024를 요구한다(현재 200). 지연 변화를 실측해야 한다. 그리고 `arguments`가 문자열로 오면 결국 JSON 파싱이 다시 필요해 잘림 문제가 재발할 수 있다. **동일 질문 30개를 양쪽으로 돌려 파싱 실패율과 지연을 비교한 뒤 결정한다.** v1.5가 지금 버티고 있으므로 급하지 않다.

### E9. 부수 버그 수정 (평가셋 전수 점검 시 함께)

- `slice6.gather_bg()`의 정렬 키 `item not in m["text"]`는 `item=None`이면 `TypeError`다. `item = item or ""` 가드를 넣는다.
- `slice3.gather()`의 검색어 `f"{corp} {year}년 {item}"`은 item이 None이면 문자열 "None"이 쿼리에 들어간다.
- 부재 응답에 `year=None`이면 "None년"으로 찍힌다(TODO.md 기록). 문구 분기 추가.
- 부재 진단 단계별 분기(기업 필터 0건 / 연도 0건 / 키워드 0건)는 필터가 전수조사라 **증명 가능한 부재**이며 평가지표 '정보한계 대응'에 직결된다.

---

## 5. 채택하지 않은 것과 그 근거

| 기각 항목 | 어느 각도가 제안했나 | 기각 사유 |
|---|---|---|
| **문장 단위 인용 강제(`[E1]` 전 문장 부착)** | 문장 귀속 각도 | **(B)를 만든 바로 그 개입이다.** 근거 서술의 비용을 문장 단위까지 올리면 기권이 최저 비용 경로가 된다. 우리는 이것을 이미 한 번 실측으로 겪었다. 문단 단위로 낮추고(I1), 진짜 판정은 코드가 독립 재계산한다(I3). |
| **미지지 문장 단독 삭제(redact-only)** | 문장 귀속 각도, 백오프 각도 | 오탐이 곧 정보의 영구 손실이고, 그것이 (B)의 코드 버전이다. 삭제만 하면 논리 흐름이 끊겨 논리성 지표도 깎인다. FinGround 실측(검증 단독 68% → 재작성 병행 78%)에 따라 **재작성을 주 경로로 채택**하고 삭제는 재료가 없을 때의 폴백으로만 남긴다. |
| **best-of-2 재순위(temperature 0.5 샘플 병행)** | 문장 귀속 각도 | TODO.md에 선결 조건으로 못박은 **"전 slice temperature=0 재현성 원칙"과 정면 충돌**한다. 재현성이 깨지면 평가셋 전후 측정 자체가 불가능해져, 이 문서의 4단(계측)을 무력화한다. 호출도 2배다. 또한 "무근거 문장이 적은 쪽"을 고르면 짧고 빈약한 답이 이기는 역선택 위험이 있다. |
| **LLM 자기 감사 `audit.py`(HCX가 자기 답변을 판정)** | decompose-then-verify 각도 | 자기가 생성한 답변을 같은 모델이 채점하는 구조라 **(A)형 세계 지식 서사를 '지지'로 오판할 자기 편향**이 본질적이다. 제3자 프레임으로 완화는 되지만 제거는 안 된다. 지연 +1회는 응답 속도 평가와 직접 상충한다. 앞 단계들로 잔여 오류가 줄면 아예 넣지 않는 것이 옳다. **조건부 유보** — 평가셋에서 표지 없는 서술형 환각이 유의하게 남을 때만 재검토한다. |
| **한국어 NLI 판정기 서빙 탑재(klue-roberta INT8)** | 트리아지 각도 | 4GB 서버에 이미 **OOM 리스크(추정 5.8GB)가 미해결**로 TODO에 적혀 있다. 게다가 KLUE-NLI는 뉴스·위키 단문 학습이라 공시 문어체·표 문장과 도메인 갭이 크고, 여러 근거 문장을 종합해야 지지되는 답변 문장을 부당하게 기각한다. 도입 게이트(검증셋에서 임베딩 단독 대비 이득 실측)를 세워 두되, **서빙에는 넣지 않는다.** |
| **AutoAIS(T5-11B) / AlignScore / MiniCheck 등 전용 판정 모델** | 여러 각도 | 영어 전용이거나 4GB에 안 들어간다. MiniCheck의 교훈("검증기는 생성기보다 훨씬 작아도 된다")을 우리 제약에서 극한까지 밀면 **모델 없는 렉시컬 검증**이 되고, 그것이 I3·I4다. |
| **kiwipiepy 형태소 분석기(서빙)** | 트리아지 각도 | 조사 분리 정밀도는 오르지만 서버 메모리가 빠듯하다. 정규식 최장일치 절단으로 근사하고, 필요하면 **오프라인 채점기에서만** 쓴다. |
| **RAGAS 계열 reference-free 채점** | 채점 각도 | 정답 없이 자동 채점하면 **(B)를 원리적으로 못 잡는다.** 아무 말도 안 한 답변이 감점을 안 받기 때문이다. 그래서 사람이 만든 너겟 정답표(§7)가 필수다. |
| **모순(Contradictory) 자동 탐지** | decompose-then-verify 각도 | AttrScore에서 GPT-4조차 모순 탐지 F1이 45.0%에 그쳤다. 우리 렉시컬 검증으로는 원리상 불가능하다. **포기가 합리적**이며, "근거 구절은 실존하되 인과를 뒤집은" 유형은 사람 육안 점검에 맡긴다. |
| **파인튜닝 / HyDE / LLM 직접 계산 / 문서 통째 투입** | — | 파인튜닝은 시간상 불가. 나머지 셋은 PAPERS.md의 "근거 있는 금지 목록"에 이미 등재된 항목으로 이번 설계에서도 유지한다. |
| **`slice4`의 `[근거 idx/n]` 형식 변경** | 문장 귀속 각도 | 개수 계약과 번호표가 이미 실측 효과를 냈다(신한지주 3/3, 두산퓨얼셀 4/4). **작동하는 것은 건드리지 않는다.** slice1/3/6을 slice4에 맞추는 방향이지 그 반대가 아니다. |

---

## 6. 논문 → 코드 매핑표

> URL이 비어 있는 항목은 리서치 원문이 식별자를 명시하지 않은 것이다. 제안서·발표에 인용하기 전 반드시 원문을 직접 확인하라.

| 논문(연도) | 핵심 발견 | 정량 효과 | 우리 적용 위치 | 우선순위 |
|---|---|---|---|---|
| **ALCE** (EMNLP 2023) · https://arxiv.org/abs/2305.14627 | 문장 단위 인라인 인용을 자동 채점 가능한 표준 형식으로 정립. 인용 재현율/정밀도 정의 | 재순위 기법으로 ASQA 인용 재현율 73.6→84.8%, 정밀도 72.5→81.6% | I1 근거 번호표 + 문단 귀속 표기 / §7 채점 정의 | 즉시 |
| **LongCite** (2024) · https://arxiv.org/abs/2409.02897 | 도입·연결 문장에는 인용을 면제해 과잉 페널티를 막음. 생성 중 인용 강제와 사후 부착의 정확성 차이 | 사후 부착 시 정확성 100% 유지, 인용 F1 65.8 | I1(문단 단위로 완화) / I3 기능 문장 면제 규칙 | 즉시 |
| **Why Language Models Hallucinate** (Kalai et al. 2025) · https://arxiv.org/abs/2509.04664 | Observation 1 — 기권 비용이 0이고 다른 행동의 비용만 있으면 특정 행동이 지배 전략이 된다 | (이론 증명) | I2 대칭 채점표(SYSTEM6 항목 3) | 즉시 |
| **Sufficient Context** (Joren et al., ICLR 2025) · https://arxiv.org/abs/2411.06037 | 기권 판단을 모델 신뢰도가 아니라 근거 충분성이라는 외부 신호로 결정하라 | Gemma 오답률: 맥락 없음 10.2% → 불충분 맥락 66.1% | I2 충분성 게이트(`pick_cause_sentences` 결과로 분기) | 즉시 |
| **RECOMP** (ICLR 2024) · https://arxiv.org/abs/2310.04408 | 추출형 압축 — 유용한 문장만 골라 넣고 무관하면 아예 넣지 않는다 | 압축률 6%까지 낮춰도 QA 성능 손실 미미 | I2 `pick_cause_sentences()` 원인 후보 화이트리스트 | 즉시 |
| **Chain-of-Note** (EMNLP 2024) · https://arxiv.org/abs/2311.09210 | 문서마다 읽기 노트를 먼저 쓰게 하면 노이즈 내성과 정당한 기권이 함께 오른다 | EM +7.9p, 범위 밖 질문 기권율 +10.5p (LLaMA-2 7B SFT) | I6 유형② `[검토]/[답변]` | 즉시 |
| **AttrScore** (Findings of EMNLP 2023) · https://arxiv.org/abs/2305.06311 | 귀속 오류를 Attributable / Extrapolatory / Contradictory로 분류 | GPT-4의 Contradictory 탐지 F1 45.0% (→ 모순 탐지는 포기가 합리적) | I3 미검증 판정 = Extrapolatory / §5 모순 탐지 기각 근거 | 즉시 |
| **SummaC** (TACL 2022) · https://arxiv.org/abs/2111.09525 | 검증 실패의 원인은 모델이 아니라 문서 단위 입력이라는 입도 불일치 | 문장 단위 분해만으로 강력한 검증기 | I3 문장 단위 트리아지(청크가 아니라 문장 대조) | 즉시 |
| **AlignScore** (ACL 2023) · https://arxiv.org/abs/2305.16739 | 긴 근거는 문단·문장으로, 답변은 문장으로 쪼개 정렬 점수를 집계 | GPT-4 기반 지표와 동등한 일관성 판정 | I3 `attribute()` 구조 | 즉시 |
| **RAGTruth** (ACL 2024) · https://arxiv.org/abs/2401.00396 | LLM에게 자기 답변의 무근거 구간을 찾게 하면 GPT-4-turbo도 구간 F1 28.3 | 구간 F1 28.3 | §5 LLM 자기 감사 기각 / I3을 결정론적 코드로 둔 근거 | 즉시 |
| **VeriScore** (Findings of EMNLP 2024) · https://arxiv.org/abs/2406.19276 | 모든 주장이 검증 가능하다는 전제가 틀렸다 — 검증 가능한 주장만 골라 검증하라 | (설계 원칙) | I4 `check_facts`의 CAUSAL 표지 한정 + 유도 수치 면제 | 즉시 |
| **FActScore** (EMNLP 2023) · https://arxiv.org/abs/2305.14251 | 장문을 원자 단위로 분해해 지식원과 개별 대조 | (정밀도만 재면 기권 답변이 만점) | I4 `check_facts` 골격 / §7 재현율 병행의 근거 | 즉시 |
| **Core** (2024) · https://arxiv.org/abs/2407.03572 | 자명하거나 반복적인 하위 주장은 고유성·정보성으로 걸러내는 착탈식 모듈 | (설계 원칙) | I4 `check_promo()` 홍보 상용구 필터 | 즉시 |
| **K-FinHallu** (2026) · https://arxiv.org/abs/2605.29523 | 한국어 금융 RAG에서 부당한 기권(False Refusal)을 독립 환각 유형으로 분류. 정당한 기권 축이 모든 모델에서 가장 약함 | (유형 체계) | I2 조건부 기권 / I5 2×2 스코어카드의 (B)형 칸 | 즉시 |
| **FinRAG-12B** (2026) · https://arxiv.org/abs/2605.05482 | 기권율은 낮을수록 좋은 것이 아니라 적정 밴드로 관리할 지표다 | 베이스 4.3%(과소) ↔ GPT-4.1 20.2%(과잉), 목표 12% | I5 `tag_abstention` + 기권율 분모 분리 계측 | 즉시 |
| **Decomposition Dilemmas** (2024) · https://arxiv.org/abs/2411.02400 | 분해된 하위 주장 수가 원본 문장 수를 넘지 않을 때 성능이 가장 좋다 | (실증) | I3이 문장 단위에서 멈추고 그 아래로 안 쪼개는 근거 | 즉시 |
| **Adding Error Bars to Evals** (Anthropic 2024) · https://arxiv.org/abs/2411.00640 | 같은 원문서 파생 문항엔 군집 표준오차, 두 시스템 비교엔 대응표본 차이 | 18문항 이진 지표 95% CI ≈ ±23%p | §7 채점 보고 형식 / E7 `eval/stats.py` | 즉시(원칙) |
| **AutoNuggetizer** (SIGIR 2025) · (ID 미확인) | vital/okay 등급 너겟의 support 비율로 근거완전성을 잰다. 문항 단위 자동 점수 상관은 Kendall τ 0.490까지 하락 | τ 0.490 (→ 소규모에선 사람이 너겟 확정) | §7 너겟 정답표 + `V_recall` | 즉시 |
| **FinGround** (ACL 2026 Industry) · https://arxiv.org/abs/2604.23588 | 지지되지 않은 주장을 삭제하지 말고 인용을 붙여 다시 쓰는 verify-then-ground | 검증 단독 68% 감소 → 재작성 병행 78% 감소 | E1 `targeted_revise` (삭제 대신 재작성 채택의 직접 근거) | 평가셋후 |
| **RARR** (ACL 2023) · https://arxiv.org/abs/2210.08726 | 생성 답변을 버리지 않고 미지원 부분만 외과적으로 편집 | 귀속 최대 +13%p, 원문 10~20%만 변경, 원 의도 보존 90%+ | E1 재작성 프롬프트 설계(통과 문장 불변 조항) | 평가셋후 |
| **Chain-of-Verification** (ACL 2024 Findings) · https://arxiv.org/abs/2309.11495 | factored 검증 필수 — 초안을 같이 보여주면 자기 환각을 반복한다 | Wikidata 정밀도 0.17→0.36, 환각 엔티티 2.95→0.68, FACTSCORE 55.9→71.4 | E1 제3자 프레임 / §5 자기 감사 기각 근거 | 평가셋후 |
| **Conformal Factuality** (ICML 2024) · https://arxiv.org/abs/2402.10978 | 지지 안 되는 하위 주장만 삭제·일반화하는 백오프 | 80~90% 정확성 유지하며 출력 대부분 보존 | E1 부분 기권 폴백(전면 기권 금지) | 평가셋후 |
| **Citation Failure / CITECONTROL** (TACL 2025) · https://arxiv.org/abs/2510.20303 | 어텐션 기반 인용은 모델 내부 접근 필수 → REST API 전용 환경에선 검색 기반이 우세 | 벤치마크에서 검색 기반 > 어텐션 기반 | E3 `reattribute()` (API 전용 환경에서 이 방법을 쓸 근거) | 평가셋후 |
| **FullCite** (2026) · https://arxiv.org/abs/2606.07130 | 사후 정렬(posthoc alignment)만으로 인용 스팬 품질 급상승. 인용의 81.8%가 앞 두 근거에 몰리는 위치 편향 | Snippet-F1 12.80 → 61.87 | E4 인용구 정렬 폴백 / I1 근거ID 분포 모니터링 | 평가셋후 |
| **Deterministic Quoting** (Yeung 2024, 기술 블로그) · (ID 없음) | 인용 텍스트를 LLM이 쓰게 하지 말고 코드가 원문에서 되꺼낸다 | 인용부 환각 0%, 주변 서술 환각 12%→2% | E4 원문 스팬 치환 | 평가셋후 |
| **Show Your Work** (medRxiv 2026) · (ID 미확인) | 인용구 요구 자체가 기권을 늘리고 일부 모델 정확도를 떨어뜨린다 | 기권 13.8→15.7%, 정확도 78.5→72.5% (p=0.009), 기계적 유효 인용 중 의미 타당 48.0~78.8% | E4를 A/B 필수로 묶은 근거 (**(B) 재발 경고**) | 평가셋후 |
| **Know Your Limits** (TACL 2025) · (ID 미확인) | 부분 기권(partial abstention) — 답변과 기권이 한 출력 안에 공존 | (유형 정의) | E5 3슬롯 형식 | 평가셋후 |
| **Lost in the Middle** (TACL 2024) · https://arxiv.org/abs/2307.03172 | 긴 컨텍스트에서 중간 위치 정보의 활용률이 U자로 떨어진다 | (U자 곡선) | I6 청크별 명시 검토 / E6 청크별 분리 호출 | 평가셋후 |
| **AbstentionBench** (2025) · https://arxiv.org/abs/2506.09038 | 기권 능력과 추론·서술 능력은 서로를 잡아먹는 구조적 긴장 | 추론 파인튜닝이 기권 성능 평균 -24% | §2.5 진자 고정 장치의 이론적 배경 | 원칙 |
| **RAGChecker** (NeurIPS 2024) · https://arxiv.org/abs/2408.08067 | 근거에 없지만 사실인 것(self-knowledge)과 어디에도 없는 것(hallucination)을 분리 | claim 단위 함의 검사 사람 상관 Pearson 61.93 (RAGAS 41.07) | §7 사람 라벨 체계(지지/부분지지/셀프지식/환각) | 즉시 |
| **With Little Power Comes Great Responsibility** (EMNLP 2020) · https://arxiv.org/abs/2010.06595 | 저전력 실험은 노이즈를 개선으로 보고하게 만든다 | (체계적 실증) | E7 McNemar + §7 "18문항은 성능 측정이 아니라 회귀 방지 도구" | 평가셋후 |
| **TAT-LLM / PoT / FinanceBench** (기존 적용) | 약한 모델 + 고정 파이프라인, 계산의 코드 위임, 실패의 81%는 검색 | PoT 금융 데이터셋 +24.1%p | 전 파이프라인 구조(PAPERS.md 1·2번, 유지) | 적용 완료 |

---

## 7. Open형 평가셋 설계

### 7.1 규모와 구성

18문항(6유형 × 3문). 그중 **유형②④⑥이 9문항**이고 이 문서의 표적이다. 나머지 9문항(①③⑤)은 회귀 방지용이다 — Open형을 고치다 Closed형을 망가뜨리지 않았는지 확인한다.

18문항 중 **3~4문항은 근거로 답할 수 없게 설계한다.** 코퍼스에 없는 연도, 존재하지 않는 항목, 배경 근거가 실제로 비어 있는 케이스다. 이것이 평가지표 '정보한계 대응'과 (B)의 반대편(정당한 기권)을 측정하는 축이다. FinRAG-12B가 학습 데이터의 22%를 답변 불가로 구성해 기권율을 관리했던 것과 같은 비율이다.

### 7.2 유형별 문항 설계 원칙

**유형② (검색추출 Open — 단일 문서 정리·요약)**
- 서술이 길어질 수밖에 없는 질문을 고른다("주요 사업 내용", "리스크 요인"). 홍보성 상용구가 유입될 여지가 큰 질문이 (C)를 재현하는 문항이다.
- 문항마다 **투입될 상위 5청크를 미리 뽑아 저장해 둔다.** 채점 시 "코퍼스엔 있지만 투입분엔 없는" 표현을 판별하려면 투입분 스냅샷이 있어야 한다.
- 3문항 중 1문항은 근거가 빈약한 소형 기업으로 잡아 과잉 서술 유혹을 만든다.

**유형④ (다중수집 Open — 여러 건 수집·분류)**
- 개수 계약이 이미 효과를 낸 영역이므로 **회귀 방지가 주 목적**이다. TODO.md에 이미 후보가 적혀 있다. 신한지주 2024 자금조달(발행 3건 + 정정 후 값), 두산퓨얼셀 체결 후 해지(4건), 한미반도체 신탁계약(해지 7건).
- 실측된 미해결 결함을 문항에 반영한다. 두산퓨얼셀은 **개수 채우기 패딩**(해지되지도 않은 건을 "해지: 없음"으로 끼워넣음)을, 한미반도체는 **짝 없는 해지 1건 누락**을 잡는 문항이다.

**유형⑥ (복합추론 Open — 같은 기업 두 연도 대조)**
- **핵심 문항이자 이 설계의 표적.** LG에너지솔루션 2023 vs 2024 매출액 문항은 (A)(B) 둘 다 실측된 케이스이므로 **반드시 포함하고, 이 문항의 답변은 매 변경마다 육안으로 읽는다.**
- 나머지 2문항 중 1문항은 **배경 근거가 실제로 비어 있는 케이스**로 설계한다. 충분성 게이트의 '불충분' 분기가 정상 작동하는지, 그리고 그때의 기권이 정당한 기권으로 채점되는지를 본다. 이 문항이 없으면 "기권을 줄였다"가 곧 개선인지 퇴보인지 구분할 수 없다.

### 7.3 너겟 정답표 — 근거완전성 점수화

각 문항에 사람이 원자 사실(너겟) 8~15개를 직접 적고 `vital`(없으면 오답) / `okay`(있으면 가점) 등급을 매긴다. 각 너겟에는 문자열 대조 키의 표기 변형을 함께 등록한다. **모든 너겟에는 출처 `rcept_no`를 적어 검증 가능하게 만든다** — 근거에 실제로 없는 사실을 vital로 등록하면 채점 전체가 오염되기 때문이다.

```jsonc
// eval/evalset.jsonl — 유형⑥ 대표 문항
{"question_id": "T6-01", "qtype": 6, "answerable": true,
 "question": "LG에너지솔루션의 2023년과 2024년 매출액을 비교하고 변화 배경을 설명해줘",
 "nuggets": [
   {"id":"N1","grade":"vital","desc":"2023년 매출 33조7455억",
    "keys":["337,455","33조7455","33조 7,455"],"rcept_no":"2024xxxxxxxxxx"},
   {"id":"N2","grade":"vital","desc":"2024년 매출 25조6196억",
    "keys":["256,196","25조6196"],"rcept_no":"2025xxxxxxxxxx"},
   {"id":"N3","grade":"vital","desc":"감소폭 8조1259억","keys":["81,259","8조1259"],"derived":true},
   {"id":"N4","grade":"vital","desc":"메탈 가격 하락 언급","keys":["메탈 가격","메탈가격","판가"],
    "rcept_no":"2024xxxxxxxxxx"},
   {"id":"N5","grade":"okay","desc":"북미 수요 관련 언급","keys":["북미"],"rcept_no":"2024xxxxxxxxxx"}],
 "forbidden": ["고금리","고물가","니켈","소비 심리","전기차 수요 둔화"]}
```

`forbidden` 필드가 이 평가셋의 고유 장치다. (A)에서 실제로 지어낸 표현을 문항에 박아 두고, 답변에 등장하면 **환각 확정 검거**로 센다. 자동 검거가 가능한 유일한 환각 축이다.

### 7.4 두 축 점수와 2×2 스코어카드

```python
# eval/score_nuggets.py
def norm(t):
    return re.sub(r"[\s,]", "", t or "")

def nugget_hit(answer, n):
    return any(norm(k) in norm(answer) for k in n["keys"])

def score_run(run, items):
    rows = []
    for rec, it in zip(run, items):
        vit = [n for n in it["nuggets"] if n["grade"] == "vital"]
        hit = {n["id"]: nugget_hit(rec["answer"], n) for n in it["nuggets"]}
        forb = [w for w in it.get("forbidden", []) if norm(w) in norm(rec["answer"])]
        rows.append({
            "qid": it["question_id"], "qtype": it["qtype"],
            "V_recall": sum(hit[n["id"]] for n in vit) / len(vit) if vit else None,
            "missed_vital": [n["desc"] for n in vit if not hit[n["id"]]],
            "forbidden_hit": forb,
            "abstain": tag_abstention(rec["answer"]),
            "latency": rec.get("latency")})
    return rows
```

| 축 | 지표 | 계산 | 대회 평가지표 대응 |
|---|---|---|---|
| **근거완전성 (기권의 비용)** | `V_recall` | vital 너겟 히트 수 / vital 총수 | 근거완전성, 요구충족 |
| **환각방지 (서술의 비용)** | `귀속률` = 지지 문장 수 / (지지+애매+미검증) | `attribute.py` 라벨 집계, 사람 확정 | 환각방지, 정확성 |
| **환각 확정 검거** | `forbidden_hit` | 금지 표현 등장 건수 | 환각방지 |
| **기권 적정성** | `abstain` × `answerable` | answerable=false에서 기권 = 정답 | 정보한계 대응 |
| **논리 흐름** | 사람 육안 | 삭제·재작성 후 문장 연결 | 논리성 |

**2×2 스코어카드 — 이것이 진자 계기판이다.**

```
                    답변이 서술함        답변이 기권함
                 +-------------------+-------------------+
 근거에 있음      |   정상 (득점)      |  (B) 과잉기словие   |
 (충분 판정)      |   V_recall 상승    |  missed_vital 증가 |
                 +-------------------+-------------------+
 근거에 없음      |  (A) 환각          |   정상 (득점)      |
 (불충분 판정)    |  미검증문장·금지어  |  정보한계 대응 만점 |
                 +-------------------+-------------------+
```

`합계손실 = (B)칸 건수 + (A)칸 건수`. 프롬프트를 고칠 때마다 **두 칸을 동시에 본다.** 한 칸만 줄고 다른 칸이 늘면 개선이 아니라 진자가 이동한 것이다. 이 표를 안 만든 것이 (A)→(B) 사고의 진짜 원인이다.

### 7.5 채점 절차와 통계 보고

1. `eval/run_eval.py`로 18문항을 배치 실행하고 지연까지 기록해 `eval/runs/run_MMDD_HHMM.json`에 저장한다.
2. `eval/score_nuggets.py`로 `V_recall`과 `forbidden_hit`을 자동 산출한다.
3. `attribution.py`로 문장별 자동 라벨 TSV를 뽑고, **사람이 `human` 컬럼을 채운다.** 라벨 체계는 `지지 / 부분지지 / 셀프지식(근거 밖이지만 세상에서는 사실) / 환각(근거에도 사실에도 없음)`이다. (A)의 '전기차 수요 둔화'는 정확히 **셀프지식**으로 분류된다.
4. `V_recall`에서 미히트로 나온 문항은 **반드시 사람이 답변을 직접 읽고 확정한다.** 문자열 키 매칭은 의미 판정의 근사라 패러프레이즈('약 33조 7천억')를 놓친다.
5. 두 버전 비교는 `eval/stats.py`의 McNemar로 판정하고, 지지율은 `62% ± 9%p (n=18문항/187문장, 문항 군집 SE)` 형식으로만 보고한다.

### 7.6 이 평가셋으로 할 수 없는 것 (스스로 못박아 둘 것)

**18문항은 성능 측정 도구가 아니라 실패 모드 탐지·회귀 방지 도구다.** 문항 단위 이진 지표의 95% 신뢰구간이 대략 ±23%p이므로, 20%p 미만의 차이를 "개선했다"고 말하면 통계적으로 거짓말이 된다. 문장 단위로 내려가면 표본이 늘지만 같은 문항의 문장들은 독립이 아니라 유효 표본은 문장 수보다 작다. 그래서 이 평가셋으로 정당하게 주장할 수 있는 것은 세 가지뿐이다.

1. **금지 표현이 사라졌는가** (`forbidden_hit`이 0인가) — 이것은 이진 사실이라 표본 크기와 무관하다.
2. **vital 너겟을 통째로 놓친 문항이 있는가** — (B)의 존재는 한 건만 있어도 실패다.
3. **think_trace의 검증 기록이 실제로 남는가** — 제출물 자체의 품질 근거다.

"평균 점수가 올랐다"는 이 규모에서 증명 불가능하다. 그 주장을 하고 싶으면 문항을 40개 이상으로 늘려야 한다.

---

## 부록 — 오늘 착수 순서 (체크리스트)

| 순서 | 작업 | 파일 | 같은 커밋으로 묶을 것 |
|---|---|---|---|
| 1 | 근거 번호표 통일 + `chunks` 반환 필드 추가 | `slice1.py` `slice3.py` `slice6.py` | ← 2와 묶음 **필수** |
| 2 | `pick_cause_sentences` + 충분성 게이트 + SYSTEM6 대칭 채점표 | `slice6.py` | ← 1과 묶음 **필수** |
| 3 | `attribute.py` 신설 (어휘 1차 / 임베딩 2차, 경고 전용) | 신설 | |
| 4 | `check_facts` `check_promo` `tag_abstention` `check_citation_format` | `verify.py` | |
| 5 | `clean(r, qtype, question)` 재배선 + 2×2 계기판 로깅 + 제출 필드 화이트리스트 | `main.py` | ← 3·4와 묶음 |
| 6 | 유형② 읽기 노트 | `slice1.py` `main.py` | |
| 7 | `gather_bg` item=None 가드 등 부수 버그 | `slice6.py` `slice3.py` | |
| 8 | 평가셋 18문항 + 너겟 정답표 작성 (사람 작업, 문항당 20~30분) | `eval/` | |
| 9 | baseline 측정 → 임계값 캘리브레이션 → **그 다음에야** 3단(E1) 착수 | `eval/` | |

**절대 지킬 것 두 가지.**
첫째, 1과 2는 반드시 같은 커밋이다. 둘 중 하나만 배포하면 (A) 또는 (B)가 확정적으로 재발한다.
둘째, 9번 이전에 어떤 삭제·차단·재작성도 켜지 마라. 측정 없이 켠 검증기의 오탐은 (B)의 코드 버전이고, 그때는 프롬프트를 되돌리는 것으로도 복구되지 않는다.
---

## 📌 구현 현황 (2026-08-09 새벽 기준)

이 문서의 **I3(attribute.py)**은 이미 만들어 뒀고 자체 시험 4종을 통과했다.
`cd agent && python attribute.py`로 바로 확인할 수 있다.

| 시험 | 결과 |
|---|---|
| 사례 (A) 환각 답변 4문장 | **3문장 적발** (고금리·구매심리 실존율 0.12, 니켈·원자재 0.30) |
| 근거에 충실한 답변 5문장 | **오탐 0** |
| 전부 무근거인 답변 | 3문장 적발 → 상한 초과 → **원문 보존 + 주의 문구** (과잉 기권 재현 안 함) |
| build_context ↔ parse_ev 왕복 | 번호·본문 일치 |

**구현하면서 실측으로 정한 것 세 가지 (설계에 없던 보완)**

1. **수치는 실존율 계산에서 제외했다.** 답변은 "25조 6,196억원", 원문은 "25,619,585 백만원"이라
   단위 변환·반올림 때문에 문자열 대조가 멀쩡한 수치를 오탐한다(1차 시험에서 오탐 1건 실제 발생).
   수치 검증은 `verify.check_facts`가 따로 맡는 것이 맞다.
2. **3자 이상 한글은 2자 어간까지 인정한다.** '매출액'↔'매출', '고성장'↔'성장' 같은 파생 차이
   오탐을 줄인다. 이 완화를 넣자 시험 2의 오탐이 1건 → 0건이 됐다.
3. **내용어 2개짜리 짧은 문장도 실존율 0이면 적발한다.** "반도체 업황이 나빠졌습니다" 같은
   짧은 환각이 기능 문장 면제 규칙으로 새는 것을 막는다.

**형식은 설계 I1을 따랐다.** 근거 번호표는 `[근거 i/n]`(slice4가 이미 쓰는 형식),
문단 귀속 표기는 `(근거 N)`. **문장 단위 인용 강제는 설계 §2.4의 결론대로 기각했다** —
그것이 (B)를 만든 개입이기 때문이고, 진짜 판정은 `check_grounding()`이 모델 표기와 무관하게
독립 재계산한다. 모델이 번호를 안 붙여도 전체 근거 대조로 폴백해서 그대로 돈다.

**다음 행동**: 설계 §3의 I1·I2를 slice6에 먼저 적용하고 LG엔솔 ⑥ 질문을 다시 쏜다.
기대 결과는 배경 서술이 되살아나면서((B) 해소) 지어낸 문장은 think_trace에 귀속경고로
찍히는 것((A) 탐지)이다. 그 결과를 보고 slice1·3으로 확산한다.

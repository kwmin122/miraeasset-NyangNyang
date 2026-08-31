# RUNBOOK — 사수 기간 운영 매뉴얼 (9/7~9/20) + 프리즈·철수 체크리스트

> **원칙: 프리즈(9/6) 후엔 고치지 않는다 — 재기동만 한다.**
> 새벽 2시에 잠 덜 깬 상태로 위에서 아래로 따라 하면 되도록 명령어 원문을 그대로 적는다.
> `⟨NCP-IP⟩` 등 ⟨꺾쇠⟩ 자리는 S7 배포 완료 시 실값으로 치환한다 (치환 전에는 이 문서는 골격 상태).
>
> *2026-08-08 Fable 초안 (S8 문서분 선작성 — NCP 불필요분). 리허설·실측 수치는 S8 본작업에서 채움.*

## 0. 시스템 한눈에

- **무엇**: `GET /answer?question=...&question_id=...` → 항상 200 + 5필드 JSON (question_id, question, retrieved_context, think_trace, answer)
- **어디**: NCP 서버 `⟨NCP-IP⟩` (2vCPU/4GB, x86_64), HTTP 포트 `⟨포트: 주최측 공지, 미공지 시 80⟩`
- **구성**: Docker 컨테이너 1개 (`--restart unless-stopped`), 내부는 FastAPI + FAISS(SQ8) + bf16 임베딩 + HCX-005
- **강등 사다리**: HCX 성공(`[HCX 사용]`) → HCX 실패 시 추출형 폴백(`[폴백 사용]`) → 에이전트 예외 시 main.py 최후방어(LIMIT_ANSWER) — **어떤 경우에도 200 + 5필드는 유지된다. 5xx가 보이면 그건 컨테이너가 죽은 것.**
- **로그**: 컨테이너 내부 `logs/requests.jsonl`(요청·응답 전문), `logs/hcx_usage.jsonl`(HCX 호출 결과·토큰)

## 1. 매일 10분 루틴 (사수 기간 아침마다)

```bash
# ① 살아 있나 (밖에서 — 평가자와 같은 경로)
curl -s http://⟨NCP-IP⟩:⟨포트⟩/ready
# 기대: {"status":"ready"}  — 이거 아니면 §3-A로

# ② 실질 응답 1발 (밖에서)
curl -s "http://⟨NCP-IP⟩:⟨포트⟩/answer?question=삼성전자+최근+공시+알려줘&question_id=daily-check" | head -c 300
# 기대: 5필드 JSON. answer가 비어있지 않을 것

# ③ 서버 들어가서 (SSH — 본인 IP에서만 접속됨)
ssh ⟨계정⟩@⟨NCP-IP⟩
docker ps                                   # STATUS가 Up이고 최근 재시작 흔적 확인 (Restarting 반복이면 §3-A)
docker inspect gongsi --format '{{.State.OOMKilled}} {{.RestartCount}}'   # false + 재시작 횟수 추이
df -h /                                     # 디스크 여유 (로그 누적 — 80% 넘으면 §3-D)
docker exec gongsi tail -3 logs/requests.jsonl   # 최근 요청에 error 필드 null인지

# ④ HCX 상태 (컨테이너 안 로그로)
docker exec gongsi tail -5 logs/hcx_usage.jsonl  # success:false 연속이면 §3-B

# ⑤ NCP 콘솔에서 크레딧 잔량 확인 (과금 초과 = 서버 정지 위험)
```

체크 결과는 팀 채팅에 "MM/DD ready OK / HCX OK / 디스크 nn%" 한 줄 보고.

## 2. 기동·재기동 명령 원문

```bash
# 재기동 (프리즈 후 유일하게 허용되는 조치)
docker restart gongsi
# 워밍업 대기 (imbedding 로딩 — 로컬 실측 ~7s, NCP 실측치로 갱신: ⟨S7 실측⟩)
sleep 15 && curl -s http://localhost:⟨포트⟩/ready

# 컨테이너가 아예 없을 때 (서버 재부팅 직후 --restart가 못 살린 경우)
docker run -d --name gongsi --restart unless-stopped \
  --cpus=2 --memory=4g --memory-swap=4g \
  -p ⟨포트⟩:8000 \
  --env-file /home/⟨계정⟩/.env \
  -v /home/⟨계정⟩/data:/app/data:ro \
  gongsi-agent:freeze-0906
# ⟨S7에서 실제 run 명령 확정 후 이 블록을 실값으로 교체할 것⟩

# 절대 하지 말 것: docker build, git pull, 코드 수정, .env 수정(키 재발급 등 비상시 제외)
```

## 3. 장애 시나리오별 대응

### A. `/ready`가 503이거나 접속 불가
1. `ssh ⟨계정⟩@⟨NCP-IP⟩` → `docker ps -a`
2. 컨테이너 Up인데 503 → 워밍업 중일 수 있음. 2분 기다렸다 재확인. 계속 503이면 `docker logs --tail 50 gongsi`에서 `warmup_error` 확인 → `docker restart gongsi`
3. 컨테이너 Exited/Restarting → `docker inspect gongsi --format '{{.State.OOMKilled}}'`
   - `true`(OOM): `docker restart gongsi` 후 관찰. 반복되면 runbook "알려진 제약"에 기록하고 재기동 루프로 버틴다 (프리즈 후 코드 수정 금지)
   - `false`: `docker logs --tail 100 gongsi`로 사인 확인 → 재기동
4. SSH 자체가 안 됨 → NCP 콘솔 웹에서 서버 상태 확인 → 콘솔 재부팅 → 부팅 후 §2 "컨테이너가 아예 없을 때" 확인
5. NCP 장애 공지(status.ncloud.com) 확인 — 인프라 장애면 **기다린다** (우리가 할 수 있는 게 없음, 복구 후 /ready만 재확인)

### B. HCX 오류 연속 (429/타임아웃/5xx)
- **서버는 자동으로 추출형 폴백으로 강등되므로 /answer는 계속 200이다. 당황해서 서버를 건드리지 말 것.**
- `docker exec gongsi tail -20 logs/hcx_usage.jsonl`로 error_type 분포 확인
- CLOVA Studio 콘솔에서 크레딧·rate limit 상태 확인
- 키 만료/폐기가 원인일 때만: 서버에서 직접 `.env` 수정(새 키) → `docker restart gongsi` (키는 여전히 카톡·git 금지)

### C. 응답은 200인데 품질이 이상함
- think_trace의 `[HCX 사용]`/`[폴백 사용]` 태그로 강등 여부부터 확인 (`docker exec gongsi grep -c "폴백 사용" logs/requests.jsonl`)
- 강등이면 §B. 강등이 아니면 **아무것도 하지 않는다** — 프리즈 후 프롬프트·코드 수정 금지, "알려진 제약"에 기록만

### D. 디스크 80% 초과
```bash
docker exec gongsi sh -c 'ls -lh logs/ && tail -c 100m logs/requests.jsonl > logs/requests.tmp && mv logs/requests.tmp logs/requests.jsonl'
docker system prune -f          # 미사용 이미지·빌드캐시만 정리 (실행 중 컨테이너는 안전)
```

## 4. 9/6 프리즈 체크리스트

- [ ] 전 문항 최종 채점: 로컬 dev 30 + blind 6, NCP 외부 경유(`python3 evalset/run_eval.py --base http://⟨NCP-IP⟩:⟨포트⟩`) — 로컬과 통과율 동일 확인
- [ ] `git tag freeze-0906 && git push origin freeze-0906` (이후 main 변경 금지)
- [ ] **★ 에이전트 모드 확인 (제일 먼저)**: `curl -s ⟨공인IP⟩:⟨포트⟩/health` → `{"status":"ok","agent_mode":"sunwoo"}`
      `mock`이면 더미 응답이라 **전 문항 0점**, `baseline`이면 선우 모듈이 안 도는 상태다.
      `config.py` 기본값이 `mock`이고 배포는 손으로 쓴 `.env`를 읽으므로, 그 한 줄을 빠뜨리면 조용히 이렇게 된다.
      (Dockerfile에 `ENV AGENT_MODE=sunwoo`를 박아 뒀지만 `--env-file`이 덮으므로 실물로 확인할 것)
- [ ] NCP의 이미지가 freeze 커밋 기준 빌드인지 확인 (`docker inspect gongsi --format '{{.Config.Image}}'` + 빌드 시점 기록)
- [ ] `--restart unless-stopped` 확인: `docker inspect gongsi --format '{{.HostConfig.RestartPolicy.Name}}'`
- [ ] **재부팅 생존 실증**: NCP 콘솔 재부팅 → 자동 복구 → /ready 200 (1회 실연)
- [ ] 제출 폼: 공인 IP·포트·엔드포인트 경로(`/answer`)·팀 정보 — 제출 직전 브라우저에서 최종 curl 1발
- [ ] `.env`가 git·이미지 히스토리에 없는지 최종 확인: `git log --all --full-history -- .env` 0건, `docker history gongsi-agent:freeze-0906 | grep -i env` 확인
- [ ] blind 6문(`questions_blind.jsonl`)·`candidates_s4.jsonl` 로컬 백업 존재 확인 (git외 유일본)
- [ ] 사수 기간 일정 공유: 매일 루틴 담당(기본: 민경욱), 부재 시 대타(§1을 그대로 전달)

## 5. 9/30 철수 체크리스트 (크레딧 소진 전 — 순서 중요)

- [ ] 최종 로그 회수: `scp ⟨계정⟩@⟨NCP-IP⟩:~/logs-final.tar.gz .` (`docker exec gongsi tar czf - logs > logs-final.tar.gz`)
- [ ] (원하면) 서버 스냅샷/이미지 백업 — 과금 확인 후 결정, 불필요하면 생략
- [ ] 서버 인스턴스 **반납/삭제** (정지 아님 — 정지는 과금 지속될 수 있음)
- [ ] 블록 스토리지·스냅샷·공인 IP 등 부속 리소스 삭제 (공인 IP는 서버 삭제 후에도 별도 과금)
- [ ] NCP 콘솔 청구 화면에서 **예상 과금 0원** 확인 (다음날 재확인)
- [ ] CLOVA Studio 키 폐기 (콘솔에서 revoke)

## 6. S7 배포 시 함정 목록 (S6 검수에서 이관 — 배포 담당은 여기부터 읽을 것)

1. **`server/artifacts/`는 git에 없다** — NCP에서 `server/tools/build_sq8_index.py` + `build_chunk_meta_sqlite.py`로 **반드시 재생성** 후 빌드. Dockerfile의 `COPY server/artifacts ./artifacts`는 디렉토리가 없으면 빌드 실패, **비어 있으면 조용히 fp32 폴백 → 4GB에서 OOM** (기동 3.8초 사망이 그 증상)
2. **torch CPU 핀**: requirements에 CPU 전용 torch로 핀 고정해서 빌드 (미핀 시 CUDA 패키지 오설치 → 이미지 11.1GB·빌드 수십 분)
3. **x86_64 재검증**: colima(aarch64) 실측은 참고치일 뿐 — NCP에서 bf16 동작·`memory.peak`(cgroup)·docker stats를 다시 재고 LOG에 기록 (로컬 기준치: stats 2.9~3.1GiB, peak ~3.8GiB)
4. `.env`는 서버에서 직접 작성 (전송 채널·이미지·git에 태우지 않음), `data/`는 rsync 후 체크섬 대조
5. 로컬 colima에서는 호스트→컨테이너 포트포워딩 curl이 안 됐음(colima 버그) — NCP에서는 정상이어야 하며, 만약 밖에서 안 열리면 ACG(방화벽)부터 볼 것

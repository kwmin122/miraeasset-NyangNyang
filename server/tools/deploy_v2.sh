#!/usr/bin/env bash
# v2 임베딩 + 최신 앱 코드 배포 (S8). 로컬에서 실행한다. 멱등 — 끊기면 다시 돌리면 된다.
#
# 무엇을 바꾸나:
#   ① /srv/build/server/{app,vendor,tools,requirements.txt,Dockerfile}  <- 레포 최신 (rsync)
#      A3 Query("") 수정과 선우 A6/B1/B2/B4 수정이 여기로 들어간다. 이걸 빼면
#      v2 산출물만 새것이고 코드는 S7 시절 것인 이미지가 나온다.
#   ② /srv/data/share_embeddings/out/chunk_meta.jsonl  (볼륨 마운트, 1.05GB)  v1 -> v2
#   ③ /srv/data/share_embeddings/search_lib.py         (볼륨 마운트, 3.2KB)   v1 -> v2
#   ④ /srv/build/server/artifacts/{index_sq8.faiss,chunk_meta.sqlite}         v1 -> v2
#      -> Dockerfile:14가 이미지에 굽는다. 그래서 docker build 가 필요하다.
#   ⑤ 이미지 재빌드 후 컨테이너 교체
#
# ②와 ④는 반드시 같이 간다. 인덱스 포지션(0..258,458)이 세 파일에 걸쳐 정합해야 하고,
# 하나만 바꾸면 _LazyChunkMeta 가 IndexError 를 내는데 그게 선우 폴백 경로로 흘러
# "틀린 답"으로만 보인다(에러로 안 보인다). 배포 후 게이트를 반드시 확인할 것.
#
# 업로드는 /srv/stage 에 받는다. /srv/build 는 도커 빌드 컨텍스트라
# .new/.v1 찌꺼기를 두면 COPY server/artifacts 가 그것까지 이미지에 굽는다.
#
# 롤백은 이전 이미지 gongsi-agent:s7 (v1 artifacts 가 구워져 있다) + share_embeddings 의 .v1 사본.
#
# 전제: SSH 22 가 열려 있어야 한다. 안 붙으면 RUNBOOK §3 A-0 (ACG myIp 버튼).
set -euo pipefail

HOST=root@49.50.143.160
KEY=~/.ssh/nyangnyangkey.pem
SSH="ssh -i $KEY -o ConnectTimeout=15"
V2=/Users/a0000/Downloads/share_embeddings_v2
REPO=/Users/a0000/orca/projects/miraeasset_server
OLD=gongsi-agent:s7
TAG=gongsi-agent:s8v2

say() { printf '\n=== %s\n' "$*"; }

say "0. 로컬 산출물 정합 (258,459 세 곳 일치)"
OMP_NUM_THREADS=1 "$REPO/.venv/bin/python3" - <<'PY'
import sqlite3, faiss, sys
from pathlib import Path
A = Path("/Users/a0000/orca/projects/miraeasset_server/server/artifacts")
n_sql = sqlite3.connect(f"file:{A/'chunk_meta.sqlite'}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM meta").fetchone()[0]
n_idx = faiss.read_index(str(A/"index_sq8.faiss")).ntotal
n_src = sum(1 for _ in open("/Users/a0000/Downloads/share_embeddings_v2/out/chunk_meta.jsonl", encoding="utf-8"))
print(f"sqlite={n_sql} faiss={n_idx} jsonl={n_src}")
sys.exit(0 if n_sql == n_idx == n_src == 258459 else 1)
PY

say "1. 롤백용 이전 이미지 확인 — 이게 없으면 되돌릴 방법이 없다"
$SSH $HOST "docker images $OLD --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep . || { echo '!!! $OLD 없음 — 롤백 불가. 중단.'; exit 1; }"

say "2. 서버 디스크 여유 (스테이징 1.7GB + 빌드 여유)"
$SSH $HOST 'df -h /; A=$(df -BG --output=avail / | tail -1 | tr -dc 0-9); echo "여유 ${A}GB"; [ "$A" -ge 8 ] || { echo "!!! 8GB 미만 — 중단"; exit 1; }'

say "3. share_embeddings v1 롤백본 (볼륨 마운트라 이미지 밖 — 빌드 컨텍스트 오염 없음)"
$SSH $HOST '
set -e
cd /srv/data/share_embeddings
[ -f out/chunk_meta.jsonl.v1 ] || cp -n out/chunk_meta.jsonl out/chunk_meta.jsonl.v1
[ -f search_lib.py.v1 ]        || cp -n search_lib.py        search_lib.py.v1
mkdir -p /srv/stage
ls -la out/ | head
'

say "4. 앱 코드 동기화 -> /srv/build  (이게 빠지면 S7 코드로 이미지가 나온다)"
rsync -av --delete -e "ssh -i $KEY" \
  --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='slot_cache.json' --exclude='slim_index.pkl' --exclude='share_embeddings' \
  "$REPO/server/app/" $HOST:/srv/build/server/app/
rsync -av --delete -e "ssh -i $KEY" --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.v1' \
  "$REPO/server/vendor/" $HOST:/srv/build/server/vendor/
rsync -av --delete -e "ssh -i $KEY" --exclude='__pycache__/' --exclude='*.pyc' \
  "$REPO/server/tools/" $HOST:/srv/build/server/tools/
rsync -av -e "ssh -i $KEY" "$REPO/server/requirements.txt" "$REPO/server/Dockerfile" $HOST:/srv/build/server/

say "5. 대용량 업로드 -> /srv/stage (빌드 컨텍스트 밖)"
scp -i $KEY "$V2/out/chunk_meta.jsonl"                 $HOST:/srv/stage/chunk_meta.jsonl
scp -i $KEY "$REPO/server/vendor/search_lib.py"   $HOST:/srv/stage/search_lib.py
scp -i $KEY "$REPO/server/artifacts/index_sq8.faiss"   $HOST:/srv/stage/index_sq8.faiss
scp -i $KEY "$REPO/server/artifacts/chunk_meta.sqlite" $HOST:/srv/stage/chunk_meta.sqlite

say "6. md5 대조 — 어긋나면 현행 무사한 채로 중단"
LOCAL_MD5=$( { md5 -q "$V2/out/chunk_meta.jsonl"; md5 -q "$REPO/server/vendor/search_lib.py"; \
               md5 -q "$REPO/server/artifacts/index_sq8.faiss"; md5 -q "$REPO/server/artifacts/chunk_meta.sqlite"; } )
REMOTE_MD5=$($SSH $HOST 'md5sum /srv/stage/chunk_meta.jsonl /srv/stage/search_lib.py \
  /srv/stage/index_sq8.faiss /srv/stage/chunk_meta.sqlite | cut -d" " -f1')
echo "local :"; echo "$LOCAL_MD5"; echo "remote:"; echo "$REMOTE_MD5"
[ "$LOCAL_MD5" = "$REMOTE_MD5" ] || { echo "!!! md5 불일치 — 중단. /srv/stage 만 지저분하고 현행은 무사하다."; exit 1; }

say "7. 원자적 교체 + 빌드 컨텍스트 청소 + 이미지 재빌드"
$SSH $HOST "
set -e
mv /srv/stage/chunk_meta.jsonl /srv/data/share_embeddings/out/chunk_meta.jsonl
mv /srv/stage/search_lib.py    /srv/data/share_embeddings/search_lib.py
mv /srv/stage/index_sq8.faiss   /srv/build/server/artifacts/index_sq8.faiss
mv /srv/stage/chunk_meta.sqlite /srv/build/server/artifacts/chunk_meta.sqlite
# COPY server/artifacts 는 폴더를 통째로 굽는다 — 찌꺼기가 있으면 이미지가 그만큼 커진다
rm -f /srv/build/server/artifacts/*.v1 /srv/build/server/artifacts/*.new
ls -la /srv/build/server/artifacts/
cd /srv/build && docker build -t $TAG -f server/Dockerfile .
"

say "8. 컨테이너 교체 (RUNBOOK §2 원문과 동일, 이미지 태그만 $TAG)"
$SSH $HOST "
set -e
docker rm -f gongsi 2>/dev/null || true
docker run -d --name gongsi --restart unless-stopped \
  --cpus=2 --memory=3400m \
  -p 80:8000 \
  --env-file /srv/.env \
  -v /srv/data/share_embeddings:/srv/data/share_embeddings:ro \
  -v /srv/data/corpus:/srv/data/corpus:ro \
  -v /srv/hf_cache:/root/.cache/huggingface \
  -v /srv/logs:/srv/logs \
  $TAG
"

say "9. 검증 게이트 — 하나라도 어긋나면 롤백"
$SSH $HOST '
set -e
ok=0
for i in $(seq 1 120); do curl -fsS http://localhost/ready >/dev/null 2>&1 && { echo "ready at ${i}s"; ok=1; break; }; sleep 1; done
[ "$ok" = "1" ] || { echo "!! FAIL: /ready 가 120s 안에 뜨지 않았다"; exit 1; }
curl -s http://localhost/health; echo
echo "-- 폴백 경보(0이어야 함: 0 아니면 메모리 다이어트 우회) --"
N=$(docker logs gongsi 2>&1 | grep -c "app.search 사용 불가" || true)
echo "app.search 사용 불가 = $N"
[ "$N" -eq 0 ] || { echo "!! FAIL: 폴백 경보 — 4GB 다이어트가 깨졌다"; exit 1; }
echo "-- 컨테이너 메모리 (<3400m) --"
docker stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.MemPerc}}"
# faiss 는 여기서 열지 않는다. docker exec 는 같은 cgroup 이라 529MB 인덱스를 한 번 더
# 올리면 살아 있는 서버를 검증하다가 OOM 으로 죽인다. 바이트 동일성은 6단계 md5 가 이미 증명했다.
echo "-- 행수 정합 (둘 다 258459, 스트리밍만) --"
docker exec gongsi python -c "
import sqlite3, sys
n_sql = sqlite3.connect(\"file:/srv/artifacts/chunk_meta.sqlite?mode=ro\", uri=True).execute(\"SELECT COUNT(*) FROM meta\").fetchone()[0]
n_jsl = sum(1 for _ in open(\"/srv/data/share_embeddings/out/chunk_meta.jsonl\", encoding=\"utf-8\"))
print(\"sqlite\", n_sql, \"/ jsonl\", n_jsl)
sys.exit(0 if n_sql == n_jsl == 258459 else 1)
" || { echo "!! FAIL: 행수가 258459 로 맞지 않는다"; exit 1; }
echo "== 9단계 게이트 전부 통과 =="
'

say "10. 밖에서 실질 응답 — 한화오션 2024Q1 (v1 이 구조적으로 못 읽던 문서)"
# 한글을 URL 에 그대로 넣으면 uvicorn 이 요청줄에서 걷어내고 400 "Invalid HTTP request" 를
# 돌려준다(앱까지 가지도 않는다). -G --data-urlencode 로 퍼센트 인코딩해서 보낸다.
curl -s --max-time 200 -G "http://49.50.143.160/answer" \
  --data-urlencode "question_id=v2-check" \
  --data-urlencode "question=한화오션 2024년 1분기 보고서 주요 내용" \
  > /tmp/v2_check.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/v2_check.json"))
print("필드:", sorted(d))
ctx = d.get("retrieved_context", "")
print("근거 길이:", len(ctx))
print("답변 앞부분:", (d.get("answer") or "")[:200].replace("\n", " "))
print("\n### 한화오션 근거 잡힘:", "한화오션" in ctx)
PY

cat <<'EOT'

=== 롤백 (점수가 떨어졌을 때) ===
ssh -i ~/.ssh/nyangnyangkey.pem root@49.50.143.160
cp /srv/data/share_embeddings/out/chunk_meta.jsonl.v1 /srv/data/share_embeddings/out/chunk_meta.jsonl
cp /srv/data/share_embeddings/search_lib.py.v1        /srv/data/share_embeddings/search_lib.py
docker rm -f gongsi
docker run -d --name gongsi --restart unless-stopped --cpus=2 --memory=3400m -p 80:8000 \
  --env-file /srv/.env \
  -v /srv/data/share_embeddings:/srv/data/share_embeddings:ro \
  -v /srv/data/corpus:/srv/data/corpus:ro \
  -v /srv/hf_cache:/root/.cache/huggingface \
  -v /srv/logs:/srv/logs \
  gongsi-agent:s7      # v1 artifacts 가 구워진 이전 이미지
sleep 60 && curl -s http://localhost/ready
EOT

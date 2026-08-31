FROM python:3.11-slim

WORKDIR /srv

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
# S6 2순위 산출물(FAISS SQ8 재양자화본, ~0.53GB) — data/ 산출물이 아닌 우리 산출물이라 이미지에 포함.
COPY server/artifacts ./artifacts
# search_patch 폴백 사본 — 볼륨 마운트된 share_embeddings에 없을 때 이것으로 동작 (app/search.py가 /srv/vendor를 sys.path에 추가).
COPY server/vendor ./vendor
# 진단 스크립트(check_memory 등) — 컨테이너 안에서 다이어트 산출물·RSS 확인용.
COPY server/tools ./tools

# 임베딩 산출물(3GB)과 모델 캐시는 이미지에 굽지 않고 볼륨 마운트:
#   docker run --cpus=2 --memory=4g -p 8000:8000 \
#     -v $(pwd)/data/share_embeddings:/srv/data/share_embeddings \
#     -v hf_cache:/root/.cache/huggingface \
#     --env-file .env -e EMB_DIR=/srv/data/share_embeddings 공시agent
# AGENT_MODE 를 이미지에 박아둔다. config.py 기본값은 mock(더미 응답)이고
# 배포는 서버에서 손으로 쓴 .env 를 --env-file 로 읽는 구조라, 그 파일에
# AGENT_MODE 한 줄을 빠뜨리면 심사에 더미 응답이 나간다(전 문항 0점).
# --env-file 값이 이 ENV 를 덮으므로 모드를 바꿀 자유는 그대로 남는다.
# 제출 전 확인: curl .../health -> {"agent_mode":"sunwoo"}
ENV AGENT_MODE=sunwoo \
    EMB_DIR=/srv/data/share_embeddings \
    LOG_PATH=/srv/logs/requests.jsonl

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

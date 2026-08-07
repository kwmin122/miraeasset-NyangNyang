FROM python:3.11-slim

WORKDIR /srv

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
# S6 2순위 산출물(FAISS SQ8 재양자화본, ~0.53GB) — data/ 산출물이 아닌 우리 산출물이라 이미지에 포함.
COPY server/artifacts ./artifacts

# 임베딩 산출물(3GB)과 모델 캐시는 이미지에 굽지 않고 볼륨 마운트:
#   docker run --cpus=2 --memory=4g -p 8000:8000 \
#     -v $(pwd)/data/share_embeddings:/srv/data/share_embeddings \
#     -v hf_cache:/root/.cache/huggingface \
#     --env-file .env -e EMB_DIR=/srv/data/share_embeddings 공시agent
ENV EMB_DIR=/srv/data/share_embeddings \
    LOG_PATH=/srv/logs/requests.jsonl

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

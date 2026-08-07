FROM python:3.11-slim

WORKDIR /srv

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app

# 임베딩 산출물(3GB)과 모델 캐시는 이미지에 굽지 않고 볼륨 마운트:
#   docker run --cpus=2 --memory=4g -p 8000:8000 \
#     -v $(pwd)/data/share_embeddings:/srv/data/share_embeddings \
#     -v hf_cache:/root/.cache/huggingface \
#     --env-file .env -e EMB_DIR=/srv/data/share_embeddings 공시agent
ENV EMB_DIR=/srv/data/share_embeddings \
    LOG_PATH=/srv/logs/requests.jsonl

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

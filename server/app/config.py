# -*- coding: utf-8 -*-
from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # mock: 파이프 검증용 가짜 에이전트 | baseline: 검색+HCX(키 없으면 추출형 폴백) | sunwoo: 선우 모듈
    agent_mode: str = "mock"

    clova_api_key: str = ""
    clova_base_url: str = "https://clovastudio.stream.ntruss.com"
    hcx_model: str = "HCX-005"

    emb_dir: Path = REPO_ROOT / "data" / "share_embeddings"
    log_path: Path = REPO_ROOT / "logs" / "requests.jsonl"

    search_k: int = 5
    # mps는 Nemotron 커스텀 모델과 libomp 충돌로 segfault → cpu 고정 (NCP도 cpu)
    search_device: str = "cpu"
    # S5a: 연결/응답 타임아웃 분리 + 재시도 횟수 (지수 backoff는 hcx.py에서 처리)
    hcx_connect_timeout_s: float = 5.0
    hcx_read_timeout_s: float = 30.0
    hcx_max_retries: int = 1
    hcx_usage_log_path: Path = REPO_ROOT / "logs" / "hcx_usage.jsonl"

    model_config = {"env_file": REPO_ROOT / ".env", "env_file_encoding": "utf-8"}


settings = Settings()

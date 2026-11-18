"""실행 설정. 모든 값은 환경변수(.env)에서 읽고, 코드에 키를 직접 적지 않는다 (NF3001)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / ".env")  # 루트에 .env를 둔 경우도 허용


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# --- 외부 API (세 곳 모두 키가 필요 없는 공개 데이터) ---
POKEAPI_BASE = os.getenv("POKEAPI_BASE", "https://pokeapi.co/api/v2")
SMOGON_STATS_BASE = os.getenv("SMOGON_STATS_BASE", "https://www.smogon.com/stats")
SHOWDOWN_DATA_BASE = os.getenv("SHOWDOWN_DATA_BASE", "https://play.pokemonshowdown.com/data")

# --- 메타 통계 기본값 (F3002: 기준 미지정 시 기본값) ---
# 6마리를 보고 3마리를 선출하는 싱글 배틀 스타디움 규칙
DEFAULT_FORMAT = os.getenv("DEFAULT_FORMAT", "gen9championsbssregmc")
DEFAULT_RATING = _int("DEFAULT_RATING", 1500)

# --- HTTP / 캐시 ---
HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 20)
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(BACKEND_DIR / ".cache")))

# --- LLM (2일차부터 사용) ---
GMS_API_KEY = os.getenv("GMS_API_KEY", "")
GMS_BASE_URL = os.getenv("GMS_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")

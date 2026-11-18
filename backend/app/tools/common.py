"""모든 Tool이 공유하는 응답 형식, 실패 코드, HTTP 호출/캐시.

- 성공: {"ok": True,  "data": {...}}
- 실패: {"ok": False, "error": {"code": "...", "message": "..."}}
Tool은 예외를 밖으로 던지지 않는다 (F3001, NF3003).
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import time
from collections import OrderedDict
from typing import Any, Callable

import requests

from app import config


class ErrorCode:
    BAD_ARGUMENT = "BAD_ARGUMENT"      # 입력이 규격과 다름 (빈 값, 형식 틀림)
    INVALID_KEY = "INVALID_KEY"        # 키가 없거나 틀림 (LLM 등 키가 필요한 API)
    NOT_FOUND = "NOT_FOUND"            # 찾는 대상이 없음 (없는 포켓몬/기술/포맷)
    NO_DATA = "NO_DATA"                # 조회는 됐지만 판단에 필요한 값이 비어 있음
    AMBIGUOUS = "AMBIGUOUS"            # 이름이 여러 대상에 걸림 → 사용자에게 되물어야 함
    UPSTREAM_ERROR = "UPSTREAM_ERROR"  # 외부 API가 정상 응답하지 않음 (5xx, 연결 실패)
    TIMEOUT = "TIMEOUT"                # 외부 API 응답 시간 초과
    UNKNOWN_TOOL = "UNKNOWN_TOOL"      # 등록되지 않은 Tool 이름
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 예상하지 못한 내부 오류


def ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def fail(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


class ToolError(Exception):
    """Tool 내부에서 실패 응답으로 바꿀 오류. safe_tool이 fail(...)로 변환한다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def safe_tool(func: Callable[..., dict]) -> Callable[..., dict]:
    """어떤 예외도 밖으로 나가지 않도록 감싼다."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> dict:
        try:
            return func(*args, **kwargs)
        except ToolError as e:
            return fail(e.code, e.message)
        except Exception as e:  # noqa: BLE001 - 마지막 방어선
            return fail(ErrorCode.INTERNAL_ERROR, f"{type(e).__name__}: 내부 처리 중 오류가 발생했습니다.")

    return wrapper


def to_id(text: str) -> str:
    """Smogon/Showdown 식별자 규칙: 영문 소문자+숫자만 남긴다. 'Garchomp-Mega-Z' -> 'garchompmegaz'"""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ---------------------------------------------------------------------------
# HTTP + 캐시
# ---------------------------------------------------------------------------
_session = requests.Session()
_session.headers["User-Agent"] = "poke-agent/0.1 (educational project)"
_mem_cache: "OrderedDict[str, Any]" = OrderedDict()
_MEM_MAX = 16


def _mem_get(key: str) -> Any:
    if key in _mem_cache:
        _mem_cache.move_to_end(key)
        return _mem_cache[key]
    return None


def _mem_put(key: str, value: Any) -> None:
    _mem_cache[key] = value
    _mem_cache.move_to_end(key)
    while len(_mem_cache) > _MEM_MAX:
        _mem_cache.popitem(last=False)


def http_get(url: str, *, ttl: int = 0, as_text: bool = False) -> Any:
    """GET 후 JSON(또는 텍스트)을 반환. ttl>0이면 디스크에 캐시한다.

    실패는 ToolError(NOT_FOUND / TIMEOUT / UPSTREAM_ERROR)로 올린다.
    PokeAPI 공정 이용 정책(로컬 캐시 권장)과 Smogon 대용량 파일 재다운로드 방지를 위해 캐시를 둔다.
    """
    key = hashlib.sha1(url.encode()).hexdigest()
    cached = _mem_get(key)
    if cached is not None:
        return cached

    path = config.CACHE_DIR / f"{key}.{'txt' if as_text else 'json'}"
    if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
        raw = path.read_text(encoding="utf-8")
        value = raw if as_text else json.loads(raw)
        _mem_put(key, value)
        return value

    try:
        r = _session.get(url, timeout=config.HTTP_TIMEOUT)
    except requests.Timeout:
        raise ToolError(ErrorCode.TIMEOUT, "외부 데이터 서버 응답이 지연되고 있습니다.")
    except requests.RequestException:
        raise ToolError(ErrorCode.UPSTREAM_ERROR, "외부 데이터 서버에 연결하지 못했습니다.")

    if r.status_code == 404:
        raise ToolError(ErrorCode.NOT_FOUND, "요청한 대상이 외부 데이터에 없습니다.")
    if r.status_code in (401, 403):
        raise ToolError(ErrorCode.INVALID_KEY, "외부 API 인증에 실패했습니다.")
    if r.status_code >= 400:
        raise ToolError(ErrorCode.UPSTREAM_ERROR, f"외부 데이터 서버가 오류를 반환했습니다 (HTTP {r.status_code}).")

    if as_text:
        value: Any = r.text
    else:
        try:
            value = r.json()
        except ValueError:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, "외부 데이터 응답 형식이 올바르지 않습니다.")

    if ttl > 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text, encoding="utf-8")
    _mem_put(key, value)
    return value

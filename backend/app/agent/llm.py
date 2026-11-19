"""LLM 연결 (SSAFY GMS, OpenAI 호환). 키가 없으면 LLMUnavailable(INVALID_KEY)을 올린다.

테스트에서는 set_llm_factory로 가짜 모델을 끼워 넣는다.
"""
from __future__ import annotations

from typing import Any, Callable

from app import config
from app.tools.common import ErrorCode


class LLMUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _default_factory() -> Any:
    if not config.GMS_API_KEY:
        raise LLMUnavailable(ErrorCode.INVALID_KEY,
                             "LLM API 키가 설정되지 않았습니다. backend/.env에 GMS_API_KEY를 넣고 서버를 다시 시작하세요.")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config.LLM_MODEL,
        api_key=config.GMS_API_KEY,
        base_url=config.GMS_BASE_URL or None,
        temperature=0,
        timeout=config.LLM_TIMEOUT,
        max_retries=1,
    )


_factory: Callable[[], Any] = _default_factory


def set_llm_factory(factory: Callable[[], Any] | None) -> None:
    global _factory
    _factory = factory or _default_factory


def get_llm() -> Any:
    return _factory()


def classify_llm_error(e: Exception) -> LLMUnavailable:
    """LLM 호출 예외를 사용자에게 보여줄 수 있는 코드로 바꾼다 (원문은 노출하지 않음, NF3002)."""
    if isinstance(e, LLMUnavailable):
        return e
    text = f"{type(e).__name__} {e}".lower()
    if "401" in text or "403" in text or "authentication" in text or "api key" in text:
        return LLMUnavailable(ErrorCode.INVALID_KEY, "LLM API 키가 올바르지 않습니다. GMS_API_KEY를 확인하세요.")
    if "timeout" in text:
        return LLMUnavailable(ErrorCode.TIMEOUT, "LLM 응답이 지연되고 있습니다. 잠시 후 다시 시도하세요.")
    if "404" in text or "model" in text and "not" in text:
        return LLMUnavailable(ErrorCode.UPSTREAM_ERROR, "LLM 접속 주소나 모델 이름을 확인하세요 (GMS_BASE_URL, LLM_MODEL).")
    return LLMUnavailable(ErrorCode.UPSTREAM_ERROR, "LLM 서버가 정상 응답하지 않았습니다.")

"""FastAPI 서버.

- /api/chat   대화 (메인 Agent)
- /api/plans  선출 계획 CRUD
- /api/logs   실행 로그 조회·삭제 (수정 없음)
- /api/tools  Tool 명세·단독 실행 (개발·검증용)
- /app        프론트엔드 화면 (frontend 폴더를 정적 파일로 제공)
실행: python -m uvicorn app.main:app --reload   (backend 폴더에서)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.api.routes import chat_router, logs_router, plans_router
from app.events.logging_agent import logging_agent
from app.storage.db import init_db
from app.tools import TOOLS, call_tool, list_specs

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if config.LOGGING_AGENT_ENABLED:
        logging_agent.attach()  # 로깅 Agent는 이벤트 버스를 구독만 한다
    yield
    logging_agent.detach()

app = FastAPI(
    title="Poke-Agent API",
    description="포켓몬 대전 선출·기술 플래너 Agent",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ToolCallRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict, description="Tool 입력 (각 Tool의 input_schema 참고)")


class ToolCallResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: dict | None = None


app.include_router(chat_router)
app.include_router(plans_router)
app.include_router(logs_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/app/")


@app.get("/health", tags=["시스템"])
def health() -> dict:
    return {"status": "ok", "tools": len(TOOLS), "llm_key": bool(config.GMS_API_KEY),
            "logging_agent": logging_agent.attached}


@app.get("/api/tools", tags=["Tool"])
def get_tools(kind: str | None = None) -> list[dict]:
    """등록된 Tool 명세(이름·설명·입력 스키마) 목록 — Agent에 넘기는 것과 같은 내용."""
    return list_specs(kind)


TOOL_EXAMPLES = {
    "meta_ranking": {"summary": "get_meta_usage · 메타 상위 후보 (기본값)", "value": {"args": {"top_n": 6}}},
    "meta_sets": {"summary": "get_meta_usage · 상대 예상 세트",
                  "value": {"args": {"pokemon": ["한카리아스", "Primarina"], "top_n": 4}}},
    "meta_future": {"summary": "get_meta_usage · 미래 월 → 추정 표시",
                    "value": {"args": {"pokemon": ["Garchomp"], "month": "2026-12"}}},
    "detail": {"summary": "get_pokemon_detail · 따라큐", "value": {"args": {"name": "따라큐"}}},
    "moves": {"summary": "get_move_info · 기술 정보", "value": {"args": {"moves": ["지진", "suckerpunch"]}}},
    "matchup": {"summary": "get_type_matchup · 부유 한카(메가Z)",
                "value": {"args": {"defend_types": ["Dragon"], "defender_ability": "Levitate"}}},
    "resolve": {"summary": "resolve_names · 한국어 이름",
                "value": {"args": {"names": ["메가한카리아스", "누리레느"]}}},
    "bad": {"summary": "실패 예시 · 빈 입력 (get_pokemon_detail)", "value": {"args": {}}},
}


@app.post("/api/tools/{name}", response_model=ToolCallResponse, tags=["Tool"])
def run_tool(
    name: str,
    body: Annotated[ToolCallRequest, Body(openapi_examples=TOOL_EXAMPLES)],
) -> dict:
    """개발·검증용: Tool 하나를 직접 실행한다. Tool 실패도 200 + ok=false로 돌려준다."""
    if name not in TOOLS:
        raise HTTPException(status_code=404, detail="등록되지 않은 Tool입니다.")
    return call_tool(name, body.args)


if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

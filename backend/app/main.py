"""FastAPI 서버 (1일차: 서버 골격 + Tool 확인용 API).

2일차에 /api/chat, /api/plans, /api/logs 를 추가한다.
실행: uvicorn app.main:app --reload   (backend 폴더에서)
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.tools import TOOLS, call_tool, list_specs

app = FastAPI(
    title="Poke-Agent API",
    description="포켓몬 대전 선출·기술 플래너 Agent",
    version="0.1.0",
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "tools": len(TOOLS)}


@app.get("/api/tools")
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


@app.post("/api/tools/{name}", response_model=ToolCallResponse)
def run_tool(
    name: str,
    body: Annotated[ToolCallRequest, Body(openapi_examples=TOOL_EXAMPLES)],
) -> dict:
    """개발·검증용: Tool 하나를 직접 실행한다. Tool 실패도 200 + ok=false로 돌려준다."""
    if name not in TOOLS:
        raise HTTPException(status_code=404, detail="등록되지 않은 Tool입니다.")
    return call_tool(name, body.args)

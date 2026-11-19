"""F3017 백엔드 API — 대화 / 계획 관리 / 실행 로그를 서로 다른 경로로 제공한다."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from app.agent.graph import run_agent
from app.domain import rulesets
from app.tools import call_tool, names
from app.api.schemas import (ChatRequest, ChatResponse, LogEvent, LogSession, Plan, PlanCreate,
                             PlanSaveResult, PlanUpdate)
from app.storage import logs as log_store
from app.storage import plans as plan_store

chat_router = APIRouter(prefix="/api/chat", tags=["대화"])
plans_router = APIRouter(prefix="/api/plans", tags=["계획 관리"])
logs_router = APIRouter(prefix="/api/logs", tags=["실행 로그"])


# ---------------------------------------------------------------- 대화
@chat_router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> dict:
    """메인 Agent 실행. 답변과 함께 실행 단계·Tool 호출 이력(events)을 돌려준다."""
    return await run_in_threadpool(run_agent, req.message, req.session_id, req.name_choices, req.game, req.battle,
                                  req.my_team, req.opp_team, req.mega_choice)


@chat_router.get("/pokemon")
def search_pokemon(q: str = "", game: str | None = None, battle: str | None = None, limit: int = 12) -> list[dict]:
    """팀 직접 선택용 검색: 이름 일부(한국어/영문)로 후보를 찾는다. q가 비면 지금 규칙의 사용률 상위 포켓몬.
    본가(sv)에서는 메가 폼을 빼고 돌려준다."""
    rs = rulesets.resolve(game, battle)
    limit = max(1, min(limit, 30))
    if q.strip():
        return names.search(q, limit, allow_mega=rs["allow_mega"])
    r = call_tool("get_meta_usage", {"format": rs["format"], "top_n": limit})
    if not r["ok"]:
        return []
    out = []
    for row in r["data"]["ranking"][:limit]:
        res = names.resolve_one(row["name"], "pokemon")
        if res.get("found") and (rs["allow_mega"] or not res.get("is_mega")):
            out.append({**names._card(res), "usage_pct": row["usage_pct"]})
    return out


@chat_router.get("/rulesets")
def get_rulesets() -> list[dict]:
    """화면 첫 토글용: 게임 버전(챔피언스/본가)과 배틀 방식(싱글/더블), 각 규칙의 포맷·선출 수."""
    return rulesets.public_list()


# ---------------------------------------------------------------- 계획
@plans_router.get("", response_model=list[Plan])
def list_plans(date: str | None = Query(None, description="YYYY-MM-DD로 거르기")) -> list[dict]:
    return plan_store.list_all(date)


@plans_router.post("", response_model=PlanSaveResult, status_code=201)
def create_plan(body: PlanCreate) -> dict:
    plan, overlaps = plan_store.create(body.model_dump())
    return {"plan": plan, "overlaps": overlaps}


@plans_router.get("/{plan_id}", response_model=Plan)
def get_plan(plan_id: int) -> dict:
    plan = plan_store.get(plan_id)
    if plan is None:
        raise HTTPException(404, "계획을 찾을 수 없습니다.")
    return plan


@plans_router.put("/{plan_id}", response_model=PlanSaveResult)
def update_plan(plan_id: int, body: PlanUpdate) -> dict:
    current = plan_store.get(plan_id)
    if current is None:
        raise HTTPException(404, "계획을 찾을 수 없습니다.")
    changes = body.model_dump(exclude_none=True)
    start, end = changes.get("start_time", current["start_time"]), changes.get("end_time", current["end_time"])
    if end <= start:
        raise HTTPException(422, "end_time은 start_time보다 늦어야 합니다.")
    plan, overlaps = plan_store.update(plan_id, changes)
    return {"plan": plan, "overlaps": overlaps}


@plans_router.delete("/{plan_id}", status_code=204)
def delete_plan(plan_id: int) -> Response:
    if not plan_store.delete(plan_id):
        raise HTTPException(404, "계획을 찾을 수 없습니다.")
    return Response(status_code=204)


# ---------------------------------------------------------------- 실행 로그 (수정 API 없음)
@logs_router.get("", response_model=list[LogSession])
def list_logs() -> list[dict]:
    return log_store.list_sessions()


@logs_router.get("/{session_id}", response_model=list[LogEvent])
def get_log(session_id: str) -> list[dict]:
    events = log_store.get_session(session_id)
    if not events:
        raise HTTPException(404, "해당 세션의 실행 로그가 없습니다.")
    return events


@logs_router.delete("/{session_id}", status_code=204)
def delete_log(session_id: str) -> Response:
    if log_store.delete_session(session_id) == 0:
        raise HTTPException(404, "해당 세션의 실행 로그가 없습니다.")
    return Response(status_code=204)

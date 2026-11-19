"""F3017 API 요청·응답 형식 (Pydantic)."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------- 대화
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, examples=[
        "내 팀: 하마돈, 마스카나, 고릴타, 대쓰여너, 보만다, 루카리오 / "
        "상대: 누리레느, 한카리아스, 브리두라스, 타부자고, 따라큐, 아머까오. 3마리 뭐 뽑아?"])
    session_id: str | None = Field(None, description="이어지는 대화면 이전 응답의 session_id")
    name_choices: dict[str, str] | None = Field(
        None, description="되묻기(clarify)에서 사용자가 고른 이름 {입력한 이름: 후보의 value}. 세션에 기억된다.",
        examples=[{"물라오슨": "Urshifu-Rapid-Strike"}])


class ClarifyCandidate(BaseModel):
    label: str = Field(..., description="보여줄 이름 (예: 연격우라오스)")
    value: str = Field(..., description="name_choices에 넣을 값 (예: Urshifu-Rapid-Strike)")


class ClarifyItem(BaseModel):
    input: str
    reason: str | None = Field(None, description="AMBIGUOUS(여러 대상) / SUGGEST(비슷한 이름) / NOT_FOUND")
    candidates: list[ClarifyCandidate] = []


class ChatResponse(BaseModel):
    session_id: str
    run_id: str
    status: str | None = Field(None, description="ok / ask / limit / error")
    intent: str | None = None
    intent_reason: str | None = None
    answer: str
    plan_draft: dict | None = Field(None, description="저장 가능한 선출 계획 초안 (POST /api/plans 본문에 일정만 더해서 저장)")
    saved: dict | None = None
    replans: list[dict] = []
    error: dict | None = None
    clarify: list[ClarifyItem] = Field(default_factory=list, description="이름 확인이 필요한 항목과 후보 (status=ask)")
    events: list[dict] = Field(default_factory=list, description="실행 단계·Tool 호출·재계획 이력 (실행 순서)")
    total_ms: int


# ---------------------------------------------------------------- 계획
class MovePlanItem(BaseModel):
    opponent: str
    my_pokemon: str
    move: str
    offense: float | None = None
    speed: str | None = None
    answered: bool | None = None


class _ScheduleMixin(BaseModel):
    @field_validator("battle_date", check_fields=False)
    @classmethod
    def _date(cls, v):
        if v is not None and not _DATE.match(v):
            raise ValueError("battle_date는 YYYY-MM-DD 형식이어야 합니다.")
        return v

    @field_validator("start_time", "end_time", check_fields=False)
    @classmethod
    def _time(cls, v):
        if v is not None and not _TIME.match(v):
            raise ValueError("시각은 HH:MM (24시간) 형식이어야 합니다.")
        return v


class PlanCreate(_ScheduleMixin):
    title: str = Field(..., min_length=1, max_length=100)
    battle_date: str = Field(..., examples=["2026-11-21"])
    start_time: str = Field(..., examples=["15:00"])
    end_time: str = Field(..., examples=["15:30"])
    format: str
    my_team: list[str]
    opponent_team: list[str]
    selected: list[str] = Field(..., min_length=1)
    lead: str | None = None
    move_plan: list[MovePlanItem] = []
    basis: dict | None = None
    memo: str | None = None
    session_id: str | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time은 start_time보다 늦어야 합니다.")
        return self


class PlanUpdate(_ScheduleMixin):
    title: str | None = None
    battle_date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    selected: list[str] | None = None
    lead: str | None = None
    move_plan: list[MovePlanItem] | None = None
    memo: str | None = None


class Plan(BaseModel):
    id: int
    title: str
    battle_date: str
    start_time: str
    end_time: str
    format: str
    my_team: list[str]
    opponent_team: list[str]
    selected: list[str]
    lead: str | None
    move_plan: list[dict]
    basis: dict | None
    memo: str | None
    session_id: str | None
    created_at: str
    updated_at: str


class PlanOverlap(BaseModel):
    id: int
    title: str
    battle_date: str
    start_time: str
    end_time: str


class PlanSaveResult(BaseModel):
    plan: Plan
    overlaps: list[PlanOverlap] = Field(default_factory=list, description="같은 날짜에 시간이 겹치는 다른 계획 (저장은 됨)")


# ---------------------------------------------------------------- 로그
class LogSession(BaseModel):
    session_id: str
    runs: int
    events: int
    first_ts: str
    last_ts: str
    tool_calls: int
    replans: int


class LogEvent(BaseModel):
    id: int
    session_id: str
    run_id: str
    seq: int
    ts: str
    type: str
    step: str | None
    payload: dict[str, Any]

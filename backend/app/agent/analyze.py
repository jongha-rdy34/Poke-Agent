"""F3005 사용자 요청 분석 (LLM). 의도와 조건을 구조화해 뽑고, 뽑지 못한 조건은 비워 둔다."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.llm import get_llm

Intent = Literal["plan_selection", "matchup_advice", "lookup", "save_plan", "chitchat"]


class Schedule(BaseModel):
    date: str | None = Field(None, description="대전 날짜 YYYY-MM-DD (오늘 날짜 기준으로 '토요일' 등을 변환)")
    start_time: str | None = Field(None, description="시작 시각 HH:MM (24시간)")
    end_time: str | None = Field(None, description="종료 시각 HH:MM. 말하지 않았으면 null")
    title: str | None = Field(None, description="계획 이름 (예: '토요일 대회 2라운드')")


class PokemonMoves(BaseModel):
    pokemon: str
    moves: list[str]


class Analysis(BaseModel):
    intent: Intent = Field(..., description=(
        "plan_selection=상대 팀을 보고 선출(누구를 낼지) 계획 / matchup_advice=특정 1:1 대면에서 선공·기술 판단 / "
        "lookup=종족값·타입·기술 같은 사실 조회 / save_plan=직전 계획을 일정과 함께 저장·확정 / chitchat=인사·사용법·기타"))
    intent_reason: str = Field(..., description="그 의도로 분류한 이유 한 문장")
    reset: bool = Field(False, description="완전히 새로운 대전(이전 팀과 무관)을 시작하면 true")
    my_team: list[str] | None = Field(None, description="내 포켓몬 이름 목록(사용자가 쓴 그대로). 이번 메시지에서 팀을 말하거나 바꿨을 때만, 바뀐 뒤 전체 목록")
    opp_team: list[str] | None = Field(None, description="상대 포켓몬 이름 목록. 이번 메시지에서 말했을 때만")
    format: str | None = Field(None, description="사용자가 Smogon 포맷 ID를 직접 말했을 때만 (예: gen9ou)")
    pick_count: int | None = Field(None, description="선출 수. '3마리', 싱글이면 3, 더블이면 4. 말하지 않았으면 null")
    month: str | None = Field(None, description="메타 기준 월 YYYY-MM. 말하지 않았으면 null")
    must_answer: list[str] | None = Field(None, description="'꼭 잡아야 하는' 상대 포켓몬")
    prefer: list[str] | None = Field(None, description="반드시 선출에 넣고 싶은 내 포켓몬")
    exclude: list[str] | None = Field(None, description="선출에서 빼고 싶은 내 포켓몬")
    my_moves: list[PokemonMoves] | None = Field(None, description="사용자가 알려준 내 포켓몬의 실제 기술")
    focus_my: str | None = Field(None, description="matchup_advice: 내 쪽 포켓몬")
    focus_opp: str | None = Field(None, description="matchup_advice: 상대 쪽 포켓몬")
    lookup_target: str | None = Field(None, description="lookup: 조회할 포켓몬")
    lookup_kind: Literal["stats", "sets"] | None = Field(None, description="lookup: stats=종족값·타입·특성, sets=실전 기술·도구 구성")
    schedule: Schedule | None = Field(None, description="대전 일정 (저장할 때 필요)")


SYSTEM = """너는 포켓몬 대전 선출 플래너의 '요청 분석기'다. 사용자의 메시지에서 의도와 조건만 구조화해서 뽑는다.
규칙:
- 메시지에 없는 조건은 지어내지 말고 null로 둔다. (예: 시간을 말하지 않았으면 start_time=null)
- 포켓몬·기술 이름은 사용자가 쓴 그대로 적는다 (번역하지 않는다).
- 이전 대화의 조건은 [기억]에 있다. 이번 메시지가 조건 일부만 바꾸면 바뀐 필드만 채운다.
  예) "보만다 빼고 다시" → exclude=["보만다"], intent=plan_selection, 나머지 null
- 팀 정보 없이 선출을 물으면 intent=plan_selection으로 두고 팀은 null로 둔다 (되묻기는 다른 단계가 한다).
- 날짜는 오늘 날짜를 기준으로 YYYY-MM-DD로 바꾼다."""


def analyze(message: str, memory: dict) -> Analysis:
    now = datetime.now()
    weekday = "월화수목금토일"[now.weekday()]
    mem_text = json.dumps({"conditions": memory.get("conditions", {}),
                           "last_result": (memory.get("last") or {}).get("summary")}, ensure_ascii=False)
    history = memory.get("history", [])[-4:]
    messages = [("system", SYSTEM + f"\n오늘: {now:%Y-%m-%d} ({weekday}요일)\n[기억] {mem_text}")]
    messages += [(h["role"] if h["role"] == "user" else "ai", h["content"]) for h in history]
    messages.append(("user", message))
    llm = get_llm().with_structured_output(Analysis, method="function_calling")
    return llm.invoke(messages)

"""F3006 작업 계획 수립 — 의도별로 필요한 작업(=Tool 묶음)과 순서를 정한다. 규칙 기반, LLM 미사용.

작업(action)은 실행 단계에서 Tool 호출로 바뀐다. 앞선 작업의 결과가 다음 작업의 입력이 된다.
"""
from __future__ import annotations

from app import config

# action -> (설명, 쓰는 Tool)
ACTIONS = {
    "resolve": ("한국어/영문 이름을 식별자로 변환", "resolve_names"),
    "meta_opp": ("상대 포켓몬 예상 세트 조회", "get_meta_usage"),
    "meta_my": ("내 포켓몬의 실전 기술 후보 조회", "get_meta_usage"),
    "detail": ("타입·종족값·특성 조회", "get_pokemon_detail"),
    "moves_opp": ("상대 주력기 정보 조회", "get_move_info"),
    "moves_my": ("내 기술 정보 조회", "get_move_info"),
    "matchup": ("방어 측별 타입 상성표 조회", "get_type_matchup"),
}

REPLAN_CRITERIA = {
    1: "내 공격 기술을 자속 타입 → 실전 통계 상위 공격기까지 넓힘",
    2: "잡아야 할 상대를 6마리 전체 → 상대가 선출할 가능성이 높은 상위 마리로 좁힘",
    3: "'잡는다' 기준을 '2배 이상 & 안전' → '선공 + 등배 이상'으로 완화",
}


def default_pick(conditions: dict) -> int:
    if conditions.get("pick_count"):
        return int(conditions["pick_count"])
    fmt = conditions.get("format") or config.DEFAULT_FORMAT
    return 4 if ("vgc" in fmt or "doubles" in fmt) else 3


def missing_conditions(intent: str, c: dict, last: dict | None) -> list[str]:
    """Tool을 부르기 전에 되물어야 할 조건."""
    miss = []
    if intent == "plan_selection":
        if len(c.get("my_team") or []) < default_pick(c):
            miss.append("my_team")
        if not c.get("opp_team"):
            miss.append("opp_team")
    elif intent == "matchup_advice":
        if not c.get("focus_my"):
            miss.append("focus_my")
        if not c.get("focus_opp"):
            miss.append("focus_opp")
    elif intent == "lookup":
        if not c.get("lookup_target"):
            miss.append("lookup_target")
    elif intent == "save_plan":
        if not last or not last.get("draft"):
            miss.append("draft")
        s = c.get("schedule") or {}
        for k in ("date", "start_time", "end_time"):
            if not s.get(k):
                miss.append(f"schedule.{k}")
    return miss


def build_plan(intent: str, c: dict, round_: int) -> list[dict]:
    """round_=0 은 최초 계획, 1 이상은 재계획(이미 확보한 결과는 다시 부르지 않음)."""
    has_my_moves = bool(c.get("my_moves"))
    if intent == "plan_selection":
        if round_ == 0:
            steps = ["resolve", "meta_opp", "detail", "moves_opp", "matchup"]
            if has_my_moves:
                steps.append("moves_my")
            return [_step(a) for a in steps]
        if round_ == 1 and not has_my_moves:
            return [_step("meta_my"), _step("moves_my")]
        return []  # 2, 3회차는 판단 기준만 바꾼다 (Tool 결과 재사용)
    if intent == "matchup_advice":
        steps = ["resolve", "meta_opp", "detail", "moves_opp", "matchup"]
        steps += ["moves_my"] if has_my_moves else ["meta_my", "moves_my"]
        return [_step(a) for a in steps]
    if intent == "lookup":
        steps = ["resolve", "detail"]
        if c.get("lookup_kind") == "sets":
            steps += ["meta_opp", "moves_opp"]
        return [_step(a) for a in steps]
    return []  # save_plan, chitchat: Tool 없음


def _step(action: str) -> dict:
    desc, tool = ACTIONS[action]
    return {"action": action, "tool": tool, "description": desc}

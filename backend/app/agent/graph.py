"""메인 Agent 실행 흐름 (LangGraph).

  analyze ─► plan ─► execute ─► judge ─┬─► respond ─► END
     │        ▲                        │
     │        └──── 재계획 (상한 3회) ◄─┘
     └─► respond   (Tool 불필요 · 되묻기 · 오류)

각 노드의 진입/종료, Tool 호출, LLM 판단, 재계획을 이벤트로 발행한다 (F3012).
"""
from __future__ import annotations

import time
import uuid
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app import config
from app.agent import judge as J
from app.agent import memory
from app.agent.analyze import analyze
from app.agent.executor import Executor, new_ctx
from app.agent.llm import classify_llm_error
from app.agent.planner import REPLAN_CRITERIA, build_plan, missing_conditions
from app.domain import rulesets
from app.agent.respond import ask_text, chitchat_answer, llm_answer, template_answer
from app.events.bus import Emitter
from app.storage import plans as plan_store
from app.tools.common import ErrorCode

FATAL_CODES = {ErrorCode.UPSTREAM_ERROR, ErrorCode.TIMEOUT, ErrorCode.INVALID_KEY}


class AgentState(TypedDict, total=False):
    session_id: str
    ruleset: dict
    teams: dict          # 직접 선택 모드에서 고른 팀 (대화 분석보다 우선)
    mega_choice: dict    # 화면에서 고른 메가진화 {my: 영문 폼 | "none", opp: …}
    message: str
    memory: dict
    intent: str
    intent_reason: str
    conditions: dict
    changed: list
    missing: list
    status: str          # running / ask / replan / ok / limit / error
    round: int
    steps: list
    all_steps: list
    ctx: dict
    result: dict
    replans: list
    error: dict
    answer: str
    plan_draft: dict
    saved: dict


def _em(config_: dict) -> Emitter:
    return config_["configurable"]["emitter"]


def node(name: str):
    """노드 진입/종료 이벤트를 자동으로 발행한다."""
    def deco(fn):
        # functools.wraps를 쓰면 LangGraph가 원래 함수 시그니처를 보고 config를 넘기지 않으므로 이름만 복사한다
        def wrapper(state: AgentState, config: RunnableConfig) -> dict:
            em = _em(config)
            em.emit("step_start", name, round=state.get("round", 0))
            t0 = time.perf_counter()
            out = fn(state, em)
            em.emit("step_end", name, duration_ms=round((time.perf_counter() - t0) * 1000),
                    status=out.get("status", state.get("status")), summary=out.pop("_summary", None))
            return out
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


# ---------------------------------------------------------------- 노드
@node("analyze")
def analyze_node(state: AgentState, em: Emitter) -> dict:
    mem = state["memory"]
    try:
        a = analyze(state["message"], mem, state.get("ruleset"))
    except Exception as e:  # noqa: BLE001
        err = classify_llm_error(e)
        em.emit("error", "analyze", code=err.code, message=err.message)
        return {"status": "error", "error": {"code": err.code, "message": err.message}, "intent": "error",
                "_summary": err.code}
    em.emit("llm_decision", "analyze", decision="intent", intent=a.intent, reason=a.intent_reason)
    new = a.model_dump()
    new["my_moves"] = [m for m in (new.get("my_moves") or [])] or None
    new["schedule"] = {k: v for k, v in (new.get("schedule") or {}).items() if v} or None
    if new["schedule"] and mem["conditions"].get("schedule"):
        new["schedule"] = {**mem["conditions"]["schedule"], **new["schedule"]}  # 일정은 바뀐 항목만 갱신
    teams = state.get("teams") or {}
    intent = a.intent
    if teams.get("my") or teams.get("opp"):
        # 직접 선택한 팀이 있으면 그 팀을 쓴다 (새 팀이면 이전 선호·제외 조건은 비운다)
        new["my_team"] = teams.get("my") or new.get("my_team")
        new["opp_team"] = teams.get("opp") or new.get("opp_team")
        if intent in ("chitchat", "lookup"):
            intent = "plan_selection"
        em.emit("decision", "analyze", decision="teams_from_picker", my=len(teams.get("my") or []),
                opp=len(teams.get("opp") or []))
    conditions, changed = memory.merge_conditions(mem["conditions"], new, reset=a.reset)
    # 팀이 바뀌면 이전 팀 기준의 메가 선택은 버린다
    choice = state.get("mega_choice") or {}
    if "my_team" in changed and "my_holders" not in choice:
        conditions.pop("my_megas", None)   # 팀이 바뀌면 메가스톤 보유자는 다시 묻는다
    if "opp_team" in changed and "opp_holders" not in choice:
        conditions.pop("opp_megas", None)
    if "my_holders" in choice:
        conditions["my_megas"] = {k: v for k, v in (choice["my_holders"] or {}).items() if v and v != "none"}
    if "opp_holders" in choice:
        conditions["opp_megas"] = {k: v for k, v in (choice["opp_holders"] or {}).items() if v and v != "none"}
    if (teams.get("my") or teams.get("opp")) and ({"my_team", "opp_team"} & set(changed)):
        for k in ("prefer", "exclude", "must_answer", "my_moves"):
            if not new.get(k):
                conditions.pop(k, None)  # 팀이 바뀌면 이전 팀 기준의 선호·제외 조건은 버린다
    rs = state.get("ruleset")
    if rs:  # 화면에서 고른 게임·배틀 규칙을 조건에 반영 (이번 메시지에서 직접 말한 값이 우선)
        if not new.get("format"):
            conditions["format"] = rs["format"]
        if not new.get("pick_count"):
            conditions["pick_count"] = rs["pick"]
        conditions["game"], conditions["battle"] = rs["game"], rs["battle"]
    missing = missing_conditions(intent, conditions, mem.get("last"))
    status = "ask" if missing else "running"
    if missing:
        em.emit("decision", "analyze", decision="ask", missing=missing)
    return {"intent": intent, "intent_reason": a.intent_reason, "conditions": conditions, "changed": changed,
            "missing": missing, "status": status, "round": 0, "replans": [], "ctx": new_ctx(), "all_steps": [],
            "_summary": {"intent": intent, "changed": changed, "missing": missing}}


@node("plan")
def plan_node(state: AgentState, em: Emitter) -> dict:
    steps = build_plan(state["intent"], state["conditions"], state["round"])
    em.emit("plan", "plan", round=state["round"], steps=[s["action"] for s in steps],
            tools=[s["tool"] for s in steps])
    all_steps = state.get("all_steps", []) + [{**s, "round": state["round"]} for s in steps]
    return {"steps": steps, "all_steps": all_steps, "_summary": [s["action"] for s in steps]}


@node("execute")
def execute_node(state: AgentState, em: Emitter) -> dict:
    ctx = state["ctx"]
    before = ctx["calls"]
    Executor(ctx, state["conditions"], state["intent"], em, state["memory"].get("aliases")).run(state["steps"])
    return {"ctx": ctx, "_summary": {"tool_calls": ctx["calls"] - before, "failures": len(ctx["failures"])}}


@node("judge")
def judge_node(state: AgentState, em: Emitter) -> dict:
    ctx, intent, round_ = state["ctx"], state["intent"], state["round"]
    if ctx["unresolved"]:
        em.emit("decision", "judge", decision="ask", unresolved=[u["input"] for u in ctx["unresolved"]])
        return {"status": "ask", "_summary": "이름 확인 필요"}
    if ctx.get("mega_ask"):
        return {"status": "ask", "missing": ["mega"], "_summary": "메가진화 선택 필요"}

    fatal = [f for f in ctx["failures"] if f["code"] in FATAL_CODES]
    if intent == "plan_selection":
        result = J.judge_selection(ctx, state["conditions"], round_)
    elif intent == "matchup_advice":
        result = J.judge_matchup(ctx, state["conditions"])
    else:
        result = J.judge_lookup(ctx)

    if result.get("satisfied"):
        return {"result": result, "status": "ok", "_summary": "조건 충족"}
    if intent == "plan_selection" and not result.get("selected") and not fatal:
        # 규칙상 고를 수 있는 조합 자체가 없음 (메가 1마리 제한, 제외 조건 등) → 재계획해도 같다
        em.emit("decision", "judge", decision="infeasible", reason=result.get("reason"))
        return {"result": result, "status": "infeasible", "_summary": "선출 조합 없음"}
    if fatal and not ctx["charts"]:
        # 외부 서버 오류는 다시 해도 결과가 같다 → 재계획하지 않고 안내
        em.emit("decision", "judge", decision="no_replan", reason="외부 데이터 오류", codes=[f["code"] for f in fatal])
        return {"result": result, "status": "error",
                "error": {"code": fatal[0]["code"], "message": fatal[0]["message"]}, "_summary": "외부 오류"}
    if intent == "plan_selection" and round_ < config.MAX_REPLAN and result.get("selected"):
        nxt = round_ + 1
        rp = {"round": nxt, "reason": f"잡지 못한 상대: {', '.join(result.get('uncovered', []))}",
              "criteria": REPLAN_CRITERIA[nxt]}
        em.emit("replan", "judge", **rp)
        return {"result": result, "status": "replan", "round": nxt, "replans": state["replans"] + [rp],
                "_summary": f"재계획 {nxt}회차"}
    status = "limit" if intent == "plan_selection" and round_ >= config.MAX_REPLAN else "ok"
    if status == "limit":
        em.emit("decision", "judge", decision="stop", reason=f"재계획 상한 {config.MAX_REPLAN}회 도달")
    return {"result": result, "status": status, "_summary": status}


@node("respond")
def respond_node(state: AgentState, em: Emitter) -> dict:
    status, intent = state.get("status"), state.get("intent")
    ctx = state.get("ctx") or {}
    out: dict[str, Any] = {}

    if status == "error":
        e = state.get("error") or {}
        answer = f"요청을 처리하지 못했습니다: {e.get('message', '알 수 없는 오류')}"
    elif status == "ask":
        answer = ask_text(state.get("missing", []), ctx.get("unresolved"))
    elif intent == "save_plan":
        answer, out = _save_from_chat(state, em)
    elif intent == "chitchat":
        try:
            answer = chitchat_answer(state["message"])
        except Exception:  # noqa: BLE001
            answer = "안녕하세요! 내 팀 6마리와 상대 팀 6마리를 알려주시면 선출 3마리와 대면별 기술을 추천해 드려요."
    else:
        facts = {"intent": intent, "status": status, "result": state.get("result"), "basis": ctx.get("basis"),
                 "ruleset": {k: (state.get("ruleset") or {}).get(k) for k in ("game_label", "battle_label", "gimmick", "status")},
                 "replans": state.get("replans"), "tool_failures": [
                     {"tool": f["tool"], "code": f["code"]} for f in ctx.get("failures", [])],
                 "replan_limit_reached": status == "limit"}
        try:
            answer = llm_answer(facts)
        except Exception as e:  # noqa: BLE001
            err = classify_llm_error(e)
            em.emit("error", "respond", code=err.code, message=err.message, fallback="template")
            answer = template_answer(intent, facts)
        if status == "limit":
            answer += ("\n\n재계획을 3회 해도 모든 상대를 잡는 조합을 찾지 못했습니다. "
                       "꼭 잡아야 할 상대를 줄이거나, 선호/제외 조건을 풀어도 될까요?")
        if intent == "plan_selection" and (state.get("result") or {}).get("selected"):
            out["plan_draft"] = _draft(state)

    last = None
    if out.get("plan_draft"):
        r = state["result"]
        last = {"draft": out["plan_draft"],
                "summary": f"선출 {', '.join(r['selected'])} / 선봉 {r['lead']} / 미커버 {r.get('uncovered')}"}
    memory.save(state["session_id"], state.get("conditions") or state["memory"]["conditions"], last,
                state["message"], answer)
    return {"answer": answer, **out, "_summary": {"status": status, "chars": len(answer)}}


def _draft(state: AgentState) -> dict:
    r, ctx, c = state["result"], state["ctx"], state["conditions"]
    label = lambda s: ctx["display"].get(s, {}).get("ko") or ctx["display"].get(s, {}).get("en") or s  # noqa: E731
    return {
        "format": (ctx.get("basis") or {}).get("format") or c.get("format") or config.DEFAULT_FORMAT,
        "my_team": [label(s) for s in ctx["my"]], "opponent_team": [label(s) for s in ctx["opp"]],
        "selected": r["selected"], "lead": r["lead"],
        "move_plan": [{**{k: p[k] for k in ("opponent", "my_pokemon", "move", "offense", "speed", "answered")},
                       # 저장용 데미지 요약: 상대 최다 배분 기준
                       "damage": (f"{p['damage']['results'][0]['min_pct']}~{p['damage']['results'][0]['max_pct']}% "
                                  f"{p['damage']['results'][0]['ko']}") if p.get("damage") else None}
                      for p in r["move_plan"]],
        "basis": {**(ctx.get("basis") or {}), "game": (state.get("ruleset") or {}).get("game_label"),
                  "replans": len(state.get("replans") or []),
                  "uncovered": r.get("uncovered")},
    }


def _save_from_chat(state: AgentState, em: Emitter) -> tuple[str, dict]:
    """계획 저장은 사용자가 이미 정한 값을 다루므로 Tool이 아니라 서버 로직이 직접 처리한다."""
    draft = state["memory"]["last"]["draft"]
    s = state["conditions"]["schedule"]
    data = {**draft, "title": s.get("title") or f"{s['date']} 선출 계획", "battle_date": s["date"],
            "start_time": s["start_time"], "end_time": s["end_time"], "session_id": state["session_id"]}
    plan, overlaps = plan_store.create(data)
    em.emit("plan_saved", "respond", plan_id=plan["id"], overlaps=[o["id"] for o in overlaps])
    text = f"계획을 저장했어요 (#{plan['id']} {plan['title']}, {plan['battle_date']} {plan['start_time']}~{plan['end_time']})."
    if overlaps:
        text += "\n⚠️ 같은 날 시간이 겹치는 계획이 있어요: " + ", ".join(
            f"#{o['id']} {o['title']} {o['start_time']}~{o['end_time']}" for o in overlaps)
    return text, {"saved": {"plan": plan, "overlaps": overlaps}}


# ---------------------------------------------------------------- 그래프
def _after_analyze(state: AgentState) -> str:
    if state["status"] in ("error", "ask") or state["intent"] in ("chitchat", "save_plan"):
        return "respond"
    return "plan"


def _after_judge(state: AgentState) -> str:
    return "plan" if state["status"] == "replan" else "respond"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("analyze", analyze_node)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("judge", judge_node)
    g.add_node("respond", respond_node)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", _after_analyze, {"plan": "plan", "respond": "respond"})
    g.add_edge("plan", "execute")
    g.add_edge("execute", "judge")
    g.add_conditional_edges("judge", _after_judge, {"plan": "plan", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


GRAPH = build_graph()


def _pokemon_cards(ctx: dict) -> dict:
    """화면 표시용: 이름(답변에 쓰인 표시 이름) → 이미지·타입·스피드 종족값."""
    cards = {}
    mega_forms = list((ctx.get("my_mega_forms") or {}).values())
    for sid in dict.fromkeys((ctx.get("my") or []) + mega_forms + (ctx.get("opp") or [])):
        d = (ctx.get("detail") or {}).get(sid)
        disp = (ctx.get("display") or {}).get(sid, {})
        label = disp.get("ko") or disp.get("en") or sid
        cards[label] = {"en": disp.get("en"), "types": (d or {}).get("types", []),
                        "sprite": (d or {}).get("sprite"), "spe": ((d or {}).get("base_stats") or {}).get("spe"),
                        "side": "opp" if sid in (ctx.get("opp") or []) else "my"}
    return cards


def run_agent(message: str, session_id: str | None = None, name_choices: dict[str, str] | None = None,
              game: str | None = None, battle: str | None = None,
              my_team: list[str] | None = None, opp_team: list[str] | None = None,
              mega_choice: dict | None = None) -> dict:
    session_id = session_id or "s-" + uuid.uuid4().hex[:10]
    em = Emitter(session_id)
    mem = memory.get(session_id)
    prev = mem["conditions"]
    ruleset = rulesets.resolve(game or prev.get("game"), battle or prev.get("battle"))
    em.emit("run_start", None, message=message, game=ruleset["game"], battle=ruleset["battle"],
            format=ruleset["format"])
    if prev.get("game") and (prev.get("game"), prev.get("battle")) != (ruleset["game"], ruleset["battle"]):
        em.emit("decision", None, decision="ruleset_changed", to=f"{ruleset['game_label']} {ruleset['battle_label']}")
    if name_choices:
        memory.add_aliases(session_id, name_choices)
        em.emit("decision", None, decision="name_choice", choices=name_choices)
    t0 = time.perf_counter()
    try:
        final = GRAPH.invoke({"session_id": session_id, "message": message, "memory": memory.get(session_id),
                              "ruleset": ruleset, "teams": {"my": my_team or [], "opp": opp_team or []},
                              "mega_choice": mega_choice or {}},
                             config={"configurable": {"emitter": em}, "recursion_limit": 40})
    except Exception as e:  # noqa: BLE001 - 어떤 경우에도 서비스는 응답한다
        em.emit("error", None, code=ErrorCode.INTERNAL_ERROR, message=type(e).__name__)
        final = {"status": "error", "answer": "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                 "error": {"code": ErrorCode.INTERNAL_ERROR, "message": "내부 오류"}}
    total = round((time.perf_counter() - t0) * 1000)
    ctx = final.get("ctx") or {}
    cards = _pokemon_cards(ctx)
    em.emit("run_end", None, status=final.get("status"), total_ms=total, tool_calls=ctx.get("calls", 0),
            replans=len(final.get("replans") or []))
    return {
        "session_id": session_id,
        "run_id": em.run_id,
        "status": final.get("status"),
        "intent": final.get("intent"),
        "intent_reason": final.get("intent_reason"),
        "answer": final.get("answer", ""),
        "plan_draft": final.get("plan_draft"),
        "saved": final.get("saved"),
        "replans": final.get("replans") or [],
        "result": final.get("result"),
        "error": final.get("error"),
        "conditions": final.get("conditions"),
        "opp_profile": (final.get("result") or {}).get("opp_profile"),
        "pokemon_cards": cards,
        "move_plan": (final.get("result") or {}).get("move_plan"),
        "mega": ctx.get("mega"),  # 내 메가스톤 후보·보유, 상대 메가 예상 (화면 토글용)
        "mega_evolve": (final.get("result") or {}).get("mega_evolve"),
        "mega_alternatives": (final.get("result") or {}).get("mega_alternatives") or [],
        "rule_notes": (final.get("result") or {}).get("rule_notes") or [],
        "ruleset": ruleset,
        "defense_advice": (final.get("result") or {}).get("defense_advice"),
        "clarify": [  # 화면에서 고를 수 있는 이름 후보 (status=ask일 때)
            {"input": u["input"], "reason": u.get("reason"), "candidates": u.get("candidates") or []}
            for u in ctx.get("unresolved", [])
        ] if final.get("status") == "ask" else [],
        "events": em.events,
        "total_ms": total,
    }

"""F3011 최종 응답 생성.

판단 결과(코드 계산값)만 근거로 LLM이 문장을 만든다. LLM이 실패하면 같은 내용을 템플릿으로 만든다.
되묻기·저장 결과처럼 정해진 문장은 LLM 없이 코드가 만든다.
"""
from __future__ import annotations

import json

from app.agent.llm import get_llm

DISCLAIMER = "※ 사용률 통계 기반 추정이며, 상대의 실제 기술·도구·배분과 다를 수 있습니다."

ASK_TEXT = {
    "my_team": "내 포켓몬을 알려주세요 (선출 수 이상, 보통 6마리). 예) 내 팀: 한카리아스, 마스카나, …",
    "opp_team": "상대 포켓몬 6마리를 알려주세요.",
    "focus_my": "어떤 내 포켓몬으로 상대할지 알려주세요.",
    "focus_opp": "어떤 상대 포켓몬과의 대면인지 알려주세요.",
    "lookup_target": "어떤 포켓몬을 조회할지 알려주세요.",
    "mega": "메가진화는 배틀당 1마리만 할 수 있어요. 내 팀에서 누가 메가진화할지 골라 주세요 (안 해도 됩니다).",
    "draft": "저장할 선출 계획이 아직 없습니다. 먼저 내 팀과 상대 팀으로 선출 계획을 만들어 주세요.",
    "schedule.date": "대전 날짜를 알려주세요 (예: 11월 22일, 이번 토요일).",
    "schedule.start_time": "대전 시작 시각을 알려주세요 (예: 15:00).",
    "schedule.end_time": "대전 종료 시각(예상)을 알려주세요 (예: 15:30).",
}

SYSTEM = f"""너는 포켓몬 대전 선출 코치다. [판단 결과] JSON에 있는 값만 사용해 한국어로 답한다.
규칙:
- 포켓몬·기술·숫자(배율, 종족값, 실수치, 비율)를 새로 만들거나 바꾸지 않는다. JSON에 없으면 말하지 않는다.
- 선출 계획이면: ① 선출 {{pick}}마리와 선봉 ② 상대별 추천 대면과 기술(배율·선공 판단과 근거) ③ 선출하지 않은 포켓몬과 사유
  ④ 근거(통계 포맷·월·레이팅, 가정) 순서로 쓴다. 잡지 못한 상대(uncovered)가 있으면 숨기지 말고 대처 방향을 한 줄로 쓴다.
- ruleset(게임·배틀 규칙)을 근거 단락 첫 줄에 쓴다. 본가(스칼렛·바이올렛)면 '종료된 시즌의 마지막 집계 통계'임을 밝힌다.
- 재계획(replans)이 있었으면 어떤 기준으로 바꿨는지 한 줄 밝힌다.
- opp_profile이 있으면 '상대 파티 공격 성향' 단락을 둔다: 파티 물리/특수 비율(lean), 포켓몬별 주된 운용 형태와 비율
  (예: 한카리아스 — 물리 고속 딜러 66%(AS 명랑), 물리막이 22%(HB 장난꾸러기)), 그리고 defense_advice를 그대로 쓴다.
  비율이 null인 포켓몬은 '통계 없음'이라고만 쓴다.
- 확인할 수 없는 값은 '역산 기준'(예: 상대 스피드 실수치 X 이상이면 역전)으로만 말한다.
- 마크다운 목록으로 간결하게. 마지막 줄은 정확히: {DISCLAIMER}"""

CHITCHAT_SYSTEM = """너는 포켓몬 대전 선출 플래너 'Poke-Agent'다. 인사나 사용법 질문에 짧게 답한다.
할 수 있는 일: ① 내 팀 6마리 + 상대 팀 6마리로 3마리 선출과 대면별 기술 추천 ② 1:1 대면 선공·기술 판단
③ 포켓몬 종족값·실전 세트 조회 ④ 만든 계획을 대전 일정과 함께 저장. 포켓몬 데이터 수치는 말하지 않는다."""


def ask_text(missing: list[str], unresolved: list[dict] | None = None) -> str:
    lines = []
    for u in unresolved or []:
        names = ", ".join(c["label"] for c in u.get("candidates") or [])
        if u.get("reason") == "NOT_IN_GAME":
            lines.append(f"'{u['input']}'은(는) 지금 고른 게임(스칼렛·바이올렛)에는 없는 메가진화예요. {names}(으)로 볼까요?")
        elif u.get("reason") == "AMBIGUOUS":
            lines.append(f"'{u['input']}'이(가) 여러 포켓몬에 해당합니다: {names}. 어느 쪽인가요?")
        elif u.get("reason") == "SUGGEST":
            lines.append(f"'{u['input']}'을(를) 정확히 찾지 못했어요. 혹시 {names} 중 하나인가요?")
        else:
            lines.append(f"'{u['input']}'을(를) 찾지 못했습니다. 이름을 다시 확인해 주세요 (영문 이름도 됩니다).")
    lines += [ASK_TEXT.get(m, m) for m in missing]
    return "확인이 필요해요.\n" + "\n".join(f"- {line}" for line in lines)


def llm_answer(facts: dict) -> str:
    llm = get_llm()
    out = llm.invoke([("system", SYSTEM.replace("{pick}", str(facts.get("result", {}).get("pick", 3)))),
                      ("user", "[판단 결과]\n" + json.dumps(facts, ensure_ascii=False, default=str))])
    text = out.content if hasattr(out, "content") else str(out)
    if DISCLAIMER not in text:
        text = text.rstrip() + "\n\n" + DISCLAIMER
    return text


def chitchat_answer(message: str) -> str:
    out = get_llm().invoke([("system", CHITCHAT_SYSTEM), ("user", message)])
    return out.content if hasattr(out, "content") else str(out)


# ---------------------------------------------------------------- 템플릿 (LLM 실패 시)
def template_answer(intent: str, facts: dict) -> str:
    r = facts.get("result") or {}
    basis = facts.get("basis") or {}
    lines: list[str] = []
    if intent == "plan_selection" and r.get("selected"):
        lines.append(f"**선출 {len(r['selected'])}마리**: {', '.join(r['selected'])} (선봉: {r['lead']})")
        if facts.get("replans"):
            lines.append(f"- 재계획 {len(facts['replans'])}회: {facts['replans'][-1]['criteria']}")
        lines.append("\n**대면별 추천**")
        for p in r["move_plan"]:
            mark = "✅" if p["answered"] else "⚠️"
            lines.append(f"- {mark} vs {p['opponent']}: {p['my_pokemon']}의 {p['move']}({p['offense']:g}배) · "
                         f"{p['speed']} — {p['speed_basis']}"
                         + (f" · 상대 {p['risk_move']} {p['risk']:g}배 주의" if p.get("risk_move") and p["risk"] >= 2 else ""))
        if r.get("uncovered"):
            lines.append(f"\n잡지 못한 상대: {', '.join(r['uncovered'])} — 교체로 받아내거나 선출 조건을 조정해 보세요.")
        if r.get("excluded"):
            lines.append("\n**선출하지 않은 포켓몬**")
            lines += [f"- {e['pokemon']}: {e['reason']}" for e in r["excluded"]]
    elif intent == "matchup_advice" and r.get("satisfied"):
        lines.append(f"**{r['my_pokemon']} vs {r['opponent']}** — {r['speed']['verdict']} ({r['speed']['basis']})")
        lines += [f"- {m['name']} ({m['type']}, {m['multiplier']:g}배)" for m in r["moves_ranked"]]
        if r.get("risk_move"):
            lines.append(f"- 상대 주력기 {r['risk_move']['name']}: 내게 {r['risk']:g}배")
        if r.get("priority_moves"):
            lines.append(f"- 상대 선공기: {', '.join(p['name'] for p in r['priority_moves'])}")
    elif intent == "lookup" and r.get("satisfied"):
        d = r["detail"]
        lines.append(f"**{r['pokemon']}** 타입 {'/'.join(d['types'])} · 종족값 {d['base_stats']}")
        if r.get("sets"):
            lines.append("실전 기술: " + ", ".join(f"{m['name']} {m['pct']}%" for m in r["sets"]["moves"][:6]))
    else:
        lines.append(r.get("reason") or "조건을 만족하는 결과를 찾지 못했습니다.")
    prof = r.get("opp_profile")
    if prof and prof.get("physical_pct") is not None:
        lines.append(f"\n**상대 파티 공격 성향**: {prof['lean']} (물리 {prof['physical_pct']}% / 특수 {prof['special_pct']}%)")
        for mbr in prof["members"]:
            if mbr["physical_share"] is None and not mbr.get("roles"):
                lines.append(f"- {mbr['name']}: 통계 없음")
                continue
            kinds = ", ".join(f"{x['role']} {x['pct']}%({x['typical']})" for x in mbr["roles"]) or mbr["basis"]
            lines.append(f"- {mbr['name']}: {kinds}")
        if r.get("defense_advice"):
            lines.append(r["defense_advice"])
    if r.get("assumptions"):
        lines.append("\n가정: " + "; ".join(r["assumptions"][:4]))
    if basis:
        rs = facts.get("ruleset") or {}
        if rs.get("game_label"):
            lines.append(f"규칙: {rs['game_label']} {rs.get('battle_label', '')} ({rs.get('gimmick')} 룰, {rs.get('status')})")
        lines.append(f"근거: {basis.get('format')} · {basis.get('month')} · 레이팅 {basis.get('rating')} 통계"
                     + (" (추정)" if basis.get("estimated") else ""))
    lines.append("\n" + DISCLAIMER)
    return "\n".join(lines)

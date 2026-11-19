"""F3008 제약 적합성 판단 · F3009 재계획 판단 — 모두 코드 계산, LLM 미사용.

확인할 수 없는 값(상대의 실제 배분·도구)은 지어내지 않고, 범위와 역산 기준으로만 말한다.
"""
from __future__ import annotations

import itertools
import math

from app import config
from app.agent.executor import ATTACK_MOVE_MIN_PCT
from app.agent.planner import REPLAN_CRITERIA, default_pick
from app.agent import sets as S
from app.domain.roles import team_profile
from app.domain.rulesets import RULESETS

SCARF_MIN_PCT = 10.0
BASE_SPEED_MARGIN = 15  # 능력치 체계를 모르는 포맷에서 '유력'으로 볼 종족값 차이


# ---------------------------------------------------------------- 스피드
def lv50_speed_range(base: int) -> tuple[int, int]:
    """일반 포맷 Lv50: 최소 = 개체값 31·노력치 0·보정 없음, 최대 = 노력치 252·성격 1.1배."""
    low = math.floor((2 * base + 31) * 50 / 100) + 5
    high = math.floor((math.floor((2 * base + 31 + 63) * 50 / 100) + 5) * 1.1)
    return low, high


def speed_verdict(my_base: int, opp_base: int, fmt: str, opp_scarf_pct: float) -> dict:
    scarf = opp_scarf_pct >= SCARF_MIN_PCT
    if "champions" in fmt:
        # Champions 포맷은 SP 체계(스탯당 최대 32)라 실수치 공식을 확정하지 않았다 → 종족값 비교만
        diff = my_base - opp_base
        if diff >= BASE_SPEED_MARGIN:
            v = "선공 유력"
        elif diff <= -BASE_SPEED_MARGIN:
            v = "후공 유력"
        else:
            v = "배분에 따라 다름"
        basis = f"스피드 종족값 {my_base} vs {opp_base} (이 포맷은 종족값 기준 비교)"
        if scarf and v != "후공 유력" and my_base < opp_base * 1.5:
            basis += f" · 상대가 구애스카프면(채용 {opp_scarf_pct:.0f}%) 상대 실질 {opp_base * 1.5:.0f} → 후공"
        return {"verdict": v, "basis": basis, "scarf_risk": scarf}

    my_lo, my_hi = lv50_speed_range(my_base)
    op_lo, op_hi = lv50_speed_range(opp_base)
    if my_lo > op_hi:
        v = "확정 선공"
    elif my_hi < op_lo:
        v = "확정 후공"
    else:
        v = "배분에 따라 다름"
    basis = f"Lv50 실수치 범위 내 {my_lo}~{my_hi} vs 상대 {op_lo}~{op_hi}"
    if v == "배분에 따라 다름":
        basis += f" · 내가 최대 투자({my_hi})면 상대 스피드 실수치 {my_hi} 이상일 때만 역전"
    if scarf:
        basis += f" · 상대 구애스카프 채용 {opp_scarf_pct:.0f}%: 스카프면 상대 최대 {math.floor(op_hi * 1.5)}"
    return {"verdict": v, "basis": basis, "scarf_risk": scarf}


def is_faster(v: dict) -> bool:
    return v["verdict"] in ("확정 선공", "선공 유력")


# ---------------------------------------------------------------- 기술 후보
def _move_options(ctx: dict, sid: str, user_moves: list[str] | None, use_meta: bool) -> tuple[list[dict], list[str]]:
    """공격 수단 목록 [{name, type, power}] 과 가정 목록."""
    detail = ctx["detail"][sid]
    assumptions = []
    opts: list[dict] = []
    if user_moves:
        for name in user_moves:
            m = _find_move(ctx, name)
            if m and m["category"] != "Status":
                opts.append({"name": m.get("ko_name") or m["name"], "type": m["type"], "power": m.get("base_power") or 0,
                             "move": m})
        return opts, assumptions
    if use_meta and sid in ctx["meta"]:
        for mv in ctx["meta"][sid]["moves"]:
            m = ctx["moves"].get(mv["name"])
            if mv["pct"] >= ATTACK_MOVE_MIN_PCT and m and m["category"] != "Status":
                opts.append({"name": m.get("ko_name") or m["name"], "type": m["type"], "power": m.get("base_power") or 0,
                             "pct": mv["pct"], "move": m})
    for t in detail["types"]:  # 자속 타입은 항상 후보 (기술 이름은 모름)
        if not any(o["type"] == t for o in opts):
            opts.append({"name": f"자속 {t} 기술", "type": t, "power": None})
    if not use_meta:
        assumptions.append(f"{_label(ctx, sid)}: 기술을 몰라 자속 타입 기술로 가정")
    return opts, assumptions


def _find_move(ctx: dict, name: str) -> dict | None:
    from app.tools.common import to_id
    key = to_id(name)
    if key in ctx["moves"]:
        return ctx["moves"][key]
    for m in ctx["moves"].values():
        if m.get("ko_name") == name or m.get("input") == name:
            return m
    return None


def _opp_threats(ctx: dict, sid: str) -> tuple[list[dict], list[dict], str | None]:
    """상대 주력 공격기 (사용률 ≥20%), 선공기, 가정 문구."""
    meta = ctx["meta"].get(sid)
    if not meta:
        types = ctx["detail"][sid]["types"]
        return ([{"name": f"자속 {t} 기술", "type": t, "pct": None} for t in types], [],
                f"{_label(ctx, sid)}: 실전 통계가 없어 자속 타입 기술로 가정")
    threats, priority = [], []
    for mv in meta["moves"]:
        m = ctx["moves"].get(mv["name"])
        if not m or mv["pct"] < ATTACK_MOVE_MIN_PCT or m["category"] == "Status":
            continue
        item = {"name": m.get("ko_name") or m["name"], "type": m["type"], "pct": mv["pct"]}
        threats.append(item)
        if (m.get("priority") or 0) > 0:
            priority.append(item)
    return threats, priority, None


def _label(ctx: dict, sid: str) -> str:
    d = ctx["display"].get(sid, {})
    return d.get("ko") or d.get("en") or sid


# ---------------------------------------------------------------- 상대 공격 성향 (물리/특수)
def move_physical_share(ctx: dict, sid: str) -> float | None:
    """실전 기술 통계 기준: 공격기 채용 비율 중 물리 기술의 비율(%)."""
    phys = spec = 0.0
    for mv in (ctx["meta"].get(sid) or {}).get("moves", []):
        m = ctx["moves"].get(mv["name"])
        if not m:
            continue
        if m["category"] == "Physical":
            phys += mv["pct"]
        elif m["category"] == "Special":
            spec += mv["pct"]
    return round(phys / (phys + spec) * 100, 1) if phys + spec > 0 else None


def opponent_profile(ctx: dict, sids: list[str]) -> dict:
    members = [{"name": _label(ctx, s), "sid": s, "roles": (ctx["meta"].get(s) or {}).get("roles")} for s in sids]
    return team_profile(members, {s: move_physical_share(ctx, s) for s in sids})


def _bulk(ctx: dict, sid: str, physical_w: float) -> float:
    """물리 비중에 맞춘 내구 지표: HP × (w·방어 + (1-w)·특방). 종족값이 없으면 0."""
    b = (ctx["detail"].get(sid) or {}).get("base_stats") or {}
    hp, de, sd = b.get("hp") or 0, b.get("def") or 0, b.get("spd") or 0
    return hp * (physical_w * de + (1 - physical_w) * sd)


def defense_advice(ctx: dict, profile: dict, candidates: list[str]) -> str | None:
    """상대 공격 성향에 맞춰 받아내기 좋은 내 포켓몬 (종족값 기준)."""
    if profile.get("physical_pct") is None or not candidates:
        return None
    w = profile["physical_pct"] / 100
    ranked = sorted(candidates, key=lambda m: -_bulk(ctx, m, w))[:2]
    if not ranked or _bulk(ctx, ranked[0], w) == 0:
        return None
    key, stat = ("def", "B") if w >= 0.6 else ("spd", "D") if w <= 0.4 else (None, None)
    def fmt(m):
        b = ctx["detail"][m]["base_stats"]
        if key:
            return f"{_label(ctx, m)}(H{b.get('hp')}·{stat}{b.get(key)})"
        return f"{_label(ctx, m)}(H{b.get('hp')}·B{b.get('def')}·D{b.get('spd')})"
    lean = profile["lean"]
    return (f"상대 파티가 {lean}(물리 {profile['physical_pct']}% / 특수 {profile['special_pct']}%)이라 "
            f"{'물리' if key == 'def' else '특수' if key == 'spd' else '양쪽'} 내구가 좋은 "
            + ", ".join(fmt(m) for m in ranked) + "가 받아내기 유리합니다 (종족값 기준).")


# ---------------------------------------------------------------- 대면표
def matchup_matrix(ctx: dict, conditions: dict, round_: int) -> tuple[dict, list[str]]:
    fmt = (ctx.get("basis") or {}).get("format") or conditions.get("format") or config.DEFAULT_FORMAT
    user_moves = {m["pokemon"]: m["moves"] for m in (conditions.get("my_moves") or [])}
    user_moves_sid = {}
    for raw, mv in user_moves.items():
        entry = ctx["names"].get(raw)
        if entry:
            user_moves_sid[entry["sid"]] = mv
    assumptions: list[str] = []
    matrix: dict = {}
    for m in ctx["my"]:
        if m not in ctx["charts"]:
            continue
        opts, a = _move_options(ctx, m, user_moves_sid.get(m), use_meta=round_ >= 1)
        assumptions += a
        for o in ctx["opp"]:
            if o not in ctx["charts"]:
                continue
            best = max(opts, key=lambda x: (ctx["charts"][o].get(x["type"], 1), x.get("power") or 0))
            off = ctx["charts"][o].get(best["type"], 1)
            threats, priority, a2 = _opp_threats(ctx, o)
            if a2 and a2 not in assumptions:
                assumptions.append(a2)
            risk_move = max(threats, key=lambda t: ctx["charts"][m].get(t["type"], 1), default=None)
            risk = ctx["charts"][m].get(risk_move["type"], 1) if risk_move else 1
            scarf = next((i["pct"] for i in (ctx["meta"].get(o) or {}).get("items", []) if i["name"] == "choicescarf"), 0)
            spd = S.speed_compare(ctx, m, o, fmt, scarf)
            matrix[(m, o)] = {"offense": off, "best_move": best, "risk": risk, "risk_move": risk_move,
                              "priority_moves": priority, "speed": spd}
    return matrix, assumptions


def is_mega(ctx: dict, sid: str) -> bool:
    # 영문 폼 표기로만 판단한다 ('yanmega', 'meganium' 같은 이름을 메가로 오인하지 않도록)
    return "-Mega" in (ctx["display"].get(sid, {}).get("en") or "")


def best_hit(ctx: dict, m: str, o: str, conditions: dict, fmt: str) -> dict | None:
    """내 m이 상대 o를 때릴 기술 중 데미지가 가장 큰 것 (실전 기술 또는 사용자가 알려준 기술)."""
    user = {ctx["names"][x["pokemon"]]["sid"]: x["moves"] for x in (conditions.get("my_moves") or [])
            if x["pokemon"] in ctx["names"]}
    opts, _ = _move_options(ctx, m, user.get(m), use_meta=True)
    doubles = conditions.get("battle") == "doubles" or (conditions.get("pick_count") or 3) >= 4
    best = None
    for opt in opts:
        mv = opt.get("move")
        if not mv:
            continue
        eff = ctx["charts"][o].get(mv["type"], 1)
        dmg = S.damage_for(ctx, m, o, mv, eff, fmt, doubles)
        if not dmg:
            continue
        head = dmg["results"][0]
        key = (head["ohko_pct"], head["two_hko_pct"], (head["min_pct"] + head["max_pct"]) / 2)
        if best is None or key > best["key"]:
            best = {"key": key, "name": opt["name"], "type": mv["type"], "offense": eff, "damage": dmg}
    return best


def answers(cell: dict, round_: int) -> bool:
    if round_ >= 3:  # 완화 기준
        return is_faster(cell["speed"]) and cell["offense"] >= 1
    return cell["offense"] >= 2 and (cell["risk"] < 2 or is_faster(cell["speed"]))


# ---------------------------------------------------------------- 선출
def judge_selection(ctx: dict, conditions: dict, round_: int) -> dict:
    pick = default_pick(conditions)
    matrix, assumptions = matchup_matrix(ctx, conditions, round_)

    def sids(names):
        return [ctx["names"][n]["sid"] for n in (names or []) if n in ctx["names"]]

    prefer, exclude, must = sids(conditions.get("prefer")), sids(conditions.get("exclude")), sids(conditions.get("must_answer"))
    judged_opp = [o for o in ctx["opp"] if o in ctx["charts"]]
    not_judged = [s for s in dict.fromkeys(ctx["my"] + ctx["opp"]) if s not in ctx["charts"]]

    targets = [o for o in (must or judged_opp) if o in judged_opp]
    narrowed = False
    if round_ >= 2 and not must and len(judged_opp) > pick + 1:
        # 상대가 실제로 낼 가능성이 높은 순 (메타 사용률) 상위 pick+1 마리
        targets = sorted(judged_opp, key=lambda o: -(ctx["meta"].get(o) or {}).get("usage_pct", 0))[: pick + 1]
        narrowed = True

    candidates = [m for m in ctx["my"] if m in ctx["charts"] and m not in exclude]
    profile = opponent_profile(ctx, judged_opp)
    phys_w = (profile["physical_pct"] or 50) / 100
    covers = {m: [o for o in targets if (m, o) in matrix and answers(matrix[(m, o)], round_)] for m in candidates}

    mega_rule = RULESETS.get(conditions.get("game") or "champions", {}).get("allow_mega", False)
    combos = [c for c in itertools.combinations(candidates, min(pick, len(candidates)))
              if all(p in c for p in prefer if p in candidates)
              and (not mega_rule or sum(is_mega(ctx, m) for m in c) <= 1)]  # 메가진화는 배틀당 1마리
    if not combos:
        reason = "선출 후보가 부족합니다 (제외 조건이나 조회 실패 때문)."
        normal = [m for m in candidates if not is_mega(ctx, m)]
        if mega_rule and sum(is_mega(ctx, p) for p in prefer) > 1:
            reason = "메가진화는 배틀당 1마리만 가능해서, 꼭 넣고 싶은 메가 포켓몬을 둘 다 선출할 수 없습니다."
        elif mega_rule and len(normal) + 1 < pick:
            reason = (f"메가진화는 배틀당 1마리라 메가 1마리 + 일반 포켓몬 {pick - 1}마리가 필요한데, "
                      f"일반 포켓몬이 {len(normal)}마리뿐입니다. 메가가 아닌 포켓몬을 팀에 더 넣어 주세요.")
        return {"satisfied": False, "reason": reason,
                "round": round_, "pick": pick, "selected": [], "assumptions": assumptions, "not_judged": not_judged}

    def score(c):
        covered = set().union(*[covers[m] for m in c])
        margin = sum(matrix[(m, o)]["offense"] - matrix[(m, o)]["risk"] for m in c for o in targets if (m, o) in matrix)
        bulk = sum(_bulk(ctx, m, phys_w) for m in c)  # 동점이면 상대 공격 성향을 잘 받는 조합
        return len(covered), margin, bulk

    best = max(combos, key=score)
    covered = set().union(*[covers[m] for m in best])
    uncovered = [o for o in targets if o not in covered]

    # 대면별 추천: 상대마다 선출한 3마리 중 데미지가 가장 큰 대면과 기술
    fmt = (ctx.get("basis") or {}).get("format") or conditions.get("format") or config.DEFAULT_FORMAT
    move_plan = []
    for o in judged_opp:
        cells = [(m, matrix[(m, o)]) for m in best if (m, o) in matrix]
        if not cells:
            continue
        hits = {mc[0]: best_hit(ctx, mc[0], o, conditions, fmt) for mc in cells}

        def rank(mc):
            h = hits.get(mc[0])
            return (answers(mc[1], round_), h["key"] if h else (0, 0, 0), mc[1]["offense"] - mc[1]["risk"],
                    is_faster(mc[1]["speed"]))
        m, cell = max(cells, key=rank)
        hit = hits.get(m)
        move_plan.append({
            "opponent": _label(ctx, o), "my_pokemon": _label(ctx, m),
            "move": hit["name"] if hit else cell["best_move"]["name"],
            "move_type": hit["type"] if hit else cell["best_move"]["type"],
            "offense": hit["offense"] if hit else cell["offense"], "risk": cell["risk"],
            "damage": hit["damage"] if hit else None,
            "risk_move": (cell["risk_move"] or {}).get("name"), "speed": cell["speed"]["verdict"],
            "speed_basis": cell["speed"]["basis"],
            "priority_moves": [p["name"] for p in cell["priority_moves"]],
            "answered": answers(cell, round_), "target": o in targets,
        })

    fmt = (ctx.get("basis") or {}).get("format") or conditions.get("format") or config.DEFAULT_FORMAT
    lead = max(best, key=lambda m: (len(covers[m]), ctx["detail"][m]["base_stats"]["spe"]))
    opp_megas = [_label(ctx, o) for o in judged_opp if is_mega(ctx, o)]
    rule_notes = []
    mega = ctx.get("mega") or {}
    if mega_rule:
        mine = [_label(ctx, m) for m in ctx["my"] if is_mega(ctx, m)]
        picked = mine and mine[0] in [_label(ctx, m) for m in best]
        rule_notes.append(f"메가진화는 배틀당 1번 — 내 메가진화: {mine[0] if mine else '안 함'}"
                          + (" (선출에 포함)" if picked else " (이번 선출에서는 빠짐 → 메가진화 없이 진행)" if mine else ""))
        if opp_megas:
            how = "예상" if mega.get("opp_predicted") else "지정"
            share = next((f["share"] for p in mega.get("opp_predictions", []) for f in p["forms"]
                          if f["value"] == mega.get("opp")), None)
            rule_notes.append(f"상대 메가진화 {how}: {opp_megas[0]}" + (f" (그 포켓몬을 낼 때 메가 폼 비율 {share}%)" if share else "")
                              + " — 이 폼으로 계산했습니다.")
        elif mega.get("opp_predictions") is not None:
            rule_notes.append("상대 팀에서 메가진화가 유력한 포켓몬이 없어 모두 기본 폼으로 계산했습니다.")
    excluded = []
    for m in ctx["my"]:
        if m in best:
            continue
        if m in exclude:
            reason = "사용자가 제외"
        elif m not in ctx["charts"]:
            reason = "타입·스피드 정보를 확인하지 못해 판단 제외"
        else:
            bad = [(o, matrix[(m, o)]) for o in targets if (m, o) in matrix and matrix[(m, o)]["risk"] >= 2]
            reason = f"잡을 수 있는 상대 {len(covers.get(m, []))}마리"
            if bad:
                reason += ", 약점 대면: " + ", ".join(
                    f"{_label(ctx, o)}의 {c['risk_move']['name']}({c['risk']:g}배)" for o, c in bad[:2])
        excluded.append({"pokemon": _label(ctx, m), "reason": reason})

    return {
        "satisfied": not uncovered,
        "round": round_,
        "criteria": REPLAN_CRITERIA.get(round_, "자속 기술 기준 · '2배 이상 & (안전하거나 선공)'이면 잡는다"),
        "pick": pick,
        "selected": [_label(ctx, m) for m in best],
        "lead": _label(ctx, lead),
        "targets": [_label(ctx, o) for o in targets],
        "targets_narrowed": narrowed,
        "covered": [_label(ctx, o) for o in targets if o in covered],
        "uncovered": [_label(ctx, o) for o in uncovered],
        "covers": {_label(ctx, m): [_label(ctx, o) for o in covers[m]] for m in candidates},
        "move_plan": move_plan,
        "excluded": excluded,
        "assumptions": assumptions,
        "not_judged": [_label(ctx, s) for s in not_judged],
        "opp_profile": profile,
        "defense_advice": defense_advice(ctx, profile, candidates),
        "rule_notes": rule_notes,
        "damage_assumptions": "Lv50·개체값 31, 급소·날씨·도구·특성·랭크 변화 제외. 배분은 실전 통계(없으면 '가정'으로 표시)",
    }


def judge_matchup(ctx: dict, conditions: dict) -> dict:
    matrix, assumptions = matchup_matrix(ctx, conditions, round_=1)
    if not ctx["my"] or not ctx["opp"] or (ctx["my"][0], ctx["opp"][0]) not in matrix:
        return {"satisfied": False, "reason": "두 포켓몬의 타입·스피드 정보를 모두 확인하지 못했습니다.",
                "not_judged": [_label(ctx, s) for s in ctx["my"] + ctx["opp"] if s not in ctx["charts"]]}
    m, o = ctx["my"][0], ctx["opp"][0]
    cell = matrix[(m, o)]
    user = bool(conditions.get("my_moves"))
    opts, _ = _move_options(ctx, m, [mv for x in conditions.get("my_moves") or [] for mv in x["moves"]] or None, use_meta=True)
    fmt = (ctx.get("basis") or {}).get("format") or conditions.get("format") or config.DEFAULT_FORMAT
    doubles = conditions.get("battle") == "doubles"
    for x in opts:
        if x.get("move"):
            x["damage"] = S.damage_for(ctx, m, o, x["move"], ctx["charts"][o].get(x["type"], 1), fmt, doubles)
    ranked = sorted(opts, key=lambda x: (
        -((x.get("damage") or {}).get("results") or [{}])[0].get("max_pct", 0),
        -ctx["charts"][o].get(x["type"], 1), -(x.get("power") or 0)))
    return {
        "satisfied": True,
        "my_pokemon": _label(ctx, m), "opponent": _label(ctx, o),
        "speed": cell["speed"],
        "moves_ranked": [{**{k: v for k, v in x.items() if k != "move"}, "multiplier": ctx["charts"][o].get(x["type"], 1)}
                         for x in ranked[:5]],
        "moves_source": "사용자가 알려준 기술" if user else "내 포켓몬의 실전 통계 상위 기술 + 자속 타입",
        "risk": cell["risk"], "risk_move": cell["risk_move"], "priority_moves": cell["priority_moves"],
        "opp_items": (ctx["meta"].get(o) or {}).get("items", [])[:3],
        "opp_profile": opponent_profile(ctx, [o]),
        "assumptions": assumptions,
    }


def judge_lookup(ctx: dict) -> dict:
    if not ctx["opp"]:
        return {"satisfied": False, "reason": "대상을 찾지 못했습니다."}
    sid = ctx["opp"][0]
    d = ctx["detail"].get(sid)
    meta = ctx["meta"].get(sid)
    return {"satisfied": bool(d), "pokemon": _label(ctx, sid), "detail": d,
            "sets": {k: meta.get(k) for k in ("usage_pct", "moves", "items", "abilities", "spreads", "roles")} if meta else None,
            "opp_profile": opponent_profile(ctx, [sid]) if meta else None}

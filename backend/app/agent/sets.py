"""배틀 세트 구성 — 실전 통계의 배분(Spreads)으로 실수치를 만들고, 데미지·스피드를 계산한다.

값을 지어내지 않기 위해, 쓰는 배분이 '실전 최다 배분'인지 '가정'인지 항상 label로 밝힌다.
"""
from __future__ import annotations

from app.domain.damage import calc_move
from app.domain.roles import NATURES, classify, parse_spread
from app.tools.common import to_id
from app.domain.stats import calc_stat, full_stats, max_points, system_for  # noqa: F401

BULKY_MIN_PCT = 15.0   # 이 비율 이상인 내구형(막이) 배분이 있으면 그 배분으로도 계산해 보여준다
NATURE_KO = {k: v[2] for k, v in NATURES.items()}
BOOST_NATURE = {"atk": "Adamant", "spa": "Modest", "def": "Impish", "spd": "Calm"}
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
LETTER = {"hp": "H", "atk": "A", "def": "B", "spa": "C", "spd": "D", "spe": "S"}


def _code(points: dict, mx: int) -> str:
    return "".join(LETTER[s] for s in STATS if points.get(s, 0) >= mx * 0.4) or "무투자"


def ability_of(ctx: dict, sid: str) -> str | None:
    """계산에 쓰는 특성: 실전 통계에서 50% 이상 채용된 특성, 없으면 특성이 하나뿐일 때 그 특성 (Showdown ID)."""
    meta = ctx["meta"].get(sid) or {}
    if meta.get("abilities") and meta["abilities"][0]["pct"] >= 50:
        return to_id(meta["abilities"][0]["name"])
    abilities = (ctx["detail"].get(sid) or {}).get("abilities") or []
    return to_id(abilities[0]["name"]) if len(abilities) == 1 else None


def _make(ctx: dict, sid: str, nature: str | None, points: dict, system: str, label: str, assumed: bool) -> dict:
    d = ctx["detail"][sid]
    return {"stats": full_stats(d["base_stats"], points, nature, system), "types": d["types"],
            "label": label, "assumed": assumed, "nature": nature, "points": points, "ability": ability_of(ctx, sid)}


def primary_set(ctx: dict, sid: str, system: str) -> dict:
    """내 포켓몬 한 마리당 하나의 세트: 실전 최다 배분. 모든 대면·스피드 판단에 같은 세트를 쓴다 (일관성).
    통계가 없으면 공격·특공 중 종족값이 높은 쪽 + 스피드 최대 투자를 가정한다."""
    cache = ctx.setdefault("_primary", {})
    if sid in cache:
        return cache[sid]
    mx = max_points(system)
    spreads = (ctx["meta"].get(sid) or {}).get("spreads") or []
    if spreads:
        sp = spreads[0]
        out = _make(ctx, sid, sp["nature"], sp["points"], system,
                    f"{_code(sp['points'], mx)} {NATURE_KO.get(sp['nature'], sp['nature'])} (실전 최다 {sp['pct']}%)", False)
        c = classify(sp["nature"], sp["points"], mx)
        out["side"] = c["side"]  # 물리 / 특수 / 혼합 / None(탱커·서포터)
    else:
        b = ctx["detail"][sid]["base_stats"]
        stat = "atk" if (b.get("atk") or 0) >= (b.get("spa") or 0) else "spa"
        out = _make(ctx, sid, BOOST_NATURE.get(stat), {stat: mx, "spe": mx}, system,
                    f"가정: {LETTER[stat]}S 최대 투자·보정 성격 (실전 통계 없음)", True)
        out["side"] = "물리" if stat == "atk" else "특수"
    cache[sid] = out
    return out


def move_fits_set(move: dict, s: dict) -> bool:
    """이 세트가 쓸 기술인가: 물리 배분이면 물리기, 특수 배분이면 특수기 (혼합·탱커는 둘 다)."""
    if move.get("override_offensive_stat") == "def":   # 바디프레스는 방어로 때림
        return True
    side = s.get("side")
    if side == "물리":
        return move["category"] == "Physical"
    if side == "특수":
        return move["category"] == "Special"
    return True


def attacker_set(ctx: dict, sid: str, category: str, system: str, attack_stat: str | None = None) -> dict:
    """내 포켓몬: 이 기술의 공격 능력치에 투자한 실전 배분 중 가장 흔한 것. 없으면 최대 투자 가정."""
    stat = attack_stat or ("atk" if category == "Physical" else "spa")
    mx = max_points(system)
    meta = ctx["meta"].get(sid) or {}
    for sp in meta.get("spreads", []):
        if sp["points"].get(stat, 0) >= mx * 0.6:
            return _make(ctx, sid, sp["nature"], sp["points"], system,
                         f"{_code(sp['points'], mx)} {NATURE_KO.get(sp['nature'], sp['nature'])} (실전 {sp['pct']}%)", False)
    pts = {stat: mx, "hp": mx if system == "sp" else 4}
    return _make(ctx, sid, BOOST_NATURE.get(stat), pts, system,
                 f"가정: {LETTER[stat]} 최대 투자·보정 성격", True)


def defender_sets(ctx: dict, sid: str, system: str) -> list[dict]:
    """상대 포켓몬: 실전 최다 배분 + (있으면) 가장 흔한 내구형 배분. 통계가 없으면 무투자 가정."""
    mx = max_points(system)
    meta = ctx["meta"].get(sid) or {}
    out = []
    spreads = meta.get("spreads") or []
    if spreads:
        sp = spreads[0]
        out.append(_make(ctx, sid, sp["nature"], sp["points"], system,
                         f"최다 배분 {_code(sp['points'], mx)} {NATURE_KO.get(sp['nature'], sp['nature'])} ({sp['pct']}%)", False))
    roles = (meta.get("roles") or {}).get("roles", [])
    bulky = next((r for r in roles if r["kind"] == "tank" and r["pct"] >= BULKY_MIN_PCT and r.get("typical_spread")), None)
    if bulky:
        nature, points = parse_spread(bulky["typical_spread"])
        key = (nature, tuple(points.get(s, 0) for s in STATS))
        if not out or key != (out[0]["nature"], tuple(out[0]["points"].get(s, 0) for s in STATS)):
            out.append(_make(ctx, sid, nature, points, system,
                             f"{bulky['role']}형 {_code(points, mx)} {NATURE_KO.get(nature, nature)} ({bulky['pct']}%)", False))
    if not out:
        out.append(_make(ctx, sid, None, {}, system, "가정: 무보정·무투자 (실전 통계 없음)", True))
    return out


def damage_for(ctx: dict, my: str, opp: str, move: dict, effectiveness: float, fmt: str, doubles: bool) -> dict | None:
    """내 포켓몬이 기술로 상대를 때릴 때: 상대 배분별 데미지 %와 확정/난수 타수."""
    system = system_for(fmt)
    if not move.get("base_power") or move.get("category") == "Status":
        return None
    attacker = primary_set(ctx, my, system)   # 대면마다 배분을 바꾸지 않는다
    rows = []
    for d in defender_sets(ctx, opp, system):
        r = calc_move(move, attacker, d, effectiveness, doubles, ctx["charts"].get(opp))
        if r:
            rows.append({**r, "vs": d["label"], "assumed": d["assumed"]})
    if not rows:
        return None
    return {"attacker": attacker["label"], "attacker_assumed": attacker["assumed"], "results": rows}


# ---------------------------------------------------------------- 스피드
def _speed_value(base: int, points: int, mod: str, system: str) -> int:
    nature = "Jolly" if mod == "+" else "Brave" if mod == "-" else None
    return calc_stat("spe", base, points, nature, system)


def my_speed(ctx: dict, sid: str, system: str) -> tuple[int, str, bool]:
    """내 스피드: 실전 최다 배분 기준. 통계가 없으면 최속(최대 투자·보정) 가정."""
    base = ctx["detail"][sid]["base_stats"]["spe"]
    spreads = (ctx["meta"].get(sid) or {}).get("spreads") or []
    if spreads:
        sp = spreads[0]
        plus, minus, ko = NATURES.get(sp["nature"], (None, None, sp["nature"]))
        mod = "+" if plus == "spe" else "-" if minus == "spe" else "0"
        return _speed_value(base, sp["points"].get("spe", 0), mod, system), f"실전 최다 배분({ko}) 기준", False
    return _speed_value(base, max_points(system), "+", system), "최속 가정", True


def speed_compare(ctx: dict, my: str, opp: str, fmt: str, scarf_pct: float) -> dict:
    """상대 실전 배분 분포 중 내 스피드보다 느린 비율로 선공 여부를 판단한다."""
    system = system_for(fmt)
    mine, how, assumed = my_speed(ctx, my, system)
    base = ctx["detail"][opp]["base_stats"]["spe"]
    dist = ((ctx["meta"].get(opp) or {}).get("roles") or {}).get("speed_dist") or []
    if dist:
        total = sum(r["pct"] for r in dist) or 1
        slower = sum(r["pct"] for r in dist if _speed_value(base, r["points"], r["nature"], system) < mine) / total
        tie = sum(r["pct"] for r in dist if _speed_value(base, r["points"], r["nature"], system) == mine) / total
        top = max(dist, key=lambda r: r["pct"])
        opp_typ = _speed_value(base, top["points"], top["nature"], system)
        basis_opp = f"상대 최다 {opp_typ}"
    else:
        lo = _speed_value(base, 0, "0", system)
        hi = _speed_value(base, max_points(system), "+", system)
        slower = 1.0 if mine > hi else 0.0 if mine < lo else 0.5
        tie = 0.0
        basis_opp = f"상대 범위 {lo}~{hi}"
    if slower >= 0.9:
        verdict = "선공 유력"
    elif slower + tie <= 0.1:
        verdict = "후공 유력"
    else:
        verdict = "배분에 따라 다름"
    basis = f"내 스피드 {mine}({how}) vs {basis_opp} · 상대 배분의 {slower * 100:.0f}%보다 빠름"
    if tie >= 0.05:
        basis += f" (동속 {tie * 100:.0f}%)"
    scarf = scarf_pct >= 10
    if scarf:
        scarf_speed = _speed_value(base, dist[0]["points"] if dist else max_points(system),
                                   dist[0]["nature"] if dist else "+", system) * 3 // 2
        basis += f" · 상대 구애스카프 채용 {scarf_pct:.0f}%: 스카프면 {scarf_speed}"
        if scarf_speed >= mine and verdict == "선공 유력":
            basis += " → 후공"
    return {"verdict": verdict, "basis": basis, "scarf_risk": scarf, "my_speed": mine,
            "faster_share": round(slower * 100, 1), "assumed": assumed}

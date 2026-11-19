"""포켓몬 운용 형태 분류 — 노력치(SP) 배분과 성격으로 '딜러/탱커'를 나눈다. 규칙 기반, LLM 미사용.

딜러: [고속 / 딜탱] × [물리 / 특수 / 혼합]   예) AS 물리 고속 딜러(한카리아스), HC 특수 딜탱(누리레느)
탱커: 물리막이 / 특수막이 / 양막이           예) HB 물리막이(스텔스록 깔이 한카리아스)
서포터: 공격 투자 없이 스피드만 올린 배분     예) HS 고속 서포터(스텔스록·도발 선봉)

배분 표기는 H(HP) A(공격) B(방어) C(특공) D(특방) S(스피드).
같은 포켓몬도 배분이 여러 갈래라 Smogon의 '모든' 배분을 가중치대로 합산해 비율로 돌려준다.
"""
from __future__ import annotations

STATS = ("hp", "atk", "def", "spa", "spd", "spe")
LETTER = {"hp": "H", "atk": "A", "def": "B", "spa": "C", "spd": "D", "spe": "S"}

# 성격: (올라가는 능력, 내려가는 능력, 한국어 이름). 무보정 성격은 (None, None)
NATURES = {
    "Hardy": (None, None, "노력"), "Docile": (None, None, "온순"), "Serious": (None, None, "성실"),
    "Bashful": (None, None, "수줍음"), "Quirky": (None, None, "변덕"),
    "Lonely": ("atk", "def", "외로움"), "Brave": ("atk", "spe", "용감"), "Adamant": ("atk", "spa", "고집"),
    "Naughty": ("atk", "spd", "개구쟁이"), "Bold": ("def", "atk", "대담"), "Relaxed": ("def", "spe", "무사태평"),
    "Impish": ("def", "spa", "장난꾸러기"), "Lax": ("def", "spd", "촐랑"), "Timid": ("spe", "atk", "겁쟁이"),
    "Hasty": ("spe", "def", "성급"), "Jolly": ("spe", "spa", "명랑"), "Naive": ("spe", "spd", "천진난만"),
    "Modest": ("spa", "atk", "조심"), "Mild": ("spa", "def", "의젓"), "Quiet": ("spa", "spe", "냉정"),
    "Rash": ("spa", "spd", "덜렁"), "Calm": ("spd", "atk", "차분"), "Gentle": ("spd", "def", "얌전"),
    "Sassy": ("spd", "spe", "건방"), "Careful": ("spd", "spa", "신중"),
}

INVEST = 0.4       # 최대치의 40% 이상 투자하면 배분 표기에 넣는다
NATURE_W = 0.25    # 성격 보정의 비중
ATTACKER = 0.5     # 공격(또는 특공) 실효 투자가 이 이상이면 딜러
SPEEDY = 0.5       # 스피드 실효 투자가 이 이상이면 고속
SIDE_GAP = 0.3     # 물리/특수, 방어/특방 한쪽으로 기울었다고 볼 차이

ROLE_ORDER = ["물리 고속 딜러", "물리 딜탱", "특수 고속 딜러", "특수 딜탱", "혼합 고속 딜러", "혼합 딜탱",
              "물리막이", "특수막이", "양막이", "고속 서포터"]


def parse_spread(key: str) -> tuple[str, dict[str, int]]:
    nature, _, pts = key.partition(":")
    vals = [int(x) for x in pts.split("/")] if pts else [0] * 6
    return nature, dict(zip(STATS, vals))


def classify(nature: str, points: dict[str, int], max_point: int) -> dict:
    plus, minus, nature_ko = NATURES.get(nature, (None, None, nature))
    inv = {s: points.get(s, 0) / max_point for s in STATS}
    eff = {s: inv[s] + (NATURE_W if s == plus else 0) - (NATURE_W if s == minus else 0) for s in STATS}
    code = "".join(LETTER[s] for s in STATS if inv[s] >= INVEST) or "-"

    if max(eff["atk"], eff["spa"]) >= ATTACKER:
        if eff["atk"] - eff["spa"] >= SIDE_GAP:
            side = "물리"
        elif eff["spa"] - eff["atk"] >= SIDE_GAP:
            side = "특수"
        else:
            side = "혼합"
        role = f"{side} {'고속 딜러' if eff['spe'] >= SPEEDY else '딜탱'}"
        kind = "attacker"
    elif eff["spe"] >= SPEEDY and max(eff["def"], eff["spd"]) < ATTACKER:
        side, role, kind = None, "고속 서포터", "support"
    else:
        side = None
        if eff["def"] - eff["spd"] >= SIDE_GAP:
            role = "물리막이"
        elif eff["spd"] - eff["def"] >= SIDE_GAP:
            role = "특수막이"
        else:
            role = "양막이"
        kind = "tank"
    return {"role": role, "kind": kind, "side": side, "code": code, "nature": nature, "nature_ko": nature_ko}


def aggregate(spreads: dict[str, float], total: float) -> dict | None:
    """Smogon Spreads 전체를 분류해 형태별 비율, 공격 성향(물리/특수)을 계산한다."""
    if not spreads or total <= 0:
        return None
    parsed = [(parse_spread(k), w) for k, w in spreads.items() if w > 0]
    if not parsed:
        return None
    # Champions 포맷은 SP(스탯당 최대 32), 그 외는 노력치(최대 252)
    max_point = 32 if max(max(p.values()) for (_, p), _ in parsed) <= 32 else 252

    roles: dict[str, dict] = {}
    weight_sum = 0.0
    for (nature, points), w in parsed:
        c = classify(nature, points, max_point)
        r = roles.setdefault(c["role"], {"role": c["role"], "kind": c["kind"], "side": c["side"], "w": 0.0, "examples": {}})
        r["w"] += w
        ex_key = f"{c['code']} {c['nature_ko']}"
        r["examples"][ex_key] = r["examples"].get(ex_key, 0) + w
        r.setdefault("raw", {})[f"{nature}:{'/'.join(str(points[s]) for s in STATS)}"] = \
            r.get("raw", {}).get(f"{nature}:{'/'.join(str(points[s]) for s in STATS)}", 0) + w
        weight_sum += w

    def pct(x: float) -> float:
        return round(x / weight_sum * 100, 1)

    out_roles = []
    for name in ROLE_ORDER:
        r = roles.get(name)
        if not r or pct(r["w"]) < 0.5:  # 0.5% 미만인 드문 배분은 목록에서 뺀다 (합계 계산에는 포함)
            continue
        top_ex = max(r["examples"].items(), key=lambda kv: kv[1])[0]
        out_roles.append({"role": name, "kind": r["kind"], "pct": pct(r["w"]), "typical": top_ex,
                          # 데미지 계산에 쓰는 이 형태의 대표 배분 원문 (예: 'Impish:32/0/32/0/2/0')
                          "typical_spread": max(r["raw"].items(), key=lambda kv: kv[1])[0]})
    out_roles.sort(key=lambda r: -r["pct"])

    attacker_w = sum(r["w"] for r in roles.values() if r["kind"] == "attacker")
    phys = sum(r["w"] for r in roles.values() if r["side"] == "물리") + 0.5 * sum(
        r["w"] for r in roles.values() if r["side"] == "혼합")
    return {
        "roles": out_roles,
        "main_role": out_roles[0]["role"],
        "attacker_pct": pct(attacker_w),
        "tank_pct": pct(sum(r["w"] for r in roles.values() if r["kind"] == "tank")),
        "support_pct": pct(sum(r["w"] for r in roles.values() if r["kind"] == "support")),
        # 딜러 배분 중 물리 쪽 비율 (혼합은 절반씩)
        "physical_share": round(phys / attacker_w * 100, 1) if attacker_w > 0 else None,
        "point_system": "SP(최대 32)" if max_point == 32 else "노력치(최대 252)",
        "coverage_pct": round(weight_sum / total * 100, 1),
        "spread_count": len(parsed),
        # 스피드 분포: (스피드 투자, 성격 보정) 별 비율 — 상대보다 빠른 비율 계산용
        "speed_dist": _speed_dist(parsed, weight_sum),
    }


def _speed_dist(parsed, weight_sum: float) -> list[dict]:
    acc: dict[tuple[int, str], float] = {}
    for (nature, points), w in parsed:
        plus, minus, _ = NATURES.get(nature, (None, None, ""))
        mod = "+" if plus == "spe" else "-" if minus == "spe" else "0"
        key = (points.get("spe", 0), mod)
        acc[key] = acc.get(key, 0) + w
    rows = [{"points": k[0], "nature": k[1], "pct": round(v / weight_sum * 100, 2)} for k, v in acc.items()]
    return sorted(rows, key=lambda r: -r["pct"])


# ---------------------------------------------------------------- 파티 단위
def team_profile(members: list[dict], move_shares: dict[str, float | None]) -> dict:
    """상대 파티의 공격 성향.

    members: [{"name": 표시 이름, "sid": 식별자, "roles": aggregate() 결과 또는 None}]
    move_shares: {sid: 실전 공격기 중 물리 기술 비율(%) 또는 None}
    파티 물리 비율 = 각 포켓몬의 물리 비중을 '딜러로 나올 확률'로 가중 평균한다.
    배분 통계가 없는 포켓몬은 기술 통계로, 그것도 없으면 판단에서 뺀다 (값을 지어내지 않음).
    """
    rows, num, den, unknown = [], 0.0, 0.0, []
    for m in members:
        r = m.get("roles")
        move_phys = move_shares.get(m["sid"])
        if r and r.get("physical_share") is not None:
            share, weight, basis = r["physical_share"], r["attacker_pct"] / 100, "배분 통계"
        elif r:  # 통계는 있지만 공격 배분이 없음 → 탱커/서포터. 물리·특수 평균에는 넣지 않는다
            rows.append({"name": m["name"], "physical_share": None, "move_physical_share": move_phys,
                         "attacker_pct": r["attacker_pct"], "main_role": r["main_role"], "roles": r["roles"][:3],
                         "basis": "공격 배분 없음 (탱커·서포터)"})
            continue
        elif move_phys is not None:
            share, weight, basis = move_phys, 1.0, "기술 통계"
        else:
            unknown.append(m["name"])
            rows.append({"name": m["name"], "physical_share": None, "basis": "판단 불가 (통계 없음)", "roles": []})
            continue
        num += share * weight
        den += weight
        rows.append({
            "name": m["name"],
            "physical_share": share,
            "move_physical_share": move_phys,
            "attacker_pct": r["attacker_pct"] if r else None,
            "main_role": r["main_role"] if r else None,
            "roles": (r or {}).get("roles", [])[:3],
            "basis": basis,
        })
    physical = round(num / den, 1) if den > 0 else None
    if physical is None:
        lean = "판단 불가"
    elif physical >= 60:
        lean = "물리 중심"
    elif physical <= 40:
        lean = "특수 중심"
    else:
        lean = "물리·특수 균형"
    return {
        "physical_pct": physical,
        "special_pct": round(100 - physical, 1) if physical is not None else None,
        "lean": lean,
        "attackers": sum(1 for x in rows if (x.get("attacker_pct") or 0) >= 50),
        "walls": [x["name"] for x in rows if x.get("main_role") in ("물리막이", "특수막이", "양막이")],
        "members": rows,
        "unknown": unknown,
    }

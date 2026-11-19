"""F3007 Tool 호출 및 결과 통합.

- 계획의 작업을 Tool 호출로 바꿔 실행하고, 결과를 ctx에 구조화해 모은다.
- 앞선 Tool 결과(이름→식별자, 기술 ID, 타입)를 다음 Tool의 입력으로 넘긴다.
- Tool 하나가 실패해도 사유를 failures에 남기고 다음으로 진행한다.
- 같은 Tool·같은 입력은 다시 부르지 않는다(재계획 시 결과 재사용).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from app.events.bus import Emitter
from app.tools import call_tool
from app.domain.rulesets import RULESETS
from app.tools.common import to_id
from app.tools.names import resolve_one

ATTACK_MOVE_MIN_PCT = 20.0  # 이 비율 이상 채용된 기술만 '주력기'로 본다


def new_ctx() -> dict:
    return {"names": {}, "display": {}, "unresolved": [], "my": [], "opp": [], "basis": None,
            "meta": {}, "meta_missing": [], "detail": {}, "moves": {}, "charts": {},
            "tool_cache": {}, "failures": [], "calls": 0, "cache_hits": 0}


class Executor:
    def __init__(self, ctx: dict, conditions: dict, intent: str, emitter: Emitter, aliases: dict | None = None):
        self.aliases = aliases or {}  # 사용자가 후보에서 고른 이름 {입력: 영문 이름}
        self.ctx = ctx
        self.c = conditions
        self.intent = intent
        self.em = emitter

    # ------------------------------------------------------------------ 공통
    def call(self, tool: str, args: dict) -> dict:
        key = tool + json.dumps(args, ensure_ascii=False, sort_keys=True)
        if key in self.ctx["tool_cache"]:
            self.ctx["cache_hits"] += 1
            return self.ctx["tool_cache"][key]
        self.em.emit("tool_call_start", "execute", tool=tool, args=args)
        t0 = time.perf_counter()
        result = call_tool(tool, args)
        ms = round((time.perf_counter() - t0) * 1000)
        err = result.get("error") or {}
        self.em.emit("tool_call_end", "execute", tool=tool, args=args, ok=result["ok"],
                     error_code=err.get("code"), error_message=err.get("message"), duration_ms=ms)
        self.ctx["calls"] += 1
        self.ctx["tool_cache"][key] = result
        if not result["ok"]:
            self.ctx["failures"].append({"tool": tool, "args": args, "code": err.get("code"), "message": err.get("message")})
        return result

    def run(self, steps: list[dict]) -> None:
        for step in steps:
            getattr(self, "_" + step["action"])()
            if step["action"] == "resolve" and self.ctx["unresolved"]:
                # 이름을 확인해야 하면 나머지 Tool은 부르지 않고 바로 되묻는다
                self.em.emit("decision", "execute", decision="stop_for_names",
                             unresolved=[u["input"] for u in self.ctx["unresolved"]])
                return
            if step["action"] == "mega" and self.ctx.get("mega_ask"):
                # 내 팀에서 누가 메가진화할지 정해야 나머지 계산을 할 수 있다
                self.em.emit("decision", "execute", decision="ask_mega",
                             options=[o["pokemon"] for o in self.ctx["mega"]["my_options"]])
                return

    def my_all(self) -> list[str]:
        """내 팀 조회 대상: 기본 폼 + 메가스톤을 든 포켓몬의 메가 폼 (둘 다 계산해서 누가 메가진화할지 고른다)."""
        return list(dict.fromkeys(self.ctx["my"] + list((self.ctx.get("my_mega_forms") or {}).values())))

    def sid(self, raw: str) -> str:
        r = self.ctx["names"].get(raw)
        return r["sid"] if r else to_id(raw)

    def en(self, sid: str) -> str:
        return self.ctx["display"].get(sid, {}).get("en", sid)

    # ------------------------------------------------------------------ 작업들
    def _roles(self) -> tuple[list[str], list[str]]:
        c = self.c
        if self.intent == "plan_selection":
            return list(c.get("my_team") or []), list(c.get("opp_team") or [])
        if self.intent == "matchup_advice":
            return [c["focus_my"]], [c["focus_opp"]]
        return [], [c["lookup_target"]]

    def _resolve(self) -> None:
        my, opp = self._roles()
        extra = (self.c.get("prefer") or []) + (self.c.get("exclude") or []) + (self.c.get("must_answer") or [])
        extra += [m["pokemon"] for m in (self.c.get("my_moves") or [])]
        names = list(dict.fromkeys(my + opp + extra))
        query = [self.aliases.get(n, n) for n in names]
        r = self.call("resolve_names", {"names": query, "kind": "pokemon"})
        results = r["data"]["results"] if r["ok"] else [{"input": n, "found": False, "reason": "NOT_FOUND"} for n in query]
        if not r["ok"] and r["error"]["code"] == "AMBIGUOUS":  # 전부 모호하면 Tool이 실패로 돌려주므로 항목별로 다시 본다
            results = [resolve_one(n, "pokemon") for n in query]
        allow_mega = RULESETS.get(self.c.get("game") or "champions", {}).get("allow_mega", True)
        for raw, item in zip(names, results):
            item = {**item, "input": raw}
            if item.get("found") and item.get("is_mega") and not allow_mega:
                base = item["en"].split("-Mega")[0]
                b = resolve_one(base, "pokemon")
                self.ctx["unresolved"].append({"input": raw, "reason": "NOT_IN_GAME", "candidates": [
                    {"label": b.get("ko") or base, "value": base}]})
                continue
            if item["found"]:
                entry = {"sid": item["showdown_id"], "en": item["en"], "ko": item.get("ko")}
            elif item["reason"] == "NOT_FOUND" and raw.isascii():
                entry = {"sid": to_id(raw), "en": raw.strip(), "ko": None}  # 영문 폼 표기(Garchomp-Mega-Z 등)는 그대로 시도
            else:
                self.ctx["unresolved"].append(item)
                continue
            self.ctx["names"][raw] = entry
            self.ctx["display"].setdefault(entry["sid"], {"en": entry["en"], "ko": entry["ko"]})
        self.ctx["my"] = [self.sid(n) for n in my if n in self.ctx["names"]]
        self.ctx["opp"] = [self.sid(n) for n in opp if n in self.ctx["names"]]

    def _meta(self, sids: list[str]) -> None:
        if not sids:
            return
        args = {"pokemon": [self.en(s) for s in sids], "top_n": 8}
        if self.c.get("format"):
            args["format"] = self.c["format"]
        if self.c.get("month"):
            args["month"] = self.c["month"]
        r = self.call("get_meta_usage", args)
        if not r["ok"]:
            self.ctx["meta_missing"] += sids
            return
        d = r["data"]
        self.ctx["basis"] = {k: d[k] for k in ("format", "month", "rating", "battles", "estimated", "notes")}
        for entry, sid in zip(d["pokemon"], sids):
            if entry["found"]:
                self.ctx["meta"][sid] = entry
            elif sid not in self.ctx["meta_missing"]:
                self.ctx["meta_missing"].append(sid)

    def _meta_opp(self) -> None:
        self._meta([s for s in self.ctx["opp"] if s not in self.ctx["meta"]])

    def _meta_my(self) -> None:
        self._meta([s for s in self.my_all() if s not in self.ctx["meta"]])

    def _detail(self) -> None:
        targets = [s for s in dict.fromkeys(self.my_all() + self.ctx["opp"]) if s not in self.ctx["detail"]]
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda s: self.call("get_pokemon_detail", {"name": self.en(s)}), targets))
        for sid, r in zip(targets, results):
            if r["ok"]:
                self.ctx["detail"][sid] = r["data"]
                ko = r["data"].get("ko_name")
                if ko and not self.ctx["display"].get(sid, {}).get("ko"):
                    self.ctx["display"].setdefault(sid, {"en": sid})["ko"] = ko

    def _move_ids(self, sids: list[str], min_pct: float = ATTACK_MOVE_MIN_PCT) -> list[str]:
        ids = []
        for s in sids:
            for m in (self.ctx["meta"].get(s) or {}).get("moves", []):
                if m["pct"] >= min_pct:
                    ids.append(m["name"])
        return ids

    def _fetch_moves(self, ids: list[str]) -> None:
        ids = [i for i in dict.fromkeys(ids) if to_id(i) not in self.ctx["moves"]]
        for i in range(0, len(ids), 24):
            r = self.call("get_move_info", {"moves": ids[i:i + 24]})
            if r["ok"]:
                for m in r["data"]["moves"]:
                    if m["found"]:
                        self.ctx["moves"][m["id"]] = m
                        self.ctx["moves"].setdefault(to_id(m["input"]), m)

    def _moves_opp(self) -> None:
        # 상대는 상위 기술 전부를 조회한다: 주력기 판단(≥20%) + 물리/특수 기술 비율 계산에 쓴다
        self._fetch_moves(self._move_ids(self.ctx["opp"], min_pct=0))

    def _moves_my(self) -> None:
        user_moves = [mv for m in (self.c.get("my_moves") or []) for mv in m["moves"]]
        self._fetch_moves(user_moves + self._move_ids(self.my_all()))

    def ability_for(self, sid: str) -> str | None:
        """상성에 반영할 특성: 통계에서 50% 이상 채용된 특성, 없으면 특성이 하나뿐일 때 그 특성."""
        meta = self.ctx["meta"].get(sid)
        if meta and meta.get("abilities") and meta["abilities"][0]["pct"] >= 50:
            return meta["abilities"][0]["name"]
        abilities = (self.ctx["detail"].get(sid) or {}).get("abilities") or []
        return abilities[0]["name"] if len(abilities) == 1 else None

    def _matchup(self) -> None:
        for sid in dict.fromkeys(self.my_all() + self.ctx["opp"]):
            d = self.ctx["detail"].get(sid)
            if not d or not d.get("judgeable") or sid in self.ctx["charts"]:
                continue
            args = {"defend_types": d["types"]}
            ability = self.ability_for(sid)
            if ability:
                args["defender_ability"] = ability
            r = self.call("get_type_matchup", args)
            if r["ok"]:
                self.ctx["charts"][sid] = {row["attack_type"]: row["multiplier"] for row in r["data"]["matchups"]}


# ---------------------------------------------------------------- 메가진화 (챔피언스: 배틀당 1마리)
OPP_HOLDER_MIN = 50.0  # 상대가 메가스톤을 들었다고 예상할 메가 확률(%)


def _base_en(en: str) -> str:
    return en.split("-Mega")[0]


def _mega(self) -> None:
    """내 팀: 메가진화 가능한 포켓몬과 폼을 찾고, 누가 메가진화할지 정해졌는지 확인한다.
    상대 팀: 실전 통계에서 메가 폼 비율로 누가 메가진화할지 예상한다.
    정해진 메가 포켓몬은 메가 폼으로 바꿔서 이후 조회·계산을 한다 (나머지는 기본 폼)."""
    from app.tools import names as N
    ctx, c = self.ctx, self.c

    def species_of(sid):
        en = ctx["display"][sid]["en"]
        base = _base_en(en)
        r = N.resolve_one(base, "pokemon")
        return base, (r.get("ko") if r.get("found") else None) or base

    sides = {"my": list(ctx["my"]), "opp": list(ctx["opp"])}
    variants: dict[str, list[dict]] = {}
    query: list[str] = []
    for side, sids in sides.items():
        for sid in sids:
            base, ko = species_of(sid)
            vs = N._species_variants({"en": base, "ko": ko})[1:]
            variants[sid] = vs
            query += [base] + [v["value"] for v in vs] if vs else []
    query = list(dict.fromkeys(query))
    usage: dict[str, float] = {}
    for i in range(0, len(query), 24):
        args = {"pokemon": query[i:i + 24], "top_n": 1}
        if c.get("format"):
            args["format"] = c["format"]
        r = self.call("get_meta_usage", args)
        if r["ok"]:
            for p in r["data"]["pokemon"]:
                if p.get("found"):
                    usage[p["input"]] = p["usage_pct"]

    def options(sid):
        """이 포맷 통계에 실제로 나오는 메가 폼만 (안 쓰이거나 금지된 폼은 뺀다)."""
        out = []
        for v in variants.get(sid, []):
            if v["value"] in usage:
                pk = N.resolve_one(v["value"], "pokemon")
                out.append({"label": v["label"], "value": v["value"], "usage_pct": usage[v["value"]],
                            "sprite": N.sprite_url(pk.get("pokeapi_name"))})
        return out

    # --- 내 팀
    my_opts = []
    explicit = [s for s in ctx["my"] if "-Mega" in ctx["display"][s]["en"]]
    for sid in ctx["my"]:
        base, ko = species_of(sid)
        opts = options(sid)
        if sid in explicit:  # 사용자가 이미 메가 폼으로 말한 경우 그 폼도 후보에
            en = ctx["display"][sid]["en"]
            if not any(o["value"] == en for o in opts):
                pk = N.resolve_one(en, "pokemon")
                opts.insert(0, {"label": pk.get("ko") or en, "value": en, "usage_pct": usage.get(en),
                                "sprite": N.sprite_url(pk.get("pokeapi_name"))})
        if opts:
            pk = N.resolve_one(base, "pokemon")
            my_opts.append({"pokemon": ko, "base": base, "sprite": N.sprite_url(pk.get("pokeapi_name")), "forms": opts})
    # 메가스톤을 든 포켓몬: {기본 영문: 메가 영문}. 여러 마리 가능, 배틀에서 메가진화는 그중 1마리
    holders = c.get("my_megas")
    if holders is None and explicit:  # '메가한카리아스z'처럼 메가 폼으로 말한 포켓몬은 스톤을 든 것으로 본다
        holders = {_base_en(ctx["display"][s]["en"]): ctx["display"][s]["en"] for s in explicit}
    ctx["mega"] = {"my_options": my_opts, "my_holders": holders or {}}
    if my_opts and holders is None:
        ctx["mega_ask"] = True

    # --- 상대 팀: 메가스톤 보유 예상 (여러 마리 가능)
    # ① 포켓몬 자체의 메가 폼 비율 — 상위 레이팅(가장 높은 컷) 통계
    # ② 팀 구성 — 이 상대 팀원들과 '메가 폼'이 같이 쓰인 정도 vs '기본 폼'이 같이 쓰인 정도 (가능도 비)
    opp_species = {sid: species_of(sid) for sid in ctx["opp"]}
    opp_forms = {sid: options(sid) for sid in ctx["opp"]}
    all_names = {sid: [opp_species[sid][0]] + [f["value"] for f in opp_forms[sid]] for sid in ctx["opp"]}
    hi_usage, fit, hi_rating = {}, {}, None
    cand = [n for sid in ctx["opp"] if opp_forms[sid] for n in all_names[sid]]
    if cand:
        teammates = list(dict.fromkeys(n for names in all_names.values() for n in names))[:30]
        args = {"pokemon": cand[:24], "top_n": 1, "rating": 9999, "teammates": teammates}
        if c.get("format"):
            args["format"] = c["format"]
        r = self.call("get_meta_usage", args)
        if r["ok"]:
            hi_rating = r["data"]["rating"]
            for p in r["data"]["pokemon"]:
                if p.get("found"):
                    hi_usage[p["input"]] = p["usage_pct"]
                    fit[p["input"]] = p.get("team_fit") or {}

    def team_fit(name: str, sid: str) -> float | None:
        """name(폼)이 이 상대 팀의 다른 포켓몬들(종 단위)과 함께 쓰인 평균 비율(%)."""
        if name not in fit:
            return None
        vals = [sum(fit[name].get(n, 0) for n in all_names[o]) for o in ctx["opp"] if o != sid]
        return sum(vals) / len(vals) if vals else None

    preds = []
    for sid in ctx["opp"]:
        base, ko = opp_species[sid]
        forms = opp_forms[sid]
        if not forms:
            continue
        src = hi_usage if any(f["value"] in hi_usage for f in forms) else usage  # 상위 컷에 없으면 기본 컷
        total = src.get(base, 0) + sum(src.get(f["value"], 0) for f in forms)
        if total <= 0:
            continue
        base_fit = team_fit(base, sid)
        weights = {"base": src.get(base, 0) / total}
        for f in forms:
            f["share"] = round(src.get(f["value"], 0) / total * 100, 1)   # ① 메가 폼 비율
            f_fit = team_fit(f["value"], sid)
            ratio = (f_fit + 1) / (base_fit + 1) if f_fit is not None and base_fit is not None else 1.0
            weights[f["value"]] = src.get(f["value"], 0) / total * ratio
            f["team_fit"] = round(f_fit, 1) if f_fit is not None else None
        z = sum(weights.values()) or 1
        for f in forms:
            f["predicted"] = round(weights[f["value"]] / z * 100, 1)       # ①+② 보정한 메가 확률
        forms.sort(key=lambda f: -f["predicted"])
        pk = N.resolve_one(base, "pokemon")
        preds.append({"pokemon": ko, "base": base, "sprite": N.sprite_url(pk.get("pokeapi_name")),
                      "mega_share": round(sum(f["share"] for f in forms), 1),
                      "mega_pct": round(sum(f["predicted"] for f in forms), 1),
                      "base_team_fit": round(base_fit, 1) if base_fit is not None else None,
                      "rating": hi_rating if src is hi_usage else None, "forms": forms})
    preds.sort(key=lambda p: -p["mega_pct"])
    # 보유자 결정: 사용자가 정했으면 그대로, 아니면 메가 확률 50% 이상인 포켓몬은 스톤을 든 것으로 예상
    opp_holders = c.get("opp_megas")
    opp_explicit = {_base_en(ctx["display"][s]["en"]): ctx["display"][s]["en"]
                    for s in ctx["opp"] if "-Mega" in ctx["display"][s]["en"]}
    predicted = opp_holders is None
    if opp_holders is None:
        opp_holders = {p["base"]: p["forms"][0]["value"] for p in preds if p["mega_pct"] >= OPP_HOLDER_MIN}
        opp_holders.update(opp_explicit)
    ctx["mega"].update({"opp_predictions": preds, "opp_holders": opp_holders, "opp_predicted": predicted,
                        "opp_rating": hi_rating})

    # --- 정해진 메가만 메가 폼으로, 나머지는 기본 폼으로
    def apply(side, chosen: dict[str, str]):
        """chosen: {기본 영문: 메가 영문}. 여기 있는 포켓몬만 메가 폼으로, 나머지는 기본 폼으로 (한 번에 적용)."""
        new = []
        for sid in ctx[side]:
            en = ctx["display"][sid]["en"]
            base = _base_en(en)
            pick = chosen.get(base)
            target = pick if pick and pick != "none" else base
            if target != en:
                r = N.resolve_one(target, "pokemon")
                nsid = to_id(target)
                ctx["display"][nsid] = {"en": target, "ko": r.get("ko") if r.get("found") else target}
                for raw, e in ctx["names"].items():
                    if e["sid"] == sid:
                        ctx["names"][raw] = {"sid": nsid, "en": target, "ko": ctx["display"][nsid]["ko"]}
                sid = nsid
            new.append(sid)
        ctx[side] = new
    if not ctx.get("mega_ask"):
        apply("my", {})  # 내 팀은 기본 폼 기준, 메가 폼은 아래에서 따로 연결
        forms = {}
        for base_en, mega_en in (ctx["mega"]["my_holders"] or {}).items():
            if not mega_en or mega_en == "none":
                continue
            base_sid = next((s for s in ctx["my"] if _base_en(ctx["display"][s]["en"]) == base_en), None)
            if base_sid:
                r = N.resolve_one(mega_en, "pokemon")
                msid = to_id(mega_en)
                ctx["display"][msid] = {"en": mega_en, "ko": r.get("ko") if r.get("found") else mega_en}
                forms[base_sid] = msid
        ctx["my_mega_forms"] = forms
        apply("opp", ctx["mega"]["opp_holders"] or {})  # 상대 스톤 보유 예상(또는 지정) 전부 메가 폼으로
        self.em.emit("decision", "execute", decision="mega", my_holders=ctx["mega"]["my_holders"],
                     opp_holders=ctx["mega"]["opp_holders"], opp_predicted=ctx["mega"]["opp_predicted"])


Executor._mega = _mega

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
        self._meta([s for s in self.ctx["my"] if s not in self.ctx["meta"]])

    def _detail(self) -> None:
        targets = [s for s in dict.fromkeys(self.ctx["my"] + self.ctx["opp"]) if s not in self.ctx["detail"]]
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
        self._fetch_moves(user_moves + self._move_ids(self.ctx["my"]))

    def ability_for(self, sid: str) -> str | None:
        """상성에 반영할 특성: 통계에서 50% 이상 채용된 특성, 없으면 특성이 하나뿐일 때 그 특성."""
        meta = self.ctx["meta"].get(sid)
        if meta and meta.get("abilities") and meta["abilities"][0]["pct"] >= 50:
            return meta["abilities"][0]["name"]
        abilities = (self.ctx["detail"].get(sid) or {}).get("abilities") or []
        return abilities[0]["name"] if len(abilities) == 1 else None

    def _matchup(self) -> None:
        for sid in dict.fromkeys(self.ctx["my"] + self.ctx["opp"]):
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

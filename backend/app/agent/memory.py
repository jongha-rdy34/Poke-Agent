"""F3010 대화 맥락(Memory).

보관: 조건(팀·포맷·선출 수·꼭 잡을 상대·선호/제외·내 기술·일정), 직전 결과 요약, 최근 대화 몇 턴
보관하지 않음: Tool 원본 응답, LLM 중간 추론
사용자가 조건을 바꾸면 바뀐 조건만 갱신한다.
"""
from __future__ import annotations

import copy
import threading

CONDITION_KEYS = ("my_team", "opp_team", "format", "pick_count", "month", "must_answer", "prefer",
                  "exclude", "my_moves", "focus_my", "focus_opp", "lookup_target", "lookup_kind", "schedule", "game", "battle",
                  "my_mega", "opp_mega")
MAX_HISTORY = 6

_store: dict[str, dict] = {}
_lock = threading.Lock()


def get(session_id: str) -> dict:
    with _lock:
        mem = _store.setdefault(session_id, {"conditions": {}, "last": None, "history": [], "aliases": {}})
        mem.setdefault("aliases", {})
        return copy.deepcopy(mem)


def add_aliases(session_id: str, choices: dict[str, str]) -> None:
    """사용자가 후보 중에서 고른 이름을 이 세션에 기억한다: {'물라오슨': 'Urshifu-Rapid-Strike'}"""
    with _lock:
        mem = _store.setdefault(session_id, {"conditions": {}, "last": None, "history": [], "aliases": {}})
        mem.setdefault("aliases", {}).update({k: v for k, v in choices.items() if k and v})


def merge_conditions(old: dict, new: dict, reset: bool = False) -> tuple[dict, list[str]]:
    """새로 언급된(None이 아닌) 조건만 덮어쓴다. 바뀐 키 목록을 함께 돌려준다."""
    merged = {} if reset else dict(old)
    changed = []
    for k in CONDITION_KEYS:
        v = new.get(k)
        if v is None or v == []:  # 언급하지 않은 조건은 그대로 둔다
            continue
        if merged.get(k) != v:
            changed.append(k)
        merged[k] = v
    return merged, changed


def save(session_id: str, conditions: dict, last: dict | None, user_msg: str, answer: str) -> None:
    with _lock:
        mem = _store.setdefault(session_id, {"conditions": {}, "last": None, "history": [], "aliases": {}})
        mem["conditions"] = conditions
        if last is not None:
            mem["last"] = last
        mem["history"] = (mem["history"] + [{"role": "user", "content": user_msg},
                                            {"role": "assistant", "content": answer[:600]}])[-MAX_HISTORY:]


def clear(session_id: str) -> None:
    with _lock:
        _store.pop(session_id, None)

"""F3001 Tool 명세 정의 및 등록.

Agent에는 이름·설명·입력 스키마만 넘긴다(list_specs). 실행은 call_tool이 담당한다.
설명(description)에는 '무엇을 하는지'보다 '언제 쓰는지'를 적는다 — 모델은 설명만 보고 Tool을 고른다.

새 Tool 추가 방법:
  1) app/tools/<source>.py에 @safe_tool 함수 작성 (ok/fail 형식으로만 반환)
  2) 아래에 입력 모델(Pydantic) 정의
  3) TOOLS에 ToolSpec 한 줄 등록
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from app.tools.common import ErrorCode, fail
from app.tools.names import resolve_names
from app.tools.pokeapi import get_pokemon_detail
from app.tools.showdown import get_move_info, get_type_matchup
from app.tools.smogon import get_meta_usage


# ---------------------------------------------------------------------------
# 입력 스키마
# ---------------------------------------------------------------------------
class MetaUsageInput(BaseModel):
    pokemon: list[str] = Field(default_factory=list, max_length=24,
                               description="예상 세트를 볼 포켓몬 이름(영문/한국어). 비우면 사용률 상위 후보 목록을 돌려준다.")
    format: str | None = Field(None, description="Smogon 포맷 ID. 예: gen9championsbssregmc(싱글 6→3), gen9ou. 생략 시 기본 포맷.")
    month: str | None = Field(None, description="집계월 YYYY-MM. 생략 시 가장 최근 집계월.")
    rating: int | None = Field(None, description="레이팅 컷. 생략 시 1500.")
    top_n: int = Field(6, ge=1, le=30, description="기술/도구 등을 상위 몇 개까지 돌려줄지")


class PokemonDetailInput(BaseModel):
    name: str = Field(..., min_length=1, description="포켓몬 이름. 영문(Garchomp-Mega-Z), PokeAPI 이름, 한국어(한카리아스) 모두 가능")


class MoveInfoInput(BaseModel):
    moves: list[str] = Field(..., min_length=1, max_length=24,
                             description="기술 이름 목록. Smogon 기술 ID(earthquake), 영문, 한국어(지진) 모두 가능")


class TypeMatchupInput(BaseModel):
    defend_types: list[str] = Field(..., min_length=1, max_length=2, description="방어 측 타입 1~2개 (영문/한국어)")
    attack_types: list[str] | None = Field(None, description="공격 기술 타입 목록. 생략 시 18타입 전체 약점표")
    defender_ability: str | None = Field(None, description="방어 측 특성(부유, 저수 등 면역 특성 반영). 영문 이름")


class ResolveNamesInput(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=24, description="변환할 이름 목록")
    kind: Literal["pokemon", "move"] = Field("pokemon", description="포켓몬 이름인지 기술 이름인지")


# ---------------------------------------------------------------------------
# 등록
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    func: Callable[..., dict]
    kind: Literal["external", "local"]
    source: str
    requirement: str

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "kind": self.kind,
            "source": self.source,
            "requirement": self.requirement,
        }


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="get_meta_usage",
            description=(
                "상대 포켓몬이 실전에서 주로 쓰는 기술·도구·특성·능력치 배분과 그 비율이 필요할 때 사용한다. "
                "상대 6마리의 예상 세트를 볼 때, 또는 pokemon을 비워 현재 메타의 사용률 상위 후보 목록이 필요할 때 쓴다. "
                "포맷·집계월·레이팅을 지정하지 않으면 기본값으로 조회하고 notes에 밝힌다."
            ),
            input_model=MetaUsageInput, func=get_meta_usage,
            kind="external", source="Smogon usage stats", requirement="F3002",
        ),
        ToolSpec(
            name="get_pokemon_detail",
            description=(
                "포켓몬 한 마리의 타입·종족값(특히 스피드)·특성이 필요할 때 사용한다. "
                "선공 여부나 타입 상성을 판단하기 전에 내 포켓몬과 상대 포켓몬 각각에 대해 호출한다. "
                "judgeable=false면 그 포켓몬은 판단하지 말고 제외 사유를 밝힌다."
            ),
            input_model=PokemonDetailInput, func=get_pokemon_detail,
            kind="external", source="PokeAPI", requirement="F3003",
        ),
        ToolSpec(
            name="get_move_info",
            description=(
                "기술의 타입·물리/특수 분류·위력·명중률·우선도가 필요할 때 사용한다. "
                "어떤 기술을 쓸지 비교하거나, 상대의 선공기(우선도>0)를 확인할 때 쓴다."
            ),
            input_model=MoveInfoInput, func=get_move_info,
            kind="external", source="Pokémon Showdown data", requirement="F3004",
        ),
        ToolSpec(
            name="get_type_matchup",
            description=(
                "공격 타입이 방어 포켓몬에게 몇 배로 들어가는지(0/0.25/0.5/1/2/4배) 계산이 필요할 때 사용한다. "
                "attack_types를 비우면 방어 측의 전체 약점·내성표를 돌려준다. 부유 같은 면역 특성은 defender_ability로 넘긴다."
            ),
            input_model=TypeMatchupInput, func=get_type_matchup,
            kind="external", source="Pokémon Showdown data", requirement="F3004",
        ),
        ToolSpec(
            name="resolve_names",
            description=(
                "사용자가 한국어(예: 한카리아스, 지진)나 애매한 이름으로 말했을 때, 다른 Tool에 넘길 영문 식별자로 바꿀 때 사용한다. "
                "AMBIGUOUS가 나오면 Tool을 더 부르지 말고 사용자에게 정확한 이름을 되묻는다."
            ),
            input_model=ResolveNamesInput, func=resolve_names,
            kind="local", source="로컬 매핑 파일 (PokeAPI 수집본)", requirement="F3001",
        ),
    ]
}


def list_specs(kind: str | None = None) -> list[dict]:
    return [t.schema() for t in TOOLS.values() if kind is None or t.kind == kind]


def call_tool(name: str, args: dict[str, Any] | None = None) -> dict:
    """이름으로 Tool을 찾아 입력을 검증한 뒤 실행한다. 어떤 경우에도 예외를 던지지 않는다."""
    spec = TOOLS.get(name)
    if spec is None:
        return fail(ErrorCode.UNKNOWN_TOOL, f"등록되지 않은 Tool입니다: {name}")
    try:
        parsed = spec.input_model.model_validate(args or {})
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        return fail(ErrorCode.BAD_ARGUMENT, f"입력 형식 오류 ({loc}): {first.get('msg')}")
    return spec.func(**parsed.model_dump())

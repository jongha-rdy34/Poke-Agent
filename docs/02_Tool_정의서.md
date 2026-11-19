# Tool 정의서 (F3001 ~ F3004)

## 0. 공통 규칙

### 응답 형식
```json
{ "ok": true,  "data": { ... } }
{ "ok": false, "error": { "code": "NOT_FOUND", "message": "사용자에게 보여줄 수 있는 문장" } }
```
- Tool은 **예외를 밖으로 던지지 않는다.** (`common.safe_tool` + `registry.call_tool`)
- 여러 항목을 한 번에 조회하는 Tool은 **일부만 실패해도 ok=true**로 돌려주고, 실패 항목에 `found:false, reason`을 붙인다.
  전부 실패하면 ok=false.
- 기본값을 쓰거나 기준을 바꿔 조회했으면 `notes`에 사람이 읽을 수 있게 남긴다.

### 실패 코드

| 코드 | 언제 | Agent의 처리 (2일차) |
|---|---|---|
| `BAD_ARGUMENT` | 입력이 규격과 다름 (빈 값, 형식 틀림, 범위 밖) | 인자를 고쳐 1회 재시도 또는 되묻기 |
| `NOT_FOUND` | 없는 포켓몬/기술/타입/포맷 | 해당 항목 제외 + 사유 표시 |
| `NO_DATA` | 조회는 됐지만 판단에 필요한 값이 비어 있음 | 판단하지 않고 제외 사유 표시 |
| `AMBIGUOUS` | 이름이 여러 대상에 걸림 | **Tool 더 부르지 않고 사용자에게 되묻기** |
| `INVALID_KEY` | 키 없음/틀림 (현재 Tool은 키 불필요 — LLM 키용) | 재계획하지 않고 설정 안내 |
| `UPSTREAM_ERROR` | 외부 서버 연결 실패, 5xx, 응답 형식 이상 | 재계획하지 않고 안내 (같은 결과 반복) |
| `TIMEOUT` | 외부 응답 시간 초과 | 1회 재시도 후 안내 |
| `UNKNOWN_TOOL` | 등록되지 않은 Tool 이름 | 계획 오류 → 로그에 이상 징후 |
| `INTERNAL_ERROR` | 예상하지 못한 내부 오류 | 안내 + 로그 |

### Tool 목록

| Tool | 구분 | 출처 | 요구사항 | 파일 |
|---|---|---|---|---|
| `get_meta_usage` | 외부 API | Smogon usage stats | F3002 후보 조회 | `backend/app/tools/smogon.py` |
| `get_pokemon_detail` | 외부 API | PokeAPI | F3003 상세정보 | `backend/app/tools/pokeapi.py` |
| `get_move_info` | 외부 API | Pokémon Showdown data | F3004 보조 정보 | `backend/app/tools/showdown.py` |
| `get_type_matchup` | 외부 API | Pokémon Showdown data | F3004 보조 정보 | `backend/app/tools/showdown.py` |
| `resolve_names` | 로컬 서버 | 로컬 매핑 (PokeAPI 수집본) | F3024★ | `backend/app/tools/names.py` |

### Tool 간 데이터 흐름 (앞선 Tool 출력 → 다음 Tool 입력)
```
사용자 입력(한국어 이름)
   └─ resolve_names ──► en / showdown_id / pokeapi_name
          ├─ get_meta_usage(pokemon=[en…]) ──► moves[].name (= Showdown 기술 ID)
          │        └─ get_move_info(moves=[…]) ──► type, category, priority
          │                 └─ get_type_matchup(attack_types=[type…])
          └─ get_pokemon_detail(name=pokeapi_name) ──► types, base_stats.spe, abilities
                   └─ get_type_matchup(defend_types=types, defender_ability=abilities[0])
```

---

## 1. `get_meta_usage` — 후보 조회 (F3002)

**설명(Agent용)**: 상대 포켓몬이 실전에서 주로 쓰는 기술·도구·특성·능력치 배분과 그 비율이 필요할 때 사용한다.
상대 6마리의 예상 세트를 볼 때, 또는 pokemon을 비워 현재 메타의 사용률 상위 후보 목록이 필요할 때 쓴다.

### 입력
| 파라미터 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `pokemon` | string[] (≤12) | X | `[]` | 영문/한국어 이름. 비우면 사용률 순위 모드 |
| `format` | string | X | `gen9championsbssregmc` | Smogon 포맷 ID (싱글 6→3 선출) |
| `month` | `YYYY-MM` | X | 최근 집계월 | 없는 월(미래 등) → 최근 월로 추정 |
| `rating` | int | X | 1500 | 없는 컷이면 가장 가까운 컷 |
| `top_n` | int 1~30 | X | 6 | 기술/도구 상위 몇 개 |

### 출력 (`mode = "sets"`)
```json
{
  "format": "gen9championsbssregmc", "month": "2026-10", "rating": 1500, "battles": 87848,
  "requested": {"format": null, "month": "2026-12", "rating": null},
  "estimated": true,
  "notes": ["2026-12 통계는 아직 없어 가장 최근 집계월 2026-10 기준으로 추정했습니다."],
  "mode": "sets",
  "pokemon": [{
    "input": "Garchomp", "found": true, "name": "Garchomp", "showdown_id": "garchomp", "usage_pct": 27.41,
    "moves": [{"name": "earthquake", "pct": 98.8}, ...],
    "items": [{"name": "choicescarf", "pct": 34.2}, ...],
    "abilities": [...], "tera_types": [],
    "spreads": [{"nature": "Jolly", "points": {"hp":2,"atk":32,"def":0,"spa":0,"spd":0,"spe":32}, "pct": 24.0}],
    "teammates": [...], "checks_counters": [{"name": "...", "battles": 712, "score": 43.1}]
  }, {"input": "Pikachu", "found": false, "reason": "NOT_IN_FORMAT", "message": "..."}]
}
```
`mode = "ranking"`이면 `ranking: [{rank, name, showdown_id, usage_pct}]`.

### 실패
| 상황 | 코드 |
|---|---|
| `month` 형식 오류, `top_n` 범위 밖, 13마리 이상 | `BAD_ARGUMENT` |
| 최근 6개월에 해당 포맷 없음 | `NOT_FOUND` (비슷한 포맷 목록 포함) |
| 요청한 포켓몬을 하나도 못 찾음 | `NO_DATA` |
| Smogon 연결 실패 | `UPSTREAM_ERROR` / `TIMEOUT` |

### 주의
- 통계는 **과거 한 달 집계**다. 오늘/미래 기준은 항상 추정 → `estimated`, `notes`.
- Champions 포맷의 `spreads.points`는 노력치(0~252)가 아니라 **SP(0~32)** 다. 테라스탈이 없어 `tera_types`는 빈 배열.

---

## 2. `get_pokemon_detail` — 상세정보 조회 (F3003)

**설명(Agent용)**: 포켓몬 한 마리의 타입·종족값(특히 스피드)·특성이 필요할 때 사용한다.
선공 여부나 타입 상성을 판단하기 전에 내 포켓몬과 상대 포켓몬 각각에 대해 호출한다.
judgeable=false면 그 포켓몬은 판단하지 말고 제외 사유를 밝힌다.

### 입력
| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `name` | string | O | `Garchomp-Mega-Z`, `garchomp-mega-z`, `한카리아스`, `메가한카리아스` 모두 가능 |

### 출력
```json
{
  "name": "mimikyu-disguised", "ko_name": "따라큐", "types": ["Ghost", "Fairy"],
  "base_stats": {"hp": 55, "atk": 90, "def": 80, "spa": 50, "spd": 105, "spe": 96},
  "abilities": [{"name": "disguise", "hidden": false}],
  "judgeable": true, "missing": [],
  "notes": ["'mimikyu' 폼이 따로 없어 기본 폼 'mimikyu-disguised'으로 조회했습니다."],
  "source": "PokeAPI"
}
```
- 판단 필수값: `types`, `base_stats.spe`. 하나라도 없으면 `judgeable=false`, `missing=[...]`, 값은 `null` 그대로.

### 실패
| 상황 | 코드 |
|---|---|
| 빈 이름 | `BAD_ARGUMENT` |
| PokeAPI에 없는 포켓몬/폼, 한국어 매핑 없음 | `NOT_FOUND` |
| 한국어 이름이 여러 종에 해당 | `AMBIGUOUS` |
| 연결 실패 | `UPSTREAM_ERROR` / `TIMEOUT` |

---

## 3. `get_move_info` — 기술 정보 (F3004)

**설명(Agent용)**: 기술의 타입·물리/특수 분류·위력·명중률·우선도가 필요할 때 사용한다.

### 입력
| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `moves` | string[] 1~24 | O | Smogon 기술 ID(`suckerpunch`), 영문(`Sucker Punch`), 한국어(`기습`) |

### 출력
```json
{"moves": [
  {"input": "기습", "found": true, "id": "suckerpunch", "name": "Sucker Punch", "ko_name": "기습",
   "type": "Dark", "category": "Physical", "base_power": 70, "accuracy": 100, "always_hits": false,
   "priority": 1, "contact": true},
  {"input": "notamove", "found": false, "reason": "NOT_FOUND"}
], "source": "Pokémon Showdown data (moves.json)"}
```
- **결과 없음 형식**: 항목별 `found:false, reason:"NOT_FOUND"`. 전부 없으면 `ok:false, NOT_FOUND`.

---

## 4. `get_type_matchup` — 타입 상성 (F3004)

**설명(Agent용)**: 공격 타입이 방어 포켓몬에게 몇 배로 들어가는지 계산이 필요할 때 사용한다.
attack_types를 비우면 방어 측의 전체 약점·내성표를 돌려준다. 부유 같은 면역 특성은 defender_ability로 넘긴다.

### 입력
| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `defend_types` | string[] 1~2 | O | 영문/한국어 타입 |
| `attack_types` | string[] | X | 생략 시 18타입 전체 (**권장: 방어 측별 1회 호출 후 코드에서 조회**) |
| `defender_ability` | string | X | `Levitate`, `Water Absorb` 등 상성을 바꾸는 특성 |

### 출력
```json
{"defend_types": ["Dragon"], "defender_ability": "Levitate",
 "matchups": [{"attack_type": "Ground", "multiplier": 0.0}, {"attack_type": "Ice", "multiplier": 2.0}],
 "weak_to": ["Ice"], "resists": [], "immune_to": ["Ground"],
 "notes": ["특성 Levitate: Ground 배율 ×0"], "source": "Pokémon Showdown data (typechart.js)"}
```

### 실패
| 상황 | 코드 |
|---|---|
| 방어 타입 0개 또는 3개 이상 | `BAD_ARGUMENT` |
| 알 수 없는 타입 | `NOT_FOUND` |
| 상성표 형식 변경으로 파싱 실패 | `UPSTREAM_ERROR` |

---

## 5. `resolve_names` — 이름 변환 (로컬 서버 API 툴)

**설명(Agent용)**: 사용자가 한국어(예: 한카리아스, 지진)나 애매한 이름으로 말했을 때 다른 Tool에 넘길 영문 식별자로 바꿀 때 사용한다.
AMBIGUOUS가 나오면 Tool을 더 부르지 말고 사용자에게 정확한 이름을 되묻는다.

| 입력 | 타입 | 설명 |
|---|---|---|
| `names` | string[] 1~24 | 이름 목록 |
| `kind` | `"pokemon"` \| `"move"` | 기본 pokemon |

출력: `results: [{input, found, en, ko, showdown_id, pokeapi_name}]`, 실패 항목은 `found:false, reason: NOT_FOUND | AMBIGUOUS`.
데이터: `backend/app/data/name_map.json` (포켓몬 1025종, **폼 337개**, 기술 937개 — `scripts/build_name_map.py`로 재생성,
폼만 추가할 때는 `--forms-only`).

**폼 이름 처리**
- PokeAPI `pokemon-form`의 한국어 폼 이름으로 색인: `워시로토무`→`Rotom-Wash`, `스핀로토무`→`Rotom-Fan`
- 폼 이름이 설명형이면 실제로 부르는 이름을 만들어 색인: `연격의 태세`+`우라오스`→`연격우라오스`, `우물의가면`+`오거폰`→`우물오거폰`
- 접두 표기(`메가`, `알로라`, `가라르`, `히스이`, `팔데아`)는 종 이름 + 폼 접미어로 변환
- 모르는 폼 이름이지만 종 이름이 들어 있으면(`이상한로토무`) **AMBIGUOUS + 그 종의 폼 후보** → Agent가 되묻는다
- Smogon은 폼 표기가 달라서(`Ogerpon-Wellspring`, `Indeedee-F`) `get_meta_usage`가 앞부분 일치로 보정하고 notes에 남긴다.
  다른 폼을 기본 종으로 붙이지는 않는다 (`Rotom-Heat`가 통계에 없으면 `Rotom`으로 대체하지 않고 NOT_IN_FORMAT).

---

## 6. 등록 및 확장 방법
1. `backend/app/tools/<출처>.py`에 함수 작성 → `@safe_tool`을 붙이고 `ok(...)` / `raise ToolError(code, msg)`만 사용
2. `registry.py`에 Pydantic 입력 모델 정의 (각 필드 `description` 필수 — 모델이 읽는다)
3. `TOOLS`에 `ToolSpec(name, description, input_model, func, kind, source, requirement)` 추가
4. `python run_tools.py`로 정상 1회 + 실패 2가지 확인, `pytest`에 오프라인 테스트 추가

Agent에는 `list_specs()`(이름·설명·입력 스키마)만 넘기고, 실행은 `call_tool(name, args)` 하나로 한다.
2일차에 LangChain `StructuredTool`로 감쌀 때도 `input_model`을 `args_schema`로 그대로 쓴다.

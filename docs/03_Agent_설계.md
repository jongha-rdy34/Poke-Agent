# Agent 설계 (2.4 실행 흐름 · 2.5 실행 기록과 저장 구조)

> 1일차 기획 단계 설계안. 2일차 구현 시 이 문서를 기준으로 하고, 바뀐 점은 "변경 기록"에 남긴다.

## 1. 메인 Agent 실행 흐름 (LangGraph)

```
 [analyze] ─► [plan] ─► [execute] ─► [judge] ─┬─► [respond]
     │          ▲                              │
     │          └────── 재계획 (상한 3회) ◄────┘  (조건 미충족 & 재계획 가능)
     └─► [respond]  (Tool 불필요 / 필수 정보 누락 → 되묻기)
```

| 단계 | 담당 | 입력 | 출력(상태에 추가) |
|---|---|---|---|
| analyze | **LLM** | 사용자 메시지 + 메모리 | `intent`, `intent_reason`, `conditions{format, pick, my_team, opp_team, must_answer, prefer, my_moves, schedule}`, `missing[]` |
| plan | 규칙 (+LLM: 기타 의도) | intent, conditions | `steps[{tool, args, depends_on}]` |
| execute | 코드 | steps | `tool_results{tool: …}`, `failures[]` |
| judge | **코드** (계산) | tool_results, conditions | `matrix`, `selection`, `satisfied`, `uncovered[]` |
| respond | **LLM** (문장화만) | 판단 결과 + 근거 | `answer`, `plan_draft` |

**LLM은 분석·문장화에만** 쓰고, 배율·스피드·조합 계산은 코드가 한다. (슬라이드: "계산은 모델이 아니라 코드가")

### 1.1 의도 분류
`plan_selection`(선출 계획) / `matchup_advice`(대면·기술) / `lookup`(사실 조회) / `save_plan`(계획 확정) / `chitchat`(기타)

### 1.2 의도별 계획

| 의도 | Tool 순서 |
|---|---|
| plan_selection | resolve_names → get_meta_usage(상대) → get_pokemon_detail(12) → get_move_info(상대 주력기) → get_type_matchup(방어 측별 1회, attack_types 생략) |
| matchup_advice | resolve_names → get_pokemon_detail(2) → get_meta_usage(양쪽) → get_move_info → get_type_matchup(2) |
| lookup | resolve_names → 필요한 Tool 하나 |
| save_plan / chitchat | Tool 없음 (save_plan은 서버 저장 로직이 직접 처리 — 이미 정한 값을 다루므로 Tool로 두지 않음) |

**되묻기(Tool 호출 전)**: `my_team` 또는 `opp_team`이 비었거나 6마리 미만 / `resolve_names`가 AMBIGUOUS.

## 2. 제약 판단 계산식 (judge, 코드)

| 값 | 계산 | 출처 Tool |
|---|---|---|
| 공격 배율 `off(m,o)` | 내 m의 공격기 타입(기술 미입력 시 **자속 타입**으로 가정) 중 o에 대한 최대 배율 | detail(m).types, matchup(o) |
| 피격 위험 `risk(m,o)` | o의 주력 공격기(사용률 ≥ 20%, Status 제외) 타입 중 m에 대한 최대 배율 | meta(o).moves, move_info, matchup(m) |
| 선공 `faster(m,o)` | 스피드 실수치 범위 비교: 내 값(입력 또는 범위) vs 상대 [무투자, 최대투자] 범위. 상대 스카프 비율 ≥ 10%면 ×1.5 범위도 표시 | detail.spe, meta(o).items/spreads |
| `answers(m,o)` | `off ≥ 2` **그리고** (`risk < 2` 또는 `faster`) | 위 값 |
| 선출 충족 | 3마리 조합 중 `must_answer`(없으면 상대 6마리) 전부를 1마리 이상이 answers | — |

- **확인할 수 없는 값은 지어내지 않는다**: 상대의 실제 배분/도구는 모름 → "상대가 최대 투자 + 스카프가 아니라면 선공", "상대 스피드 SP가 X 이상이면 역전"처럼 **역산 기준**으로 제시.
- 스피드 실수치 공식은 **포맷별로 분리**(F3025★): 일반 포맷 = 노력치(0~252)·Lv50 공식, Champions 포맷 = SP(0~32) 체계.
  → SP 체계의 정확한 실수치 공식은 2일차에 확인 후 확정 (확인 전에는 **종족값 비교 + "배분에 따라 역전 가능"** 으로만 표시).

## 3. 재계획 (F3009)

| 회차 | 조건 | 바꾸는 기준 | 재사용 |
|---|---|---|---|
| 1 | 선출 미충족 | 내 포켓몬 공격기를 자속 → **메타 통계 상위 공격기(내 포켓몬 기준)** 로 확장 | detail·matchup 결과 전부 |
| 2 | 여전히 미충족 | 대상 상대를 6마리 → **상대 선출 확률 상위 3~4마리**(사용률·팀메이트 통계)로 좁힘 | 위 + meta |
| 3 | 여전히 미충족 | `answers` 기준을 "2배 & 안전" → "**선공 + 등배 이상**"으로 완화 | 전부 |
| 상한 도달 | — | 멈추고 "꼭 잡을 상대를 줄이거나 선호 조건을 풀어도 될까요?" 되묻기 | — |

**재계획하지 않는 경우**: `UPSTREAM_ERROR`, `TIMEOUT`(1회 재시도 후), `INVALID_KEY` — 다시 해도 결과가 같으므로 바로 안내.

## 4. 대화 맥락(Memory) (F3010)

| 보관 | 보관하지 않음 |
|---|---|
| 포맷, 선출 수, 내 팀, 상대 팀(정규화된 이름), 꼭 잡을 상대, 선호 조건 | Tool 원본 응답 (Smogon 파일 등 대용량) |
| 직전 선출 결과 요약(3마리, 선봉, 근거 한 줄), 사용한 통계 월 | LLM 중간 추론 |

"보만다 빼고 다시" → `prefer/exclude`만 갱신, 팀·포맷 유지 → judge부터 재실행(캐시 재사용).

## 5. 실행 이벤트 (F3012) — 메인 Agent는 발행만, 구독자를 모름

```json
{"session_id": "s-…", "run_id": "r-…", "seq": 7, "ts": "2026-11-19T10:00:01.123+09:00",
 "type": "tool_call_end", "step": "execute",
 "payload": {"tool": "get_meta_usage", "args": {...}, "ok": true, "error_code": null, "duration_ms": 412}}
```

| type | 언제 | payload |
|---|---|---|
| `step_start` / `step_end` | 각 노드 진입/종료 | step, (end) 요약·duration_ms |
| `llm_decision` | 의도 분류, 되묻기 결정, 재시도 판단 | decision, reason |
| `tool_call_start` / `tool_call_end` | Tool 요청/응답 | tool, args, ok, error_code, duration_ms |
| `replan` | 재계획 발생 | round, reason, changed_criteria |
| `run_end` | 응답 완료 | status(ok/asked/limit/error), total_ms |

이벤트 버스: 프로세스 내 pub/sub. 구독자 예외는 버스가 삼키고 메인 흐름에 전파하지 않음 → **로깅 Agent를 떼어도 메인 Agent 정상 동작** (분리 판단 기준).

## 6. 저장 구조 (SQLite)

```sql
-- 실행 로그 (F3013, F3014) — INSERT/SELECT/DELETE만, UPDATE 없음
CREATE TABLE run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, run_id TEXT NOT NULL, seq INTEGER NOT NULL,
  ts TEXT NOT NULL, type TEXT NOT NULL, step TEXT, payload TEXT NOT NULL  -- JSON
);
-- 선출 계획 (F3015, F3016)
CREATE TABLE plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, battle_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
  format TEXT NOT NULL, my_team TEXT NOT NULL, opponent_team TEXT NOT NULL,   -- JSON 배열
  selected TEXT NOT NULL, lead TEXT, move_plan TEXT, basis TEXT,               -- JSON
  session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```
겹침 판정: `battle_date = ? AND start_time < :end AND end_time > :start AND id != :self`

## 7. 백엔드 API (F3017)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/health` | — | `{status, tools}` ✅ |
| GET | `/api/tools` | `?kind=external|local` | Tool 명세 목록 ✅ |
| POST | `/api/tools/{name}` | `{args}` | Tool 응답 (개발·검증용) ✅ |
| POST | `/api/chat` | `{session_id?, message}` | `{session_id, answer, steps[], tool_calls[], replans, plan_draft?}` |
| GET | `/api/plans` | `?date=` | 계획 목록 |
| POST | `/api/plans` | 계획 본문 | `{plan, overlaps[]}` |
| GET/PUT/DELETE | `/api/plans/{id}` | — / 수정 본문 / — | 계획 / `{plan, overlaps[]}` / 204 |
| GET | `/api/logs` | — | 세션별 요약 목록 |
| GET | `/api/logs/{session_id}` | — | 이벤트 목록(seq 순) |
| DELETE | `/api/logs/{session_id}` | — | 204 (수정 API 없음) |

## 8. 화면 (F3018, F3019)
- **대화 화면**: 팀 입력 칸(내 6 / 상대 6, 한국어 자동 변환) + 채팅. 답변 아래에 실행 단계 타임라인
  (단계 → Tool 호출 → 소요 시간 → 실패 코드), 재계획 지점은 색으로 구분, "통계 2026-10 · 1500 컷" 근거 배지.
- **계획 관리 화면**: 날짜별 목록, 상세(선출 3마리·대면별 기술), 일정/선출 수정·삭제, 겹침 경고.

---

## 9. 2일차 구현 결과와 변경 기록

| 항목 | 설계 | 구현 / 변경 | 이유 |
|---|---|---|---|
| Tool 순서 결정 | 규칙 기반 계획 | 그대로. `check_llm.py`에서 LLM이 한국어 이름이면 `resolve_names`부터 고르는 것을 확인 → 순서는 LLM에 맡기지 않고 planner가 고정 | 같은 요청에 같은 계획 → 재현성·로그 비교가 쉬움 |
| 상성 호출 | 방어 측별 1회 | 그대로 (`attack_types` 생략, `defender_ability` 반영) | 설계 시험 230회 → 대표 요청 약 30회 |
| 재계획 1회차 | 메타 상위 공격기로 확장 | 새 Tool은 `meta_my`, `moves_my`만 호출, detail·matchup은 재사용 | 같은 Tool·같은 입력은 executor 캐시로 다시 부르지 않음 (테스트로 확인) |
| 이벤트 순서 | seq 순서 저장 | 병렬 detail 호출 시 구독자가 순서 뒤바뀐 이벤트를 받는 문제 발견 → **발행을 Emitter 잠금 안에서** 수행 | 실행 로그는 발생 순서대로여야 함 |
| 응답 생성 | LLM | LLM 실패 시 **템플릿 응답**으로 대체하고 `error(fallback=template)` 이벤트 기록 | NF3003: LLM 장애에도 서비스 응답 |
| 키 누락 | 안내 | 분석 단계에서 `INVALID_KEY` → 재계획 없이 설정 안내, `/health.llm_key`로 화면 상단 배너 | F3019, NF3002(키 값은 노출 안 함) |
| 계획 저장 | 서버 로직 | 대화(`save_plan` 의도)와 화면 저장 버튼 둘 다 `storage.plans.create` 사용 | 이미 정한 값을 다루므로 Tool로 두지 않음 |

### 실제 데이터 실행 예 (가짜 LLM + 실제 Tool, `serve` 시험)
- 내 팀: 하마돈·마스카나·고릴타·대쓰여너·보만다·루카리오 / 상대: 누리레느·한카리아스·브리두라스·타부자고·따라큐·아머까오
- 0회차(자속 기준): 한카리아스·아머까오 미커버 → **재계획 1회**(실전 기술 확장) → 6마리 모두 커버
- 결과: 마스카나·보만다·루카리오 (선봉 마스카나), Tool 30회, 실패 0

### 수정: 폼 이름 인식 (워시로토무)
- 문제: "워시로토무"라고 하면 이름 변환에서 NOT_FOUND → "찾지 못했습니다"로 되물음. 이름 매핑을 **종 이름만** 수집했기 때문.
- 수정: PokeAPI 폼 이름 337개 추가 수집, 설명형 폼 이름 조합 색인, 모르는 폼은 후보 제시, Smogon 폼 표기 보정.
- 확인: 상대 팀에 워시로토무를 넣은 선출 계획이 정상 판단됨 (하이드로펌프·볼트체인지를 상대 주력기로 사용).

### 추가: 게임 버전 토글 (포켓몬 챔피언스 / 스칼렛·바이올렛)
| | 챔피언스 | 스칼렛·바이올렛 (본가) |
|---|---|---|
| 특수 룰 | 메가진화 (X/Y/Z 포함) | 테라스탈, 메가진화 없음 |
| 싱글 / 더블 포맷 | `gen9championsbssregmc` / `gen9championsvgc2026regmc` | `gen9bssregi` / `gen9vgc2026regi` |
| 통계 | 진행 중 시즌 최신 월 | 종료된 시즌 → 마지막 집계 월 (예: 2026-06), 통계 검색 범위를 24개월로 확대 |
| 능력치·스피드 판단 | SP(최대 32), 종족값 비교 | 노력치(최대 252), Lv50 실수치 범위 비교 |
- 화면 상단 토글로 고르고 `/api/chat`의 `game`, `battle`로 전달 → 세션 조건(`format`, `pick_count`, `game`)에 반영. 바꾸면 새 대화로 시작
- 이름: `메가한카리아스z`, `한카리아스 메가Z`처럼 X/Y/Z를 인식. PokeAPI 전체 포켓몬 목록(1,351개)으로 실제 존재하는 폼인지 확인하고,
  없는 폼(`메가고릴타`)이나 갈리는 폼(`메가리자몽` → X/Y)은 지어내지 않고 후보로 되묻는다
- 본가에서 메가 포켓몬을 말하면 `NOT_IN_GAME`으로 기본 폼을 후보로 되묻는다

### 추가: 상대 파티 물리/특수 비율과 운용 형태
- 규칙(`backend/app/domain/roles.py`, LLM 미사용): Smogon의 **모든 배분(Spreads)** 을 성격과 함께 분류해 가중 합산
  - 딜러: [고속 / 딜탱] × [물리 / 특수 / 혼합] — 공격·특공 실효 투자(투자량/최대치 ± 성격 0.25) ≥ 0.5면 딜러, 스피드 실효 ≥ 0.5면 고속
  - 탱커: 물리막이 / 특수막이 / 양막이 (방어·특방 실효 차이 0.3 기준), 공격 없이 스피드만 올리면 고속 서포터
  - 배분 표기 H·A·B·C·D·S (최대치의 40% 이상 투자한 능력), SP(최대 32)·노력치(최대 252) 자동 판별
- 파티 물리 비율 = 포켓몬별 물리 비중을 **딜러로 나올 확률**로 가중 평균 (탱커 위주는 평균에서 제외, 통계 없으면 판단 불가로 표시)
- 활용: 응답의 '상대 파티 공격 성향' 단락, 받아내기 좋은 내 포켓몬(HP×방어/특방 종족값), 선출 조합 동점 시 3순위 기준
- 실측 예 (2026-10, Champions BSS 1500): 한카리아스 물리 고속 딜러 66%·물리막이 22%, 메가한카리아스Z 특수 고속 딜러 85%,
  누리레느 특수 딜탱 56%, 아머까오 물리막이 72%

### 추가: 데미지 계산 · 실수치 스피드 · 메가 1마리 · 팀 직접 선택
**실수치** (`backend/app/domain/stats.py`, Lv50·개체값 31)
- 본가(노력치): 일반 공식
- 챔피언스(SP): Pokémon Showdown `data/mods/champions/scripts.ts`의 statModify와 동일 — HP = 종족값+SP+75, 그 외 = (종족값+SP+20)×성격.
  1 SP = 노력치 8과 같은 값 (예: 한카리아스 명랑 S32 = 169 = 명랑 S252)

**데미지** (`backend/app/domain/damage.py`): Showdown getDamage와 같은 순서·반올림. 랜덤 16단계, 자속, 상성, 더블 범위기 0.75, 연속기(트리플악셀 20/40/60, 2~5회 확률),
바디프레스(방어로 공격)·사이코쇼크(방어로 받음)·속임수(상대 공격) 반영. 급소·날씨·도구·특성·랭크 변화는 제외하고 응답에 밝힌다.
공식 계산기 `@smogon/calc` 0.10.0과 7개 사례에서 16단계 값이 일치 (`tests/test_damage.py`).
- 공격측(내 포켓몬): 그 기술의 공격 능력치에 투자한 실전 최다 배분, 없으면 '가정: 최대 투자'
- 방어측(상대): 실전 최다 배분 + 15% 이상인 내구형(막이) 배분이 있으면 그 배분으로도 계산
- 출력: 체력 대비 %, 확정/난수 1타(확률), 확정/난수 2타. 대면 계획의 기술은 이 데미지가 가장 큰 기술로 고른다

**스피드**: 내 스피드(실전 최다 배분) vs 상대 실전 배분 분포 → "상대 배분의 N%보다 빠름", 구애스카프 채용률·스카프 실수치 함께 표시

**메가진화 (챔피언스)**: 메가스톤은 여러 마리가 들 수 있고(예: 앱솔나이트Z·한카리아스나이트Z·보만다나이트), **배틀에서 메가진화는 1번**.
- 계획 단계 `mega`(이름 확인 바로 다음): 양쪽 팀 종마다 실제 메가 폼(PokeAPI 목록) 중 **이 포맷 통계에 나오는 폼만** 후보로 확인 (`get_meta_usage` 1회)
- 내 팀: "메가스톤을 든 포켓몬"을 묻는다 — 종마다 [스톤 없음 / 메가 폼들] 중 하나, **여러 종 동시에 가능** (`conditions.my_megas = {기본: 메가}`).
  메가 폼으로 직접 말한 포켓몬은 스톤 보유로 본다. 세션에 기억, 팀이 바뀌면 다시 묻는다
- 스톤을 든 포켓몬은 **기본 폼과 메가 폼을 둘 다** 조회·계산한다 (상세·실전 배분·기술·상성)
- 선출 판단: 3마리 조합 × {메가진화 안 함, 조합 안의 스톤 보유자 각각 1마리만 메가 폼} 을 모두 평가해 가장 좋은 (조합, 메가진화 대상)을 고른다.
  점수가 같으면 메가진화를 쓰는 쪽. 같은 3마리에서 메가진화 대상을 바꿨을 때의 커버 수를 `mega_alternatives`로 보여준다
- 상대 팀: 종별 메가 폼 비율 = 메가 폼 사용률 ÷ (기본 + 메가 폼 사용률). 가장 높은 종의 최다 메가 폼을 **예상**으로 쓰고(15% 이상), 화면에서 바꾸면 '지정'

**팀 직접 선택**: 화면 상단 `[대화로 입력 | 직접 선택]`. 직접 선택은 6+6 슬롯에 검색(`GET /api/chat/pokemon?q=&game=`, 비우면 현재 규칙의 사용률 상위)으로 채우고
`/api/chat`의 `my_team`, `opp_team`으로 보낸다 → 대화 분석보다 우선

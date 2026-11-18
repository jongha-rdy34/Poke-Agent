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

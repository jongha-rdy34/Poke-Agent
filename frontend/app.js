// Poke-Agent 프론트엔드 — 백엔드 API(/api/chat, /api/plans, /api/logs)를 호출해 화면을 그린다.
// FastAPI가 /app 으로 제공하면 같은 주소, 파일을 따로 열면 localhost:8000 을 호출한다.
const API = location.pathname.startsWith("/app") ? "" : "http://localhost:8000";
const $ = (s, el = document) => el.querySelector(s);

// ---------------------------------------------------------------- 공통
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 아주 작은 마크다운: **굵게**, `코드`, - 목록, 줄바꿈 (HTML은 먼저 이스케이프)
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
    const item = line.match(/^\s*[-*]\s+(.*)/);
    if (item) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${item[1]}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const h = line.match(/^#{1,4}\s+(.*)/);
    html += h ? `<p><b>${h[1]}</b></p>` : line.trim() ? `<p>${line}</p>` : "";
  }
  return html + (inList ? "</ul>" : "");
}

// 화면에는 API 키나 오류 원문을 보여주지 않는다 (NF3002)
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...opts });
  } catch {
    throw new Error("서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인하세요.");
  }
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map(x => x.msg).join(", ") : "요청을 처리하지 못했습니다.";
    throw new Error(msg);
  }
  return body;
}

function store(key, val) {
  try { val === undefined ? localStorage.removeItem(key) : localStorage.setItem(key, val); } catch { /* 저장 불가 환경 */ }
}
function load(key) { try { return localStorage.getItem(key); } catch { return null; } }

// ---------------------------------------------------------------- 탭
document.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("hidden", v.id !== "tab-" + btn.dataset.tab));
  if (btn.dataset.tab === "plans") loadPlans();
  if (btn.dataset.tab === "logs") loadLogSessions();
}));

// 서버 상태 확인: LLM 키가 없으면 설정 안내
(async () => {
  try {
    const h = await api("/health");
    if (!h.llm_key) showBanner("LLM API 키가 설정되지 않았습니다. backend/.env 에 GMS_API_KEY 를 넣고 서버를 다시 시작하세요.");
  } catch (e) { showBanner(e.message); }
})();
function showBanner(text) { const b = $("#banner"); b.textContent = text; b.classList.remove("hidden"); }

// ---------------------------------------------------------------- 게임 규칙 토글
// 챔피언스(메가진화) / 스칼렛·바이올렛(테라스탈) × 싱글 / 더블. 바꾸면 새 대화로 시작한다 (팀·통계가 달라지므로)
const RULE_NOTE = {
  champions: "메가진화 룰 · 능력치 SP(최대 32) · 진행 중인 시즌 통계",
  sv: "테라스탈 룰 · 메가진화 없음 · 노력치(최대 252) · 종료된 시즌의 마지막 집계 통계",
};
let rule = { game: load("poke.game") || "champions", battle: load("poke.battle") || "singles" };
function renderRule() {
  document.querySelectorAll("#seg-game button").forEach(b => b.setAttribute("aria-checked", String(b.dataset.game === rule.game)));
  document.querySelectorAll("#seg-battle button").forEach(b => b.setAttribute("aria-checked", String(b.dataset.battle === rule.battle)));
  $("#rule-note").textContent = RULE_NOTE[rule.game];
}
function setRule(key, value) {
  if (rule[key] === value) return;
  rule[key] = value; store("poke." + key, value); renderRule();
  if (key === "game") {
    try { picks = JSON.parse(load("poke.picks." + rule.game)) || { my: [], opp: [] }; } catch { picks = { my: [], opp: [] }; }
    if (rule.game === "sv") picks.my = picks.my.filter(p => !/-Mega/.test(p.value));  // 본가는 메가 없음
    renderPicker();
  }
  const hadChat = messages.querySelectorAll(".msg:not(.intro)").length > 0;
  sessionId = null; store("poke.session");
  if (hadChat) {
    [...messages.querySelectorAll(".msg:not(.intro)")].forEach(m => m.remove());
    addMsg("agent", `<span class="muted">규칙을 <b>${esc($(`[data-${key}="${value}"]`).textContent.trim())}</b>(으)로 바꿨어요. 새 대화로 시작합니다.</span>`);
  }
}
document.querySelectorAll("#seg-game button").forEach(b => b.addEventListener("click", () => setRule("game", b.dataset.game)));
document.querySelectorAll("#seg-battle button").forEach(b => b.addEventListener("click", () => setRule("battle", b.dataset.battle)));

// ---------------------------------------------------------------- 입력 방식: 대화 / 직접 선택
let mode = load("poke.mode") || "chat";
let picks = { my: [], opp: [] };
try { picks = JSON.parse(load("poke.picks." + rule.game)) || picks; } catch { /* 처음 */ }
let pickTarget = null;  // {side, index}

function savePicks() { store("poke.picks." + rule.game, JSON.stringify(picks)); }
function renderMode() {
  document.querySelectorAll("#seg-mode button").forEach(b => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
  $("#picker").classList.toggle("hidden", mode !== "pick");
  renderPicker();
}
function renderPicker() {
  for (const side of ["my", "opp"]) {
    const box = $(`.team[data-side="${side}"] .slots`);
    box.innerHTML = Array.from({ length: 6 }, (_, i) => {
      const p = picks[side][i];
      const active = pickTarget && pickTarget.side === side && pickTarget.index === i;
      return p ? `<button type="button" class="slot filled ${active ? "active" : ""}" data-side="${side}" data-i="${i}" title="${esc(p.value)}">
          ${p.sprite ? `<img src="${esc(p.sprite)}" alt="" loading="lazy">` : ""}<span>${esc(p.label)}</span>
          <span class="x" data-remove aria-label="빼기">×</span></button>`
        : `<button type="button" class="slot ${active ? "active" : ""}" data-side="${side}" data-i="${i}">+ 추가</button>`;
    }).join("");
    $(`.team[data-side="${side}"] [data-count]`).textContent = `${picks[side].length}/6`;
  }
  const megas = picks.my.filter(p => /-Mega/.test(p.value)).length;
  $("#pk-hint").textContent = rule.game === "champions" && megas > 1
    ? `메가 포켓몬 ${megas}마리 — 선출은 메가 1마리까지만 들어갑니다` : "";
  $("#pk-go").disabled = picks.my.length < 3 || picks.opp.length < 1;
}
async function pkSearch() {
  const q = $("#pk-q").value;
  const list = $("#pk-results");
  try {
    const rows = await api(`/api/chat/pokemon?q=${encodeURIComponent(q)}&game=${rule.game}&battle=${rule.battle}&limit=18`);
    const taken = new Set([...picks.my, ...picks.opp].filter(Boolean).map(p => p.value));
    list.innerHTML = rows.length ? rows.map(r => `<button type="button" class="pk-item" role="option" data-v="${esc(r.value)}"
        data-l="${esc(r.label)}" data-s="${esc(r.sprite || "")}" ${taken.has(r.value) && pickTarget?.side === "my" ? "disabled" : ""}>
        ${r.sprite ? `<img src="${esc(r.sprite)}" alt="" loading="lazy">` : ""}<span>${esc(r.label)}</span>
        ${r.usage_pct != null ? `<small>${r.usage_pct}%</small>` : ""}</button>`).join("")
      : `<p class="muted small">찾는 포켓몬이 없어요.</p>`;
  } catch (e) { list.innerHTML = `<p class="notice err">${esc(e.message)}</p>`; }
}
let pkTimer;
$("#pk-q").addEventListener("input", () => { clearTimeout(pkTimer); pkTimer = setTimeout(pkSearch, 200); });
$("#picker").addEventListener("click", ev => {
  const slot = ev.target.closest(".slot");
  if (slot) {
    const side = slot.dataset.side, i = +slot.dataset.i;
    if (ev.target.closest("[data-remove]")) {
      picks[side].splice(i, 1); savePicks(); pickTarget = null;
      $(".picker-search").classList.add("hidden"); renderPicker(); return;
    }
    pickTarget = { side, index: Math.min(i, picks[side].length) };
    $(".picker-search").classList.remove("hidden");
    $("#pk-q").value = ""; $("#pk-q").focus(); renderPicker(); pkSearch(); return;
  }
  const item = ev.target.closest(".pk-item");
  if (item && pickTarget) {
    picks[pickTarget.side][pickTarget.index] = { value: item.dataset.v, label: item.dataset.l, sprite: item.dataset.s };
    picks[pickTarget.side] = picks[pickTarget.side].filter(Boolean);
    savePicks();
    const next = picks[pickTarget.side].length;  // 다음 빈 칸으로
    pickTarget = next < 6 ? { side: pickTarget.side, index: next } : null;
    if (!pickTarget) $(".picker-search").classList.add("hidden");
    $("#pk-q").value = ""; renderPicker(); if (pickTarget) pkSearch();
  }
});
$("#pk-clear").addEventListener("click", () => { picks = { my: [], opp: [] }; savePicks(); pickTarget = null; renderPicker(); });
$("#pk-go").addEventListener("click", () => {
  const summary = `내 팀: ${picks.my.map(p => p.label).join(", ")} / 상대 팀: ${picks.opp.map(p => p.label).join(", ")}`;
  send("이 팀으로 선출 추천해줘", null, summary, { my_team: picks.my.map(p => p.value), opp_team: picks.opp.map(p => p.value) });
});
document.querySelectorAll("#seg-mode button").forEach(b => b.addEventListener("click", () => {
  mode = b.dataset.mode; store("poke.mode", mode); renderMode();
}));

// ---------------------------------------------------------------- 대화
let sessionId = load("poke.session");
const messages = $("#messages");

function addMsg(role, html, cls = "") {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="bubble ${cls}">${html}</div>`;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

const STEP_NAME = { analyze: "요청 분석", plan: "작업 계획", execute: "Tool 실행", judge: "제약 판단", respond: "응답 생성" };
const STATUS_BADGE = {
  ok: ["완료", "ok"], ask: ["확인 필요", "warn"], limit: ["재계획 상한 도달", "warn"], error: ["오류", "err"],
  infeasible: ["선출 조합 없음", "warn"],
};

function renderTrace(events) {
  const rows = [];
  for (const e of events) {
    const p = e.payload || {};
    if (e.type === "step_end") {
      rows.push(`<li class="step"><span>▸</span><span>${STEP_NAME[e.step] || e.step}</span><span class="ms">${p.duration_ms}ms</span></li>`);
    } else if (e.type === "tool_call_end") {
      const ok = p.ok;
      rows.push(`<li class="tool ${ok ? "" : "fail"}"><span>${ok ? "✓" : "✗"}</span><span>${esc(p.tool)}${ok ? "" : ` — ${esc(p.error_code)}`}</span><span class="ms">${p.duration_ms}ms</span></li>`);
    } else if (e.type === "replan") {
      rows.push(`<li class="replan"><span>↻</span><span>재계획 ${p.round}회차 — ${esc(p.reason)}<br><small>${esc(p.criteria)}</small></span><span></span></li>`);
    } else if (e.type === "llm_decision") {
      rows.push(`<li class="decision"><span>◆</span><span>의도: ${esc(p.intent)} — ${esc(p.reason)}</span><span></span></li>`);
    } else if (e.type === "decision") {
      rows.push(`<li class="decision"><span>◆</span><span>${esc(p.decision)}${p.reason ? " — " + esc(p.reason) : ""}${p.missing ? " (" + esc(p.missing.join(", ")) + ")" : ""}</span><span></span></li>`);
    } else if (e.type === "error") {
      rows.push(`<li class="tool fail"><span>!</span><span>${esc(p.code)}${p.fallback ? " → 템플릿 응답으로 대체" : ""}</span><span></span></li>`);
    }
  }
  // 단계 종료 이벤트가 그 단계의 Tool 호출 뒤에 오므로, 단계 행을 Tool 행 앞으로 옮긴다
  return `<ol class="timeline">${reorder(rows).join("")}</ol>`;
}
function reorder(rows) {
  const out = []; let pending = [];
  for (const r of rows) {
    if (r.startsWith('<li class="step"')) { out.push(r, ...pending); pending = []; }
    else pending.push(r);
  }
  return out.concat(pending);
}

function draftCard(draft, msgEl) {
  const wrap = document.createElement("div");
  wrap.className = "draft";
  wrap.innerHTML = `<h4>이 선출 계획 저장하기</h4>
    <div class="picks">${draft.selected.map(s => `<span class="pick ${s === draft.lead ? "lead" : ""}">${esc(s)}</span>`).join("")}</div>`;
  const form = $("#tpl-plan-form").content.firstElementChild.cloneNode(true);
  form.title.value = `${draft.opponent_team.slice(0, 2).join("·")} 상대 선출`;
  const actions = $(".form-actions", form);
  actions.innerHTML = `<button class="primary" type="submit">저장</button>`;
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    const body = { ...draft, ...Object.fromEntries(new FormData(form)), session_id: sessionId };
    if (!body.memo) delete body.memo;
    const note = document.createElement("div");
    try {
      const r = await api("/api/plans", { method: "POST", body: JSON.stringify(body) });
      note.className = "notice ok";
      note.textContent = `저장했어요 (#${r.plan.id}). 계획 관리 탭에서 확인할 수 있어요.`;
      if (r.overlaps.length) {
        note.className = "notice warn";
        note.textContent += ` ⚠️ 같은 날 시간이 겹치는 계획: ${r.overlaps.map(o => `#${o.id} ${o.title} ${o.start_time}~${o.end_time}`).join(", ")}`;
      }
      form.remove();
    } catch (e) { note.className = "notice err"; note.textContent = e.message; }
    wrap.appendChild(note);
  });
  wrap.appendChild(form);
  $(".bubble", msgEl).appendChild(wrap);
}

// ---------------------------------------------------------------- 포켓몬 이미지·타입
const TYPE_KO = { Normal: "노말", Fire: "불꽃", Water: "물", Electric: "전기", Grass: "풀", Ice: "얼음", Fighting: "격투",
  Poison: "독", Ground: "땅", Flying: "비행", Psychic: "에스퍼", Bug: "벌레", Rock: "바위", Ghost: "고스트",
  Dragon: "드래곤", Dark: "악", Steel: "강철", Fairy: "페어리" };
let CARDS = {};  // 최근 응답의 {표시 이름: {sprite, types, …}}

function sprite(name, size = "md") {
  const c = CARDS[name] || {};
  const initial = esc((name || "?").slice(0, 1));
  // 이미지가 없거나 못 불러오면 첫 글자 원으로 대신한다
  return c.sprite
    ? `<span class="sprite ${size}"><img src="${esc(c.sprite)}" alt="${esc(name)}" loading="lazy"
         onerror="this.parentNode.classList.add('noimg');this.parentNode.textContent='${initial}'"></span>`
    : `<span class="sprite ${size} noimg">${initial}</span>`;
}
function typeBadges(name) {
  return ((CARDS[name] || {}).types || []).map(t => `<span class="type t-${t.toLowerCase()}">${TYPE_KO[t] || t}</span>`).join("");
}
function speedClass(v) {
  return /선공/.test(v || "") ? "fast" : /후공/.test(v || "") ? "slow" : "even";
}

// 대면 한 줄: 내 포켓몬(공격) → 기술·데미지 → 상대(방어)
function dmgLine(x, main) {
  const lo = Math.min(x.min_pct, 100), hi = Math.min(x.max_pct, 100);
  const cls = /확정 1타/.test(x.ko) ? "ko1" : /난수 1타/.test(x.ko) ? "ko1r" : /2타/.test(x.ko) ? "ko2" : "ko3";
  return `<div class="dmg ${main ? "" : "sub"}" title="상대 체력 ${x.hp}, 데미지 ${x.min}~${x.max}${x.hits ? " (" + x.hits + ")" : ""}">
    <span class="hpbar"><span class="hp-sure" style="width:${lo}%"></span><span class="hp-maybe" style="left:${lo}%;width:${Math.max(hi - lo, 0)}%"></span></span>
    <span class="dmg-pct">${x.min_pct}~${x.max_pct}%</span>
    <span class="ko ${cls}">${esc(x.ko)}</span>
    <span class="vs-set">${esc(x.vs)}</span>
  </div>`;
}
function vsRow(m) {
  const d = m.damage;
  return `<li class="${m.answered === false ? "weak" : ""}">
    <span class="vs-side mine">${sprite(m.my_pokemon, "sm")}<b>${esc(m.my_pokemon)}</b></span>
    <span class="vs-attack">
      <span class="vs-move-line">
        <span class="vs-move">${esc(m.move)}</span>
        ${m.offense != null ? `<span class="mult m${String(m.offense).replace(".", "_")}">×${m.offense}</span>` : ""}
        <span class="spd ${speedClass(m.speed)}" title="${esc(m.speed_basis || "")}">${esc(m.speed || "")}</span>
        ${m.answered === false ? `<span class="warn-tag" title="확실히 잡는 대면이 아님">주의</span>` : ""}
      </span>
      ${d ? d.results.map((x, i) => dmgLine(x, i === 0)).join("") : `<span class="muted small">위력 정보가 없어 데미지 계산 생략</span>`}
      ${d ? `<span class="muted small">공격측 배분: ${esc(d.attacker)}${(d.results[0].ability_notes || []).length ? " · " + esc(d.results[0].ability_notes.join(", ")) : ""}</span>` : ""}
      ${(m.blocked || []).map(b => `<span class="blocked small">⛔ ${esc(b)}</span>`).join("")}
    </span>
    <span class="vs-arrow" aria-hidden="true">▶</span>
    <span class="vs-side theirs">${sprite(m.opponent, "sm")}<span>${esc(m.opponent)}</span></span>
  </li>`;
}

// 선출 3마리 + 선봉 + 대면 계획 (답변 맨 위)
function selectionCard(draft) {
  const mp = draft.move_plan || [];
  return `<section class="selection">
    <h3>선출 ${draft.selected.length}마리</h3>
    ${(draft.rule_notes || []).map(n => `<p class="rule-note">${md(n).replace(/^<p>|<\/p>$/g, "")}</p>`).join("")}
    ${draft.mega_alternatives?.length ? `<div class="mega-compare"><span class="muted small">메가진화 비교 (이 3마리 기준):</span>
      ${draft.mega_alternatives.slice().sort((a, b) => b.covered - a.covered || (b.ohko || 0) - (a.ohko || 0)).map(a => `<span class="mega-alt ${a.chosen ? "chosen" : ""}"
        title="${esc((a.covered_names || []).join(", "))}">${esc(a.mega || "메가진화 안 함")} · 상대 ${a.covered}마리 커버 · 확정 1타 ${a.ohko ?? 0}${a.chosen ? " ✓" : ""}</span>`).join("")}
    </div>` : ""}
    <div class="picks-big">${draft.selected.map(n => `
      <div class="pick-card ${n === draft.lead ? "lead" : ""} ${n === draft.mega_evolve ? "mega-evo" : ""}">
        ${n === draft.lead ? `<span class="lead-tag">선봉</span>` : ""}
        ${n === draft.mega_evolve ? `<span class="mega-tag">메가진화</span>` : ""}
        ${sprite(n, "lg")}
        <div class="pick-name">${esc(n)}</div>
        <div class="types">${typeBadges(n)}</div>
        <div class="muted small">상대 ${mp.filter(m => m.my_pokemon === n).length}마리 담당</div>
      </div>`).join("")}
    </div>
    ${mp.length ? `<h4>대면 계획 <span class="muted small">내 포켓몬 → 기술 → 상대</span></h4><ul class="vs-list">${mp.map(m => vsRow(m)).join("")}</ul>
      <p class="muted small">데미지: Lv50·개체값 31, 급소·날씨·도구·특성·랭크 변화 제외. 배분은 실전 통계 기준(없으면 '가정').</p>` : ""}
  </section>`;
}

// 상대 파티 공격 성향: 물리/특수 막대 + 포켓몬별 운용 형태
const ROLE_CLASS = r => r.includes("물리 ") || r === "물리 딜탱" ? "phys" : r.includes("특수 ") || r === "특수 딜탱" ? "spec"
  : r.includes("혼합") ? "mixed" : r.includes("서포터") ? "support" : "wall";
function profileCard(p, advice, msgEl) {
  if (!p || p.physical_pct == null) return;
  const wrap = document.createElement("div");
  wrap.className = "profile";
  wrap.innerHTML = `<h4>상대 파티 공격 성향 <span class="badge">${esc(p.lean)}</span></h4>
    <div class="ratio" role="img" aria-label="물리 ${p.physical_pct}%, 특수 ${p.special_pct}%">
      <span class="phys" style="width:${p.physical_pct}%">물리 ${p.physical_pct}%</span>
      <span class="spec" style="width:${p.special_pct}%">특수 ${p.special_pct}%</span>
    </div>
    <ul class="roles">${p.members.map(m => `<li>${sprite(m.name, "xs")}<b>${esc(m.name)}</b>
      ${m.physical_share == null && !m.roles.length ? `<span class="muted">통계 없음</span>` :
        m.roles.length ? m.roles.map(r => `<span class="role ${ROLE_CLASS(r.role)}" title="${esc(r.typical)}">${esc(r.role)} ${r.pct}%</span>`).join("")
        : `<span class="muted">${esc(m.basis)} · 물리 ${m.physical_share}%</span>`}</li>`).join("")}</ul>
    ${advice ? `<p class="advice">${esc(advice)}</p>` : ""}`;
  $(".bubble", msgEl).appendChild(wrap);
}

// 메가진화: 내 팀은 '메가스톤을 든 포켓몬'을 여러 마리 고른다 (배틀에서 메가진화는 그중 1번 → 선출 추천이 누가 할지 계산).
// 상대는 통계 예상을 보여주고 바꿀 수 있게 한다.
function megaCard(mega, originalText, msgEl, collapsed) {
  if (!mega || (!mega.my_options?.length && !mega.opp_predictions?.length)) return;
  const held = mega.my_holders || {};
  const hasHolders = Object.keys(held).length > 0;
  // 행마다 하나 고름: 이미 정한 스톤 → 없으면 가장 많이 쓰이는 메가 폼을 미리 선택
  const holders = {};
  for (const o of mega.my_options || []) {
    const best = [...o.forms].sort((a, b) => (b.usage_pct || 0) - (a.usage_pct || 0))[0];
    holders[o.base] = hasHolders ? (held[o.base] || "none") : (best ? best.value : "none");
  }
  const oppHeld = mega.opp_holders || {};
  const oppHolders = {};
  for (const p of mega.opp_predictions || []) oppHolders[p.base] = oppHeld[p.base] || "none";
  let oppTouched = false;  // 상대 메가는 사용자가 바꿨을 때만 '지정'으로 보낸다 (아니면 계속 통계 예상)
  const chip = (group, value, label, extra, sprite, checked) => `<button type="button" class="chip toggle mega-chip" role="radio"
      data-group="${esc(group)}" data-value="${esc(value)}" data-label="${esc(label)}" aria-checked="${String(checked)}">
      ${sprite ? `<img src="${esc(sprite)}" alt="" loading="lazy">` : ""}${esc(label)}${extra ? `<small>${esc(extra)}</small>` : ""}</button>`;
  const myRows = (mega.my_options || []).map(o => `<div class="clarify-row"><div class="clarify-input">${esc(o.pokemon)}</div>
      <div class="chips" role="radiogroup" aria-label="${esc(o.pokemon)} 메가스톤">
        ${chip("my:" + o.base, "none", "스톤 없음", "", "", holders[o.base] === "none")}
        ${o.forms.map(f => chip("my:" + o.base, f.value, f.label, f.usage_pct != null ? `사용률 ${f.usage_pct}%` : "", f.sprite, holders[o.base] === f.value)).join("")}
      </div></div>`).join("");
  const oppRows = (mega.opp_predictions || []).map(p => `<div class="clarify-row"><div class="clarify-input">${esc(p.pokemon)}
      <small class="muted" title="${p.rating ? p.rating + "+ 통계 " : ""}메가 폼 비율 ${p.mega_share}% → 이 상대 팀원 구성 반영 ${p.mega_pct}%">메가 ${p.mega_pct}%</small></div>
      <div class="chips" role="radiogroup">
        ${chip("opp:" + p.base, "none", "스톤 없음", "", "", oppHolders[p.base] === "none")}
        ${p.forms.map(f => chip("opp:" + p.base, f.value, f.label, `${f.predicted}%`, f.sprite, oppHolders[p.base] === f.value)).join("")}
      </div></div>`).join("");
  const wrap = document.createElement(collapsed ? "details" : "div");
  wrap.className = "clarify mega" + (collapsed ? " mega-fold" : "");
  wrap.innerHTML = (collapsed ? `<summary>메가스톤·상대 메가 설정 바꾸기</summary>` : "") + `
    ${myRows ? `<h4>내 팀: 메가스톤을 든 포켓몬 <span class="muted small">여러 마리 가능 · 배틀에서 메가진화는 1번 → 누가 할지는 선출 추천에서 계산</span></h4>${myRows}` : ""}
    ${oppRows ? `<h4>상대 메가스톤 예상 <span class="muted small">${mega.opp_rating ? mega.opp_rating + "+ 레이팅 " : ""}메가 폼 비율 × 상대 팀원 구성 · 50% 이상이면 보유로 예상 · 바꿀 수 있어요</span></h4>${oppRows}` : ""}
    <div class="form-actions"><button type="button" class="primary">이대로 계산</button></div>`;
  wrap.querySelectorAll(".mega-chip").forEach(btn => btn.addEventListener("click", () => {
    const g = btn.dataset.group;
    wrap.querySelectorAll(`.mega-chip[data-group="${g}"]`).forEach(b => b.setAttribute("aria-checked", String(b === btn)));
    if (g.startsWith("opp:")) { oppHolders[g.slice(4)] = btn.dataset.value; oppTouched = true; }
    else holders[g.slice(3)] = btn.dataset.value;
  }));
  $(".primary", wrap).addEventListener("click", () => {
    const names = Object.entries(holders).filter(([, v]) => v !== "none")
      .map(([b, v]) => wrap.querySelector(`.mega-chip[data-group="my:${b}"][data-value="${v}"]`)?.dataset.label || v);
    const oppNames = Object.entries(oppHolders).filter(([, v]) => v !== "none")
      .map(([b, v]) => wrap.querySelector(`.mega-chip[data-group="opp:${b}"][data-value="${v}"]`)?.dataset.label || v);
    const oppName = oppNames.join(", ") || "없음";
    wrap.querySelectorAll("button").forEach(b => b.disabled = true);
    const choice = { my_holders: holders };
    if (oppTouched) choice.opp_holders = oppHolders;
    send(originalText, null, `메가스톤: ${names.join(", ") || "없음"}${oppRows ? ` / 상대 메가스톤: ${oppName}${oppTouched ? "" : " (예상)"}` : ""}`,
         null, choice);
  });
  $(".bubble", msgEl).appendChild(wrap);
}

// 이름 확인: 후보를 토글 칩으로 보여주고, 고른 이름으로 같은 요청을 다시 보낸다
function clarifyCard(items, originalText, msgEl) {
  const choices = {};
  const wrap = document.createElement("div");
  wrap.className = "clarify";
  const askable = items.filter(it => it.candidates.length);
  wrap.innerHTML = `<h4>어떤 포켓몬인가요?</h4>` + askable.map((it, i) => `
    <div class="clarify-row" data-i="${i}">
      <div class="clarify-input">'${esc(it.input)}'</div>
      <div class="chips" role="radiogroup" aria-label="${esc(it.input)} 후보">
        ${it.candidates.map(c => `<button type="button" class="chip toggle" role="radio" aria-checked="false"
            data-value="${esc(c.value)}" title="${esc(c.value)}">${esc(c.label)}</button>`).join("")}
      </div>
    </div>`).join("") +
    (items.length > askable.length ? `<p class="muted">후보가 없는 이름은 다시 입력해 주세요.</p>` : "") +
    `<div class="form-actions"><button type="button" class="primary" disabled>선택한 이름으로 다시 계산</button></div>`;
  const submit = $(".primary", wrap);
  wrap.querySelectorAll(".clarify-row").forEach(row => {
    const item = askable[+row.dataset.i];
    row.querySelectorAll(".toggle").forEach(btn => btn.addEventListener("click", () => {
      row.querySelectorAll(".toggle").forEach(b => b.setAttribute("aria-checked", String(b === btn)));
      choices[item.input] = btn.dataset.value;
      choices["__label__" + item.input] = btn.textContent;
      submit.disabled = askable.some(a => !choices[a.input]) || items.length > askable.length;
    }));
  });
  wrap.querySelectorAll(".clarify-row").forEach(row => {  // 후보가 하나뿐이면 미리 골라 둔다
    const t = row.querySelectorAll(".toggle");
    if (t.length === 1) t[0].click();
  });
  submit.addEventListener("click", () => {
    const picked = Object.fromEntries(askable.map(a => [a.input, choices[a.input]]));
    const summary = askable.map(a => `${a.input} → ${choices["__label__" + a.input]}`).join(", ");
    wrap.querySelectorAll("button").forEach(b => b.disabled = true);
    send(originalText, picked, `이름 선택: ${summary}`);
  });
  $(".bubble", msgEl).appendChild(wrap);
}

let lastTeams = null;  // 직접 선택 모드로 보낸 팀 (메가 선택 후 다시 계산할 때 같이 보낸다)
async function send(text, nameChoices = null, displayText = null, teams = null, megaChoice = null) {
  addMsg("user", esc(displayText || text));
  const pending = addMsg("agent", `<span class="typing">분석하고 Tool을 호출하는 중 <i></i><i></i><i></i></span>`);
  $("#send").disabled = true;
  try {
    const payload = { message: text, session_id: sessionId, game: rule.game, battle: rule.battle };
    if (nameChoices) payload.name_choices = nameChoices;
    if (teams) { Object.assign(payload, teams); lastTeams = teams; }
    else if (megaChoice && lastTeams) Object.assign(payload, lastTeams);
    if (megaChoice) payload.mega_choice = megaChoice;
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify(payload) });
    sessionId = r.session_id; store("poke.session", sessionId);
    const [label, cls] = STATUS_BADGE[r.status] || [r.status || "-", ""];
    const tools = r.events.filter(e => e.type === "tool_call_end");
    const fails = tools.filter(e => !e.payload.ok).length;
    let html = md(r.answer);
    if (r.error?.code === "INVALID_KEY") html += `<div class="notice warn">설정 안내: backend/.env 의 GMS_API_KEY 를 확인한 뒤 서버를 다시 시작하세요.</div>`;
    html += `<div class="meta-line">
      <span class="badge ${cls}">${label}</span>
      ${r.replans.length ? `<span class="badge replan">재계획 ${r.replans.length}회</span>` : ""}
      <span class="badge">Tool ${tools.length}회${fails ? ` · 실패 ${fails}` : ""}</span>
      <span class="badge">${(r.total_ms / 1000).toFixed(1)}s</span>
      ${r.ruleset ? `<span class="badge">${esc(r.ruleset.game_label)} · ${esc(r.ruleset.battle_label)}</span>` : ""}</div>
      <details class="trace"><summary>실행 과정 보기</summary>${renderTrace(r.events)}</details>`;
    CARDS = r.pokemon_cards || {};
    if (r.plan_draft) {  // 선출 결과를 맨 위에 크게, 자세한 설명은 접어서 아래에
      html = selectionCard({ ...r.plan_draft, move_plan: r.move_plan || r.plan_draft.move_plan, rule_notes: r.rule_notes,
                                mega_evolve: r.mega_evolve, mega_alternatives: r.mega_alternatives }) + `<details class="detail-text"><summary>자세한 설명 보기</summary>${html}</details>`;
    }
    pending.querySelector(".bubble").innerHTML = html;
    if (r.status === "error") pending.querySelector(".bubble").classList.add("error");
    profileCard(r.opp_profile, r.defense_advice, pending);
    if (r.plan_draft) draftCard(r.plan_draft, pending);
    if (r.clarify?.length) clarifyCard(r.clarify, text, pending);
    else if (r.mega && r.status === "ask") megaCard(r.mega, text, pending, false);
    else if (r.mega && r.plan_draft) megaCard(r.mega, text, pending, true);
  } catch (e) {
    pending.querySelector(".bubble").innerHTML = `<div class="notice err">${esc(e.message)}</div>`;
  } finally {
    $("#send").disabled = false;
    messages.scrollTop = messages.scrollHeight;
  }
}

$("#chat-form").addEventListener("submit", ev => {
  ev.preventDefault();
  const text = $("#input").value.trim();
  if (!text) return;
  $("#input").value = "";
  send(text);
});
$("#input").addEventListener("keydown", ev => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); $("#chat-form").requestSubmit(); }
});
document.querySelectorAll("[data-example]").forEach(b => b.addEventListener("click", () => {
  $("#input").value = b.dataset.example; $("#input").focus();
}));
$("#new-session").addEventListener("click", () => {
  sessionId = null; store("poke.session");
  [...messages.querySelectorAll(".msg:not(.intro)")].forEach(m => m.remove());
});

// ---------------------------------------------------------------- 계획 관리
async function loadPlans() {
  const box = $("#plans");
  const date = $("#plan-filter").value;
  try {
    const plans = await api("/api/plans" + (date ? `?date=${date}` : ""));
    box.innerHTML = plans.length ? "" : `<p class="empty">저장한 계획이 없습니다. 대화에서 선출 계획을 만든 뒤 저장하세요.</p>`;
    plans.forEach(p => box.appendChild(planCard(p)));
  } catch (e) { box.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}

function planCard(p) {
  const card = document.createElement("article");
  card.className = "card";
  card.innerHTML = `
    <header><h3>${esc(p.title)}</h3><span class="when">${esc(p.battle_date)} ${esc(p.start_time)}~${esc(p.end_time)}</span></header>
    <div class="muted" style="font-size:13px">${esc(p.format)} · 상대: ${p.opponent_team.map(esc).join(", ")}</div>
    <div class="picks">${p.selected.map(s => `<span class="pick ${s === p.lead ? "lead" : ""}">${esc(s)}</span>`).join("")}</div>
    ${p.move_plan.length ? `<table class="mp"><thead><tr><th>상대</th><th>대면</th><th>기술</th><th>선공</th></tr></thead><tbody>
      ${p.move_plan.map(m => `<tr><td>${m.answered === false ? "⚠️ " : ""}${esc(m.opponent)}</td><td>${esc(m.my_pokemon)}</td>
      <td>${esc(m.move)}${m.offense != null ? ` (${m.offense}배)` : ""}</td><td>${esc(m.speed ?? "")}</td></tr>`).join("")}
      </tbody></table>` : ""}
    ${p.memo ? `<p class="muted">메모: ${esc(p.memo)}</p>` : ""}
    <div class="muted" style="font-size:12px;margin-top:6px">생성 ${esc(p.created_at)}${p.updated_at !== p.created_at ? ` · 수정 ${esc(p.updated_at)}` : ""}</div>
    <div class="card-actions"><button class="ghost" data-act="edit">수정</button><button class="danger" data-act="del">삭제</button></div>`;

  $('[data-act="del"]', card).addEventListener("click", async () => {
    if (!confirm(`'${p.title}' 계획을 삭제할까요?`)) return;
    try { await api(`/api/plans/${p.id}`, { method: "DELETE" }); loadPlans(); }
    catch (e) { alert(e.message); }
  });
  $('[data-act="edit"]', card).addEventListener("click", () => {
    if ($(".plan-form", card)) return;
    const form = $("#tpl-plan-form").content.firstElementChild.cloneNode(true);
    for (const k of ["title", "battle_date", "start_time", "end_time", "memo"]) form[k].value = p[k] ?? "";
    $(".form-actions", form).innerHTML = `<button type="button" class="ghost" data-act="cancel">취소</button><button class="primary">수정 저장</button>`;
    $('[data-act="cancel"]', form).addEventListener("click", () => form.remove());
    form.addEventListener("submit", async ev => {
      ev.preventDefault();
      try {
        const r = await api(`/api/plans/${p.id}`, { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
        await loadPlans();
        if (r.overlaps.length) alert(`수정했어요. 같은 날 시간이 겹치는 계획: ${r.overlaps.map(o => `#${o.id} ${o.title}`).join(", ")}`);
      } catch (e) { alert(e.message); }
    });
    card.appendChild(form);
  });
  return card;
}
$("#plans-refresh").addEventListener("click", loadPlans);
$("#plan-filter").addEventListener("change", loadPlans);

// ---------------------------------------------------------------- 실행 로그
async function loadLogSessions() {
  const list = $("#log-sessions");
  try {
    const sessions = await api("/api/logs");
    list.innerHTML = sessions.length ? "" : `<li class="muted">실행 로그가 없습니다.</li>`;
    for (const s of sessions) {
      const li = document.createElement("li");
      li.innerHTML = `<code>${esc(s.session_id)}</code><br><span class="muted">${esc(s.last_ts.slice(0, 19).replace("T", " "))}</span><br>
        실행 ${s.runs} · Tool ${s.tool_calls}${s.replans ? ` · <span class="badge replan">재계획 ${s.replans}</span>` : ""}`;
      li.addEventListener("click", () => {
        list.querySelectorAll("li").forEach(x => x.classList.toggle("active", x === li));
        loadLogDetail(s.session_id);
      });
      list.appendChild(li);
    }
  } catch (e) { list.innerHTML = `<li class="notice err">${esc(e.message)}</li>`; }
}

async function loadLogDetail(id) {
  const box = $("#log-detail");
  try {
    const events = await api(`/api/logs/${encodeURIComponent(id)}`);
    box.innerHTML = `<div class="toolbar"><b>${esc(id)}</b><button class="danger" id="log-del">로그 삭제</button></div>
      <table class="events"><thead><tr><th>#</th><th>시각</th><th>이벤트</th><th>단계</th><th>내용</th></tr></thead><tbody>
      ${events.map(e => {
        const fail = e.type === "tool_call_end" && !e.payload.ok;
        return `<tr class="${e.type === "replan" ? "replan" : fail ? "fail" : ""}"><td>${e.seq}</td>
          <td>${esc(e.ts.slice(11, 23))}</td><td>${esc(e.type)}</td><td>${esc(e.step ?? "")}</td>
          <td><code>${esc(JSON.stringify(e.payload))}</code></td></tr>`;
      }).join("")}</tbody></table>`;
    $("#log-del").addEventListener("click", async () => {
      if (!confirm("이 세션의 실행 로그를 삭제할까요?")) return;
      await api(`/api/logs/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(e => alert(e.message));
      box.innerHTML = `<p class="muted">삭제했습니다.</p>`;
      loadLogSessions();
    });
  } catch (e) { box.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}
$("#logs-refresh").addEventListener("click", loadLogSessions);
renderRule();
renderMode();

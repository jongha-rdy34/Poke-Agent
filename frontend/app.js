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
    <ul class="roles">${p.members.map(m => `<li><b>${esc(m.name)}</b>
      ${m.physical_share == null && !m.roles.length ? `<span class="muted">통계 없음</span>` :
        m.roles.length ? m.roles.map(r => `<span class="role ${ROLE_CLASS(r.role)}" title="${esc(r.typical)}">${esc(r.role)} ${r.pct}%</span>`).join("")
        : `<span class="muted">${esc(m.basis)} · 물리 ${m.physical_share}%</span>`}</li>`).join("")}</ul>
    ${advice ? `<p class="advice">${esc(advice)}</p>` : ""}`;
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
  submit.addEventListener("click", () => {
    const picked = Object.fromEntries(askable.map(a => [a.input, choices[a.input]]));
    const summary = askable.map(a => `${a.input} → ${choices["__label__" + a.input]}`).join(", ");
    wrap.querySelectorAll("button").forEach(b => b.disabled = true);
    send(originalText, picked, `이름 선택: ${summary}`);
  });
  $(".bubble", msgEl).appendChild(wrap);
}

async function send(text, nameChoices = null, displayText = null) {
  addMsg("user", esc(displayText || text));
  const pending = addMsg("agent", `<span class="typing">분석하고 Tool을 호출하는 중 <i></i><i></i><i></i></span>`);
  $("#send").disabled = true;
  try {
    const payload = { message: text, session_id: sessionId };
    if (nameChoices) payload.name_choices = nameChoices;
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
      <span class="badge">${(r.total_ms / 1000).toFixed(1)}s</span></div>
      <details class="trace"><summary>실행 과정 보기</summary>${renderTrace(r.events)}</details>`;
    pending.querySelector(".bubble").innerHTML = html;
    if (r.status === "error") pending.querySelector(".bubble").classList.add("error");
    profileCard(r.opp_profile, r.defense_advice, pending);
    if (r.plan_draft) draftCard(r.plan_draft, pending);
    if (r.clarify?.length) clarifyCard(r.clarify, text, pending);
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

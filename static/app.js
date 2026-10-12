// 이중길드 검사기 화면 — 입력 받기, 진행률 보여주기, 결과 그리기
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `오류 ${r.status}`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), isErr ? 6000 : 2500);
}

// ── 테마 (라이트/다크) ──
function currentTheme() {
  return document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function paintThemeBtn() {
  $("#themeBtn").textContent = currentTheme() === "dark" ? "☀️ 라이트" : "🌙 다크";
}
$("#themeBtn").onclick = () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("gc_theme", next);
  paintThemeBtn();
};
paintThemeBtn();

// ── 화면 전환 ──
function showView() {
  const v = { "#history": "history", "#lookup": "lookup" }[location.hash] || "check";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === v));
  $("#view-check").hidden = v !== "check";
  $("#view-lookup").hidden = v !== "lookup";
  $("#view-history").hidden = v !== "history";
  if (v === "lookup") {
    // 검사 화면에서 넣은 키·월드를 이 화면에서도 쓰게 (메모리에서만, 저장 안 함)
    if (!$("#lkKey").value) $("#lkKey").value = $("#apiKey").value;
    if (!$("#lkWorld").dataset.touched) $("#lkWorld").value = $("#world").value;
  }
  if (v === "history") loadHistory();
}
window.addEventListener("hashchange", showView);

// ── 입력값 기억 ──
// API 키는 기억하지 않는다 (검사할 때마다 입력, 서버도 저장하지 않음). 예전 버전이 남긴 키는 지운다.
const fields = { world: "gc_world", guild: "gc_guild", reverse: "gc_reverse" };
try { localStorage.removeItem("gc_key"); } catch (e) {}
for (const [id, k] of Object.entries(fields)) {
  const v = store.get(k);
  if (v !== null) $("#" + id).value = v;
}
$("#activity").checked = store.get("gc_activity") === "1";
$("#keyShow").onclick = () => {
  const i = $("#apiKey");
  i.type = i.type === "password" ? "text" : "password";
  $("#keyShow").textContent = i.type === "password" ? "보기" : "숨기기";
};

// ── 검사 시작 ──
$("#scanForm").onsubmit = async (e) => {
  e.preventDefault();
  for (const [id, k] of Object.entries(fields)) store.set(k, $("#" + id).value.trim());
  store.set("gc_activity", $("#activity").checked ? "1" : "0");
  $("#scanBtn").disabled = true;
  try {
    const job = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({
        api_key: $("#apiKey").value.trim(),
        world: $("#world").value,
        guild: $("#guild").value.trim(),
        reverse_top: Number($("#reverse").value),
        activity: $("#activity").checked,
      }),
    });
    poll(job);
  } catch (err) {
    toast(err.message, true);
    $("#scanBtn").disabled = false;
  }
};

let pollTimer = null;
function poll(job) {
  renderProgress(job);
  clearTimeout(pollTimer);
  if (job.status !== "running") return finish(job);
  pollTimer = setTimeout(async () => {
    try { poll(await api(`/api/jobs/${job.id}`)); }
    catch (err) { toast("진행 상황을 못 받았어요: " + err.message, true); $("#scanBtn").disabled = false; }
  }, 1000);
}

function renderProgress(j) {
  const p = $("#progress");
  p.hidden = false;
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  const steps = Array.from({ length: j.steps }, (_, i) => `<span class="${i < j.step ? "done" : ""}"></span>`).join("");
  p.innerHTML = `
    <div class="phase"><span>${esc(j.guild)} · ${esc(j.world)} — ${esc(j.phase)}</span>
      <span>${j.total ? `${j.done.toLocaleString()} / ${j.total.toLocaleString()} (${pct}%)` : ""}</span></div>
    <div class="bar ${j.total ? "" : "wait"}"><i style="width:${pct}%"></i></div>
    <div class="row-end"><div class="steps" title="단계 ${j.step}/${j.steps}">${steps}</div>
      <span class="muted">단계 ${j.step} / ${j.steps}</span>
      <button type="button" class="danger" id="cancelBtn">멈추기</button></div>`;
  $("#cancelBtn").onclick = async () => {
    $("#cancelBtn").disabled = true;
    await api(`/api/jobs/${j.id}/cancel`, { method: "POST" }).catch(() => {});
  };
}

async function finish(job) {
  $("#scanBtn").disabled = false;
  $("#progress").hidden = true;
  if (job.status === "done") {
    toast("검사 끝!");
    await openScan(job.scan_id);
  } else {
    toast(job.error || "검사가 멈췄어요.", job.status === "error");
  }
}

// ── 결과 ──
let current = null;
let filter = "all";
let query = "";

async function openScan(id) {
  try {
    current = await api(`/api/scans/${id}`);
  } catch (err) {
    return toast(err.message, true);
  }
  store.set("gc_last_scan", id);
  filter = "all"; query = "";
  if (location.hash === "#history") location.hash = "#check";
  renderResult();
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function avatar(c, cls = "") {
  const img = c && c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy">` : `<span class="none">?</span>`;
  return `<div class="avatar ${cls}">${img}</div>`;
}
const lvjob = (c) => [c.level ? `Lv.${c.level}` : "", c.job || ""].filter(Boolean).join(" · ") || "정보 없음";

function charChip(c, red = false) {
  const sub = red ? `${esc(c.guild)} · Lv.${c.level ?? "?"}` : esc(lvjob(c));
  return `<div class="ch ${red ? "red" : ""}">${avatar(c, "sm")}<div class="t"><b>${esc(c.name)}</b><span>${sub}</span></div></div>`;
}

function accCard(a) {
  const r = a.rep;
  const flags = [];
  if (a.forward) flags.push(`본캐(대표)가 「${esc(r.guild)}」 길드`);
  if (a.reverse) flags.push(`부캐 ${a.others.length}명이 다른 길드`);
  const badges = [];
  if (a.rep_in_ours) badges.push(`<span class="badge ok">본캐 우리 길드</span>`);
  else if (a.forward) badges.push(`<span class="badge dual">본캐 · ${esc(r.guild)}</span>`);
  else if (a.rep_guildless) badges.push(`<span class="badge gold">본캐 길드 없음</span>`);
  else badges.push(`<span class="badge gold">본캐 정보 없음</span>`);
  if (a.union_level) badges.push(`<span class="badge acc-c">유니온 ${Number(a.union_level).toLocaleString()}</span>`);
  if (a.stale30) badges.push(`<span class="badge sky">30일 성장 없음</span>`);
  if (r.away7) badges.push(`<span class="badge sky">7일 미접속</span>`);

  const ours = a.ours.filter((c) => !(a.rep_in_ours && c.name === r.name));
  let alts = "";
  if (ours.length) {
    alts += `<div class="ttl">${a.rep_in_ours ? "우리 길드 부캐" : "우리 길드에 있는 캐릭터"} ${ours.length}</div>
      <div class="chars">${ours.map((c) => charChip(c)).join("")}</div>`;
  }
  if (a.others.length) {
    alts += `<div class="ttl red" style="margin-top:${ours.length ? 10 : 2}px">다른 길드에 있는 부캐 ${a.others.length}</div>
      <div class="chars">${a.others.map((c) => charChip(c, true)).join("")}</div>`;
  }
  return `<article class="acc ${a.dual ? "is-dual" : ""}">
    ${a.dual ? `<div class="flag">⚠ 이중길드 <span>— ${flags.join(" · ")}</span></div>` : ""}
    <div class="main">${avatar(r)}
      <div class="who"><div class="sub">대표 캐릭터</div><div class="name">${esc(r.name)}</div>
        <div class="sub">${esc(lvjob(r))}</div><div class="badges">${badges.join("")}</div></div></div>
    ${alts ? `<div class="alts">${alts}</div>` : ""}
  </article>`;
}

function matches(a) {
  if (filter === "dual" && !a.dual) return false;
  if (filter === "multi" && a.ours.length + a.others.length < 2) return false;
  if (filter === "stale" && !a.stale30 && !a.rep.away7) return false;
  if (!query) return true;
  const q = query.toLowerCase();
  return [a.rep, ...a.ours, ...a.others].some((c) => (c.name || "").toLowerCase().includes(q) || (c.guild || "").toLowerCase().includes(q));
}

function renderResult() {
  const box = $("#result");
  if (!current) return renderWelcome();
  const res = current.result, s = res.summary, g = res.guild, d = current.diff, o = current.options || {};
  const revTxt = { 0: "끔", "-1": "전체 길드" }[o.reverse_top] ?? `상위 ${o.reverse_top}개 길드`;

  let diffHtml = "";
  if (d) {
    const list = (arr) => (arr.length ? arr.map(esc).join(", ") : "<span class='muted'>없음</span>");
    diffHtml = `<div class="card"><h3 style="margin-bottom:10px">지난 검사(${esc(d.prev_date)} 기준)와 비교</h3>
      <div class="diff">
        <div class="new"><b>새로 생긴 이중길드</b>${list(d.new_dual)}</div>
        <div class="gone"><b>이중길드 아님으로 바뀜</b>${list(d.gone_dual)}</div>
        <div><b>새로 들어온 캐릭터</b>${list(d.joined)}</div>
        <div><b>나간 캐릭터</b>${list(d.left)}</div>
      </div></div>`;
  }

  const dual = res.accounts.filter((a) => a.dual && matches(a));
  const normal = res.accounts.filter((a) => !a.dual && matches(a));
  const unk = res.unknown.filter((u) => !query || u.name.toLowerCase().includes(query.toLowerCase()));
  const showUnk = filter === "all" || filter === "unknown";

  const chips = [["all", "전체"], ["dual", "이중길드만"], ["multi", "부캐 있는 계정"]];
  if (s.activity_on) chips.push(["stale", "성장 없음·미접속"]);
  chips.push(["unknown", "판별 불가"]);

  box.innerHTML = `
    <div class="res-head">
      <div><h2>${esc(g.name)}<small>${esc(g.world)}${g.level ? ` · Lv.${g.level}` : ""}</small></h2>
        <div class="meta">${esc(g.date)} 데이터 기준 · 길드 마스터 ${esc(g.master || "?")} · 검사 ${esc(current.created_at.replace("T", " "))}</div></div>
      <div class="meta">역방향: ${revTxt}${s.reverse_on ? ` (다른 길드 ${s.other_guilds}개, ${s.other_members.toLocaleString()}명 확인)` : ""}<br>
        넥슨 API ${s.api_calls.toLocaleString()}번 호출 · 캐시 ${s.cache_hits.toLocaleString()}번 사용 · ${s.seconds}초</div>
    </div>
    <div class="tiles">
      <div class="card tile dual"><div class="label">이중길드 계정</div><div class="value">${s.dual}</div>
        <div class="sub">본캐 다른 길드 ${s.forward}${s.reverse_on ? ` · 부캐 다른 길드 ${s.reverse}` : " · 역방향 꺼짐"}</div></div>
      <div class="card tile real"><div class="label">실질 길드원 (계정)</div><div class="value">${s.accounts}</div>
        <div class="sub">부캐 있는 계정 ${s.multi}</div></div>
      <div class="card tile chars"><div class="label">길드 캐릭터 수</div><div class="value">${s.characters}</div>
        <div class="sub">길드 목록 기준</div></div>
      <div class="card tile unk"><div class="label">판별 불가</div><div class="value">${s.unknown}</div>
        <div class="sub">유니온 랭킹에 없음 등</div></div>
      ${s.activity_on ? `<div class="card tile stale"><div class="label">30일 성장 없음</div><div class="value">${s.stale30}</div><div class="sub">대표 캐릭터 기준</div></div>` : ""}
    </div>
    ${diffHtml}
    <div class="toolbar">
      <div class="chips">${chips.map(([k, t]) => `<button type="button" data-f="${k}" class="${filter === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <input id="search" placeholder="캐릭터·길드 이름 찾기" value="${esc(query)}">
    </div>
    ${filter !== "unknown" ? `
      <div class="sec-title dual"><h3>이중길드</h3><span class="count">${dual.length}</span></div>
      ${dual.length ? `<div class="acc-grid">${dual.map(accCard).join("")}</div>` : `<div class="empty">${s.dual ? "조건에 맞는 계정이 없어요" : "이중길드가 없어요 🎉"}</div>`}
      ${filter !== "dual" ? `
        <div class="sec-title"><h3>실질 길드원</h3><span class="count">${normal.length}</span><span class="muted">대표 캐릭터 기준으로 묶은 계정</span></div>
        ${normal.length ? `<div class="acc-grid">${normal.map(accCard).join("")}</div>` : `<div class="empty">조건에 맞는 계정이 없어요</div>`}` : ""}` : ""}
    ${showUnk ? `
      <div class="sec-title"><h3>판별 불가</h3><span class="count">${unk.length}</span><span class="muted">어느 계정인지 알 수 없어서 따로 모았어요</span></div>
      ${unk.length ? `<div class="unk-grid">${unk.map((u) => `<div class="ch">${avatar(u, "sm")}<div class="t"><b>${esc(u.name)}</b>
        <span>${esc(u.reason)}${u.level ? ` · Lv.${u.level}` : ""}</span></div></div>`).join("")}</div>` : `<div class="empty">없어요</div>`}` : ""}`;

  box.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { filter = b.dataset.f; renderResult(); }));
  const si = $("#search");
  si.oninput = () => {
    query = si.value.trim();
    const pos = si.selectionStart;
    renderResult();
    const n = $("#search"); n.focus(); n.setSelectionRange(pos, pos);
  };
}

function renderWelcome() {
  $("#result").innerHTML = `<div class="welcome">
    <div class="card"><div class="num">1</div><h3>API 키 넣기</h3><p>넥슨 오픈API 사이트에서 받은 키를 넣어요. 이 브라우저에만 기억돼요.</p></div>
    <div class="card"><div class="num">2</div><h3>월드·길드 고르기</h3><p>우리 길드원마다 유니온 대표 캐릭터를 찾아 계정별로 묶어요.</p></div>
    <div class="card"><div class="num">3</div><h3>검사 누르기</h3><p>대표 캐릭터가 다른 길드에 있으면 이중길드로 표시돼요. 결과는 '지난 기록'에 남아요.</p></div>
  </div>`;
}

// ── 지난 기록 ──
async function loadHistory() {
  const box = $("#historyList");
  let list;
  try { list = await api("/api/scans"); } catch (err) { return toast(err.message, true); }
  if (!list.length) { box.innerHTML = `<div class="empty">아직 검사한 기록이 없어요</div>`; return; }
  box.innerHTML = list.map((h) => {
    const s = h.summary || {}, o = h.options || {};
    return `<div class="card hist" data-id="${h.id}">
      <div class="top-line"><span class="g">${esc(h.guild)}</span><span class="muted">${esc(h.world)}</span></div>
      <div class="muted">${esc(h.data_date)} 데이터 · ${esc(h.created_at.replace("T", " "))}</div>
      <div class="nums">
        <span class="badge dual">이중길드 ${s.dual ?? "?"}</span>
        <span class="badge ok">계정 ${s.accounts ?? "?"}</span>
        <span class="badge">캐릭터 ${s.characters ?? "?"}</span>
        <span class="badge gold">판별 불가 ${s.unknown ?? "?"}</span>
      </div>
      <div class="foot"><span class="muted">역방향 ${o.reverse_top ? (o.reverse_top < 0 ? "전체" : "상위 " + o.reverse_top) : "끔"}</span>
        <button type="button" class="danger" data-del="${h.id}">삭제</button></div>
    </div>`;
  }).join("");
  box.querySelectorAll(".hist").forEach((el) => (el.onclick = (e) => {
    if (e.target.dataset.del) return;
    openScan(el.dataset.id);
  }));
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("이 검사 기록을 지울까요?")) return;
    await api(`/api/scans/${b.dataset.del}`, { method: "DELETE" });
    if (current && String(current.id) === b.dataset.del) { current = null; store.set("gc_last_scan", ""); renderResult(); }
    loadHistory();
  }));
}

// ── 시작 ──
(async function init() {
  showView();
  const running = await api("/api/jobs/current").catch(() => null);
  if (running) { $("#scanBtn").disabled = true; poll(running); }
  const last = store.get("gc_last_scan");
  if (last) {
    try { current = await api(`/api/scans/${last}`); } catch (e) { current = null; }
  }
  renderResult();
})();

// ── 이름 조회 ──
$("#lkWorld").onchange = () => ($("#lkWorld").dataset.touched = "1");
$("#lookupForm").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("#lkBtn");
  btn.disabled = true;
  btn.textContent = "조회 중…";
  try {
    const r = await api("/api/lookup", {
      method: "POST",
      body: JSON.stringify({ api_key: $("#lkKey").value.trim(), world: $("#lkWorld").value, names: $("#lkNames").value }),
    });
    renderLookup(r);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "조회";
  }
};

function lookupCard(r) {
  if (!r.found) {
    return `<article class="acc"><div class="main">${avatar(null)}<div class="who"><div class="name">${esc(r.name)}</div>
      <div class="sub">${esc(r.reason)}</div></div></div></article>`;
  }
  const c = r.char, p = r.rep;
  const g = (x) => (x ? `「${esc(x)}」` : "길드 없음");
  const badges = [];
  if (!p) badges.push(`<span class="badge gold">${esc(r.reason || "대표 캐릭터를 알 수 없음")}</span>`);
  else if (r.is_rep) badges.push(`<span class="badge ok">이 캐릭터가 대표(본캐)</span>`);
  else if (r.dual) badges.push(`<span class="badge dual">대표는 ${g(p.guild)} 길드</span>`);
  else if (p.guild === c.guild && c.guild) badges.push(`<span class="badge ok">대표와 같은 길드</span>`);
  else badges.push(`<span class="badge gold">대표 ${g(p.guild)}</span>`);
  if (r.union_level) badges.push(`<span class="badge acc-c">유니온 ${Number(r.union_level).toLocaleString()}</span>`);
  if (c.access === "false") badges.push(`<span class="badge sky">7일 미접속</span>`);
  const same = r.same_account && r.same_account.length
    ? `<div class="ttl" style="margin-top:10px">넣은 이름 중 같은 계정</div><div class="sub">${r.same_account.map(esc).join(", ")}</div>` : "";
  return `<article class="acc ${r.dual ? "is-dual" : ""}">
    ${r.dual ? `<div class="flag">⚠ 이중길드 <span>— 이 캐릭터는 ${g(c.guild)}, 대표(본캐)는 ${g(p.guild)}</span></div>` : ""}
    <div class="main">${avatar(c)}
      <div class="who"><div class="sub">조회한 캐릭터 · ${g(c.guild)}</div><div class="name">${esc(c.name)}</div>
        <div class="sub">${esc(lvjob(c))}</div><div class="badges">${badges.join("")}</div></div></div>
    ${p && !r.is_rep ? `<div class="alts"><div class="ttl">유니온 대표 캐릭터</div>
      <div class="chars">${charChip(p, r.dual)}</div>${same}</div>` : same ? `<div class="alts">${same}</div>` : ""}
  </article>`;
}

function renderLookup(res) {
  const dual = res.results.filter((r) => r.dual).length;
  $("#lookupResult").innerHTML = `
    <div class="sub" style="margin:4px 2px 12px">${esc(res.world)} · ${esc(res.date)} 기준 · ${res.results.length}명 조회${dual ? ` · <b style="color:var(--warn)">이중길드 ${dual}명</b>` : ""}</div>
    <div class="acc-grid">${res.results.map(lookupCard).join("")}</div>`;
}

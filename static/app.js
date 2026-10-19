// 우리 길드원 관리 — 화면: 길드원 검색 / 캐릭터명 검색 / 길드 검색 / 설정
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && (data.login || data.setup)) boot();  // 로그인이 풀렸거나 아직 설정 전 → 처음 화면으로
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
  $("#themeBtn").textContent = currentTheme() === "dark" ? "라이트" : "다크";
}
$("#themeBtn").onclick = () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("gc_theme", next);
  paintThemeBtn();
};
paintThemeBtn();

// ── 바깥 링크 / 길드 검색 링크 ──
const meaegiChar = (n) => `https://meaegi.com/s/${encodeURIComponent(n)}`;
const meaegiGuild = (g, w) => `https://meaegi.com/g/${encodeURIComponent(g)}/${encodeURIComponent(w)}`;
const guildHash = (w, g, focus) => `#guild?w=${encodeURIComponent(w || "")}&g=${encodeURIComponent(g)}${focus ? `&f=${encodeURIComponent(focus)}` : ""}`;

// 의심 캐릭터 밑에 붙는 버튼: [길드 검색] [메애기 캐릭터] [메애기 길드]
function suspectLinks(name, guild, world, focus) {
  const b = [];
  if (guild) b.push(`<a class="lnk main" href="${guildHash(world, guild, focus)}">「${esc(guild)}」 길드 검색</a>`);
  if (name) b.push(`<a class="lnk" href="${meaegiChar(name)}" target="_blank" rel="noopener">메애기: ${esc(name)}</a>`);
  if (guild && world) b.push(`<a class="lnk" href="${meaegiGuild(guild, world)}" target="_blank" rel="noopener">메애기: ${esc(guild)}</a>`);
  return `<div class="links">${b.join("")}</div>`;
}

// 다른 길드 대조 결과
const CHECK = {
  confirmed: ["dual", "포착", "그 길드의 길드원 목록에도 있어요"],
  stale: ["gold", "이미 나간 것으로 보임", "캐릭터 정보엔 이 길드로 나오지만 그 길드의 길드원 목록엔 없어요. 캐릭터 정보는 그 캐릭터로 접속해야 갱신돼서 옛날 길드가 남아 있는 거예요 — 확실히 하려면 그 캐릭터로 한 번 접속해 달라고 해 주세요"],
  unknown: ["gray", "확인 불가", "그 길드의 길드원 목록을 받지 못했어요 — 메애기에서 직접 확인해 주세요"],
};
function checkLine(c, name) {
  const [cls, label, text] = CHECK[c.status] || CHECK.unknown;
  return `<div class="check ${cls}"><b>${label}</b> <span>${esc(name)} → 「${esc(c.guild)}」 · ${text}</span></div>`;
}

// ── 화면 전환 ──
const VIEWS = ["members", "chars", "guild", "settings", "changelog"];
const OLD = { "#check": "members", "#history": "members", "#lookup": "chars" };
function route() {
  const [h, q] = location.hash.split("?");
  return { view: OLD[h] || VIEWS.find((v) => "#" + v === h) || "members", params: new URLSearchParams(q || "") };
}
function showView() {
  const { view, params } = route();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === view));
  for (const v of VIEWS) $("#view-" + v).hidden = v !== view;
  if (view === "members") loadHistory();
  if (view === "changelog") loadChangelog();
  if (view === "guild" && params.get("g")) {
    // 의심 결과의 링크로 들어옴 → 채우고 바로 검색
    if (params.get("w")) $("#gWorld").value = params.get("w");
    $("#gName").value = params.get("g");
    $("#gFocus").value = params.get("f") || "";
    history.replaceState(null, "", "#guild");
    startGuildSearch();
  }
  if (!showView.keepScroll) window.scrollTo(0, 0);
  showView.keepScroll = false;
}
window.addEventListener("hashchange", showView);

// ── 설정 ──
let cfg = {};
function paintSettings() {
  $("#sWorld").value = cfg.world || "";
  $("#sGuild").value = cfg.guild || "";
  $("#sModel").value = cfg.claude_model;
  const keyNow = (k, env) => (k ? `지금 키: <code>${esc(k)}</code>${env ? " <span class='muted'>(.env에서 읽음)</span>" : ""}
      <button type="button" class="ghost small" data-clear="${env ? "" : "1"}">지우기</button>` : `<span class="muted">아직 없어요</span>`);
  $("#nexonNow").innerHTML = keyNow(cfg.nexon_key, cfg.nexon_from_env);
  $("#claudeNow").innerHTML = cfg.claude_source === "shared"
    ? `<span class="badge ok">공용 키 사용 중</span> <span class="muted">길드에서 같이 쓰는 키예요. 개인 키를 아래에 저장하면 그걸 먼저 써요.</span>`
    : cfg.claude_source === "personal" && me.mode === "shared"
      ? `<span class="badge acc-c">개인 키 사용 중</span> <code>${esc(cfg.claude_key)}</code>
         <button type="button" class="ghost small" data-clear="1">개인 키 지우기 (공용 키로)</button>`
      : keyNow(cfg.claude_key, cfg.claude_from_env);
  $("#claudeLabel").textContent = me.mode === "shared" ? "개인 키 넣기" : "새 키 넣기";
  $("#claudeHint").textContent = me.mode === "shared"
    ? (cfg.claude_source === "personal" ? "(바꿀 때만)" : "(넣으면 공용 키 대신 이 키를 써요)")
    : "(바꿀 때만)";
  $("#nexonNow").querySelector("[data-clear]")?.addEventListener("click", () => clearKey("nexon_key", cfg.nexon_from_env));
  $("#claudeNow").querySelector("[data-clear]")?.addEventListener("click", () => clearKey("claude_key", cfg.claude_from_env));
  const ours = cfg.guild ? `${esc(cfg.guild)} <small>${esc(cfg.world)}</small>` : `<span class="muted">설정에서 정해 주세요</span>`;
  $("#ourGuild").innerHTML = ours;
  $("#ourGuild2").innerHTML = ours;
  $("#needSetup").hidden = !!(cfg.nexon_key && cfg.guild && cfg.world);
  if (!$("#gWorld").dataset.touched && cfg.world) $("#gWorld").value = cfg.world;
}
async function loadSettings() {
  cfg = await api("/api/settings").catch(() => ({}));
  paintSettings();
  paintAccount();
}

// ── 계정 (같이 쓰기) ──
let me = { mode: null, user: null };
function paintAccount() {
  const shared = me.mode === "shared";
  $("#whoAmI").hidden = !shared;
  $("#whoName").textContent = shared && me.user ? `${me.user.username}${me.user.is_admin ? " (관리자)" : ""}` : "";
  $("#accountBox").hidden = !shared;
  $("#soloBox").hidden = me.mode !== "solo";
  $("#adminBox").hidden = !(shared && me.user?.is_admin);
  $("#meLabel").textContent = shared && me.user ? `— ${me.user.username}` : "";
  $("#saveHint").innerHTML = shared
    ? "이 설정은 <b>내 계정에만</b> 저장돼요(다른 운영진은 각자 넣어요). 키는 화면에 앞뒤 몇 글자만 보여줘요."
    : "설정은 서버의 <code>data/guild.db</code>에 저장돼서 다음에도 그대로 유지돼요. 키는 화면에 앞뒤 몇 글자만 보여줘요. 이 파일엔 키가 들어 있으니 남에게 주지 마세요.";
  if (shared && me.user?.is_admin) loadUsers();
  $("#clBox").hidden = !me.user?.is_admin;  // 혼자 쓰기면 나, 같이 쓰기면 관리자
  if (me.user?.is_admin) loadClAdmin();
}
async function loadUsers() {
  const list = await api("/api/users").catch(() => []);
  paintUsers(list);
}
function paintUsers(list) {
  $("#userList").innerHTML = list.map((u) => `<div class="user-row">
      <b>${esc(u.username)}</b>${u.is_admin ? `<span class="badge acc-c">관리자</span>` : ""}
      <span class="muted small">${esc(u.created_at.slice(0, 10))} 추가</span>
      <span class="sp"></span>
      <button type="button" class="ghost small" data-reset="${u.id}" data-name="${esc(u.username)}">비밀번호 초기화</button>
      ${u.is_admin ? "" : `<button type="button" class="danger small" data-deluser="${u.id}" data-name="${esc(u.username)}">삭제</button>`}
    </div>`).join("");
  $("#userList").querySelectorAll("[data-reset]").forEach((b) => (b.onclick = async () => {
    const pw = prompt(`'${b.dataset.name}'의 새 비밀번호 (6글자 이상)`);
    if (!pw) return;
    try { await api(`/api/users/${b.dataset.reset}/password`, { method: "POST", body: JSON.stringify({ password: pw }) }); toast("바꿨어요. 새 비밀번호를 알려 주세요."); }
    catch (err) { toast(err.message, true); }
  }));
  $("#userList").querySelectorAll("[data-deluser]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`'${b.dataset.name}' 계정을 지울까요? 그 계정의 설정·검색 기록도 같이 지워져요.`)) return;
    try { paintUsers(await api(`/api/users/${b.dataset.deluser}`, { method: "DELETE" })); toast("지웠어요"); }
    catch (err) { toast(err.message, true); }
  }));
}
$("#addUserForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    paintUsers(await api("/api/users", { method: "POST", body: JSON.stringify({ username: $("#nuId").value.trim(), password: $("#nuPw").value }) }));
    toast(`'${$("#nuId").value.trim()}' 계정을 만들었어요. 아이디·비밀번호를 알려 주세요.`);
    $("#nuId").value = ""; $("#nuPw").value = "";
  } catch (err) { toast(err.message, true); }
};
$("#pwForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/me/password", { method: "POST", body: JSON.stringify({ current: $("#pwNow").value, new: $("#pwNew").value }) });
    $("#pwNow").value = ""; $("#pwNew").value = "";
    toast("비밀번호를 바꿨어요");
  } catch (err) { toast(err.message, true); }
};
$("#logoutBtn").onclick = async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  location.href = "/";
};
$("#toShared").onclick = () => { if (confirm("다른 사람과 같이 쓰기로 바꿀까요? 관리자 계정을 만들면 모두 로그인해야 해요.")) showGate("admin"); };

// ── 업데이트 내역 ──
const postMd = (p) => `## ${p.version} — ${p.date || ""}\n${p.body || ""}`;
// CHANGELOG.md 모양을 화면용으로 (## 버전, ### 소제목, - 목록, **굵게**, `코드`)
function mdToHtml(md) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
  let html = "", inList = false, open = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of md.replace(/<!--[\s\S]*?-->/g, "").split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (line.startsWith("# ")) continue;
    if (line.startsWith("## ")) {
      closeList();
      if (open) html += "</section>";
      const [ver, ...rest] = line.slice(3).split(" — ");
      html += `<section class="card cl-ver"><div class="cl-head"><h3 class="cl-title">${inline(ver)}</h3><span class="muted small">${inline(rest.join(" — "))}</span></div>`;
      open = true;
    } else if (line.startsWith("### ")) {
      closeList();
      html += `<h4 class="cl-sub">${inline(line.slice(4))}</h4>`;
    } else if (line.startsWith("- ")) {
      if (!inList) { html += `<ul class="cl-list">`; inList = true; }
      html += `<li>${inline(line.slice(2))}</li>`;
    } else if (line.trim()) {
      closeList();
      html += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  if (open) html += "</section>";
  return html;
}
// 위쪽 버전 표시 + 아직 안 본 새 버전이면 점
async function markNewVersion() {
  const r = await fetch("/api/version").then((x) => x.json()).catch(() => ({ version: "", posts: [] }));
  $("#verChip").hidden = !r.version;
  $("#verText").textContent = r.version;
  $("#verChip .new-dot").hidden = !r.version || store.get("gc_seen_version") === r.version;
  return r;
}
async function loadChangelog() {
  const r = await markNewVersion();
  $("#clPosts").innerHTML = r.posts?.length ? r.posts.map((p) => mdToHtml(postMd(p))).join("")
    : `<div class="empty">아직 올라온 업데이트 내역이 없어요</div>`;
  if (r.version) { store.set("gc_seen_version", r.version); $("#verChip .new-dot").hidden = true; }
}
const CL_STATUS = { draft: ["초안", "gray"], saved: ["저장만 함", "gold"], published: ["게시 중", "ok"], hidden: ["숨김", "dual"] };
let clItems = [];
async function loadClAdmin() {
  try { paintClAdmin(await api("/api/changelog")); }
  catch (err) { $("#clAdmin").innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}
function paintClAdmin(items) {
  clItems = items;
  $("#clAdmin").innerHTML = items.map((it) => {
    const cur = it.post || it.draft || {};
    const [label, cls] = CL_STATUS[it.status];
    return `<div class="cl-item" data-v="${esc(it.version)}">
      <div class="cl-item-head"><b class="cl-title">${esc(it.version)}</b><span class="badge ${cls}">${label}</span>
        ${it.post && it.draft && it.post.body !== it.draft.body ? `<span class="muted small">초안과 문구 다름</span>` : ""}
        <span class="sp"></span><input type="date" data-k="date" value="${esc((cur.date || "").slice(0, 10))}"></div>
      <textarea data-k="body" rows="${Math.min(10, (cur.body || "").split("\n").length + 1)}">${esc(cur.body || "")}</textarea>
      <div class="cl-actions">
        ${it.draft ? `<button type="button" class="ghost" data-act="load">초안 다시 불러오기</button>` : ""}
        <button type="button" class="ghost" data-act="preview">미리보기</button>
        <button type="button" class="ghost" data-act="save">저장 (게시 안 함)</button>
        ${it.status === "published" ? `<button type="button" data-act="hide">숨기기</button>`
          : `<button type="button" class="primary" data-act="publish">${it.status === "hidden" ? "다시 보이기" : "게시"}</button>`}
        ${it.post ? `<button type="button" class="danger" data-act="del">게시 취소·문구 삭제</button>` : ""}</div>
      <div class="cl-preview" hidden></div></div>`;
  }).join("") || `<div class="empty">초안이 없어요</div>`;
}
$("#clAdmin").onclick = async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const box = b.closest(".cl-item"), v = box.dataset.v, it = clItems.find((x) => x.version === v);
  const body = box.querySelector("[data-k=body]").value, date = box.querySelector("[data-k=date]").value;
  const url = `/api/changelog/${encodeURIComponent(v)}`;
  const put = async (extra, msg) => {
    try { paintClAdmin(await api(url, { method: "PUT", body: JSON.stringify({ body, date, ...extra }) })); toast(msg); markNewVersion(); }
    catch (err) { toast(err.message, true); }
  };
  const act = b.dataset.act;
  if (act === "load") {
    box.querySelector("[data-k=body]").value = it.draft.body;
    box.querySelector("[data-k=date]").value = (it.draft.date || "").slice(0, 10);
    toast("초안을 불러왔어요 (아직 저장 안 됨)");
  }
  if (act === "preview") {
    const pv = box.querySelector(".cl-preview");
    pv.hidden = !pv.hidden;
    pv.innerHTML = mdToHtml(postMd({ version: v, date, body }));
  }
  if (act === "save") put({}, "저장했어요");
  if (act === "publish") put({ published: 1, hidden: 0 }, `${v} 게시했어요`);
  if (act === "hide") put({ hidden: 1 }, `${v} 숨겼어요`);
  if (act === "del" && confirm(`${v}의 게시와 고친 문구를 지울까요? (초안은 남아요)`)) {
    try { paintClAdmin(await api(url, { method: "DELETE" })); markNewVersion(); } catch (err) { toast(err.message, true); }
  }
};
$("#clNew").onclick = async () => {
  const v = (prompt("새 버전 이름 (예: v1.3)") || "").trim();
  if (!v) return;
  try { paintClAdmin(await api(`/api/changelog/${encodeURIComponent(v)}`, { method: "PUT", body: JSON.stringify({ body: "- " }) })); }
  catch (err) { toast(err.message, true); }
};

// ── 처음 화면 (사용 방식 고르기 / 관리자 만들기 / 로그인) ──
function showGate(step) {
  $("#gate").hidden = false;
  $("#app").hidden = true;
  $("#gateChoose").hidden = step !== "choose";
  $("#gateAdmin").hidden = step !== "admin";
  $("#gateLogin").hidden = step !== "login";
  document.querySelector("header nav").hidden = true;
  $("#whoAmI").hidden = true;
  (step === "admin" ? $("#adId") : step === "login" ? $("#lgId") : null)?.focus();
}
document.querySelectorAll(".choice").forEach((b) => (b.onclick = async () => {
  if (b.dataset.mode === "shared") return showGate("admin");
  try { await api("/api/setup", { method: "POST", body: JSON.stringify({ mode: "solo" }) }); location.hash = "#settings"; boot(); }
  catch (err) { toast(err.message, true); }
}));
$("#adBack").onclick = () => (me.mode === "solo" ? boot() : showGate("choose"));
$("#gateAdmin").onsubmit = async (e) => {
  e.preventDefault();
  if ($("#adPw").value !== $("#adPw2").value) return toast("비밀번호 두 칸이 달라요.", true);
  try {
    await api("/api/setup", { method: "POST", body: JSON.stringify({ mode: "shared", username: $("#adId").value.trim(), password: $("#adPw").value }) });
    $("#adPw").value = $("#adPw2").value = "";
    location.hash = "#settings";
    toast("관리자 계정을 만들었어요. '설정'에서 API 키와 길드를 넣고, 아래에서 운영진 계정을 추가하세요.");
    boot();
  } catch (err) { toast(err.message, true); }
};
$("#gateLogin").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ username: $("#lgId").value.trim(), password: $("#lgPw").value }) });
    location.reload();
  } catch (err) { toast(err.message, true); }
};
async function clearKey(k, fromEnv) {
  if (fromEnv) return toast(".env 파일에 있는 키예요. .env에서 지워 주세요.", true);
  if (!confirm(k === "claude_key" && me.mode === "shared" ? "개인 키를 지우고 공용 키를 쓸까요?" : "이 키를 지울까요?")) return;
  cfg = await api("/api/settings", { method: "POST", body: JSON.stringify({ ["clear_" + k]: true }) });
  paintSettings();
  toast("지웠어요");
}
$("#settingsForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    cfg = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ world: $("#sWorld").value, guild: $("#sGuild").value, claude_model: $("#sModel").value,
        nexon_key: $("#sNexon").value, claude_key: $("#sClaude").value }),
    });
    $("#sNexon").value = ""; $("#sClaude").value = "";
    paintSettings();
    toast("저장했어요");
  } catch (err) { toast(err.message, true); }
};
$("#testBtn").onclick = async () => {
  try {
    const r = await api("/api/settings/test", { method: "POST" });
    toast(`찾았어요: 「${r.guild}」 길드원 ${r.members}명 · 마스터 ${r.master}`);
  } catch (err) { toast(err.message, true); }
};
$("#gWorld").onchange = () => ($("#gWorld").dataset.touched = "1");

// ── 검색 작업 (길드원 검색·길드 검색 공통, 한 번에 하나) ──
$("#activity").checked = store.get("gc_activity") !== "0";
if (store.get("gc_reverse") !== null) $("#reverse").value = store.get("gc_reverse");
else $("#reverse").value = "200";  // 기본: 꽤 깊게 (길드원 검색은 API를 많이 안 먹는다)

$("#scanForm").onsubmit = async (e) => {
  e.preventDefault();
  store.set("gc_reverse", $("#reverse").value);
  store.set("gc_activity", $("#activity").checked ? "1" : "0");
  startJob({ mode: "ours", reverse_top: Number($("#reverse").value), activity: $("#activity").checked });
};
$("#guildForm").onsubmit = (e) => { e.preventDefault(); startGuildSearch(); };
function startGuildSearch() {
  const focus = $("#gFocus").value.split(",").map((x) => x.trim()).filter(Boolean);
  startJob({ mode: "other", world: $("#gWorld").value, guild: $("#gName").value.trim(), focus });
}

async function startJob(body) {
  setBusy(true);
  try { poll(await api("/api/scan", { method: "POST", body: JSON.stringify(body) })); }
  catch (err) { toast(err.message, true); setBusy(false); }
}
function setBusy(b) { $("#scanBtn").disabled = b; $("#gBtn").disabled = b; }

let pollTimer = null;
function poll(job) {
  renderProgress(job);
  clearTimeout(pollTimer);
  if (job.status !== "running") return finish(job);
  pollTimer = setTimeout(async () => {
    try { poll(await api(`/api/jobs/${job.id}`)); }
    catch (err) { toast("진행 상황을 못 받았어요: " + err.message, true); setBusy(false); }
  }, 1000);
}

function renderProgress(j) {
  const p = j.mode === "other" ? $("#gProgress") : $("#progress");
  p.hidden = false;
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  const steps = Array.from({ length: j.steps }, (_, i) => `<span class="${i < j.step ? "done" : ""}"></span>`).join("");
  p.innerHTML = `
    <div class="phase"><span>${esc(j.guild)} · ${esc(j.world)} — ${esc(j.phase)}</span>
      <span>${j.total ? `${j.done.toLocaleString()} / ${j.total.toLocaleString()} (${pct}%)` : ""}</span></div>
    <div class="bar ${j.total ? "" : "wait"}"><i style="width:${pct}%"></i></div>
    <div class="row-end"><div class="steps" title="단계 ${j.step}/${j.steps}">${steps}</div>
      <span class="muted">단계 ${j.step} / ${j.steps}</span>
      <button type="button" class="danger" data-cancel>멈추기</button></div>`;
  p.querySelector("[data-cancel]").onclick = async (e) => {
    e.target.disabled = true;
    await api(`/api/jobs/${j.id}/cancel`, { method: "POST" }).catch(() => {});
  };
}

async function finish(job) {
  setBusy(false);
  $("#progress").hidden = true;
  $("#gProgress").hidden = true;
  if (job.status === "done") {
    toast("검색 끝!");
    if (job.mode === "other") return renderGuild(job.result);
    await openScan(job.scan_id);
    loadHistory();
  } else {
    toast(job.error || "검색이 멈췄어요.", job.status === "error");
  }
}

// ── 검색 결과 (길드원 검색 → #result, 길드 검색 → #guildResult) ──
const panes = {
  ours: { box: "#result", data: null, filter: "all", query: "" },
};

async function openScan(id, { scroll = true } = {}) {
  let data;
  try { data = await api(`/api/scans/${id}`); } catch (err) { return toast(err.message, true); }
  const mode = "ours";
  const pane = panes[mode];
  Object.assign(pane, { data, filter: "all", query: "" });
  store.set(lastKey(), id);
  if (location.hash.split("?")[0] !== "#members") { showView.keepScroll = true; location.hash = "#members"; }
  renderPane(mode);
  if (scroll) $(pane.box).scrollIntoView({ behavior: "smooth", block: "start" });
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

function accCard(a, ctx) {
  const r = a.rep;
  const flags = [];
  if (a.forward) flags.push(`본캐(대표)가 「${esc(r.guild)}」 길드`);
  if (a.reverse) flags.push(`부캐 ${a.others.length}명이 다른 길드`);
  const badges = [];
  if (a.rep_in_ours) badges.push(`<span class="badge ok">본캐 ${ctx.other ? "이 길드" : "우리 길드"}</span>`);
  else if (a.forward) badges.push(`<span class="badge dual">본캐 · ${esc(r.guild)}</span>`);
  else if (a.rep_guildless) badges.push(`<span class="badge gold">본캐 길드 없음</span>`);
  else badges.push(`<span class="badge gold">본캐 정보 없음</span>`);
  if (a.union_level) badges.push(`<span class="badge acc-c">유니온 ${Number(a.union_level).toLocaleString()}</span>`);
  if (a.stale30) badges.push(`<span class="badge sky">30일 성장 없음</span>`);
  if (r.away7) badges.push(`<span class="badge sky">7일 미접속</span>`);
  if (r.guild !== null && !r.fresh) badges.push(`<span class="badge gray" title="최신 정보를 못 받아 어제 기준으로 봤어요">어제 기준</span>`);
  const focus = ctx.focus.has(r.name);
  if (focus) badges.unshift(`<span class="badge focus">찾는 계정</span>`);

  const ours = a.ours.filter((c) => !(a.rep_in_ours && c.name === r.name));
  let alts = "";
  if (ours.length) {
    alts += `<div class="ttl">${a.rep_in_ours ? (ctx.other ? "이 길드 부캐" : "우리 길드 부캐") : (ctx.other ? "이 길드에 있는 캐릭터" : "우리 길드에 있는 캐릭터")} ${ours.length}</div>
      <div class="chars">${ours.map((c) => charChip(c)).join("")}</div>`;
  }
  if (a.others.length) {
    const gs = [...new Set(a.others.map((c) => c.guild))];
    alts += `<div class="ttl red" style="margin-top:${ours.length ? 10 : 2}px">다른 길드에 있는 부캐 ${a.others.length} <span class="muted">(그 길드 목록에서 확인 · 포착)</span></div>
      <div class="chars">${a.others.map((c) => charChip(c, true)).join("")}</div>
      ${gs.map((g) => suspectLinks("", g, ctx.world, a.others.filter((c) => c.guild === g).map((c) => c.name).join(","))).join("")}`;
  }
  let check = "";
  if (a.left_guild) {
    check = checkLine(a.check, r.name);
  } else if (a.forward) {
    check = (a.check ? checkLine(a.check, r.name) : "") + suspectLinks(r.name, r.guild, ctx.world, r.name);
  } else if (r.guild === null || (!r.fresh && a.dual)) {
    check = `<div class="check gray"><b>최신 정보 없음</b> <span>메애기에서 직접 확인해 주세요</span></div>${suspectLinks(r.name, "", ctx.world)}`;
  }
  return `<article class="acc ${a.dual ? "is-dual" : ""} ${focus ? "is-focus" : ""}">
    ${a.dual ? `<div class="flag">의심 <span>— ${flags.join(" · ")}</span></div>` : ""}
    <div class="main">${avatar(r)}
      <div class="who"><div class="sub">대표 캐릭터</div><div class="name">${esc(r.name)}</div>
        <div class="sub">${esc(lvjob(r))}</div><div class="badges">${badges.join("")}</div></div></div>
    ${check ? `<div class="checks">${check}</div>` : ""}
    ${alts ? `<div class="alts">${alts}</div>` : ""}
  </article>`;
}

function matches(a, pane) {
  const f = pane.filter;
  if (f === "dual" && !a.dual) return false;
  if (f === "multi" && a.ours.length + a.others.length < 2) return false;
  if (f === "stale" && !a.stale30 && !a.rep.away7) return false;
  if (f === "focus" && !(pane.focus || new Set()).has(a.rep.name)) return false;
  if (!pane.query) return true;
  const q = pane.query.toLowerCase();
  return [a.rep, ...a.ours, ...a.others].some((c) => (c.name || "").toLowerCase().includes(q) || (c.guild || "").toLowerCase().includes(q));
}

function renderPane(mode) {
  const pane = panes[mode];
  const box = $(pane.box);
  const cur = pane.data;
  if (!cur) { box.innerHTML = welcome(); return; }
  const res = cur.result, s = res.summary, g = res.guild, d = cur.diff, o = cur.options || {};
  const revTxt = { 0: "끔", "-1": "전체 길드" }[o.reverse_top] ?? `상위 ${o.reverse_top}개 길드`;

  let diffHtml = "";
  if (d) {
    const list = (arr) => (arr.length ? arr.map(esc).join(", ") : "<span class='muted'>없음</span>");
    diffHtml = `<div class="card"><h3 style="margin-bottom:10px">지난 검색(${esc(d.prev_date)} 기준)과 비교</h3>
      <div class="diff">
        <div class="new"><b>새로 생긴 의심</b>${list(d.new_dual)}</div>
        <div class="gone"><b>의심 풀림</b>${list(d.gone_dual)}</div>
        <div><b>새로 들어온 캐릭터</b>${list(d.joined)}</div>
        <div><b>나간 캐릭터</b>${list(d.left)}</div>
      </div></div>`;
  }
  const chips = [["all", "전체"], ["dual", "의심만"], ["multi", "부캐 있는 계정"]];
  if (s.activity_on) chips.push(["stale", "성장 없음·미접속"]);
  chips.push(["unknown", "판별 불가"]);
  const deep = (s.deep_guilds || []);

  box.innerHTML = `
    <div class="res-head">
      <div><h2>${esc(g.name)}<small>${esc(g.world)}${g.level ? ` · Lv.${g.level}` : ""}</small></h2>
        <div class="meta">${g.fresh ? "최신 정보로 갱신해서 봤어요" : `${esc(g.date)} 데이터 기준`} · 유니온 대표는 ${esc(g.date)} 랭킹 기준 · 길드 마스터 ${esc(g.master || "?")} · ${esc(cur.created_at.replace("T", " "))}</div></div>
      <div class="meta">역방향: ${revTxt}${s.other_guilds ? ` · 다른 길드 ${s.other_guilds}개(${(s.other_members || 0).toLocaleString()}명) 확인` : ""}<br>
        ${deep.length ? `심층 확인한 의심 길드: ${deep.map(esc).join(", ")}<br>` : ""}
        넥슨 API ${s.api_calls.toLocaleString()}번 · 캐시 ${s.cache_hits.toLocaleString()}번 · ${s.seconds}초</div>
    </div>
    <div class="tiles">
      <div class="card tile dual"><div class="label">의심 계정</div><div class="value">${s.dual}</div>
        <div class="sub">본캐 다른 길드 ${s.forward} · 부캐 다른 길드 ${s.reverse}</div></div>
      <div class="card tile caught"><div class="label">포착 / 나간 것으로 보임</div><div class="value">${s.confirmed ?? 0}<small> / ${s.stale ?? 0}</small></div>
        <div class="sub">다른 길드 목록과 대조</div></div>
      <div class="card tile real"><div class="label">실질 길드원 (계정)</div><div class="value">${s.accounts}</div>
        <div class="sub">부캐 있는 계정 ${s.multi}</div></div>
      <div class="card tile chars"><div class="label">캐릭터 수</div><div class="value">${s.characters}</div>
        <div class="sub">길드 목록 기준</div></div>
      <div class="card tile unk"><div class="label">판별 불가</div><div class="value">${s.unknown}</div>
        <div class="sub">유니온 랭킹에 없음 등</div></div>
      ${s.activity_on ? `<div class="card tile stale"><div class="label">30일 성장 없음</div><div class="value">${s.stale30}</div><div class="sub">대표 캐릭터 기준</div></div>` : ""}
    </div>
    ${diffHtml}
    <div class="toolbar">
      <div class="chips">${chips.map(([k, t]) => `<button type="button" data-f="${k}" class="${pane.filter === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <input data-search placeholder="캐릭터·길드 이름 찾기" value="${esc(pane.query)}">
    </div>
    <div data-list></div>`;

  box.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => {
    pane.filter = b.dataset.f;
    box.querySelectorAll("[data-f]").forEach((x) => x.classList.toggle("on", x === b));
    renderList(mode);
  }));
  // 입력칸은 다시 만들지 않는다 (다시 만들면 한글 조합이 끊겨 'ㅁㅜㄴㅣ'처럼 써짐)
  box.querySelector("[data-search]").addEventListener("input", (e) => {
    pane.query = e.target.value.trim();
    renderList(mode);
  });
  renderList(mode);
}

function renderList(mode) {
  const pane = panes[mode];
  const res = pane.data.result, s = res.summary, g = res.guild;
  const ctx = { world: g.world, focus: new Set(), other: false };
  const dual = res.accounts.filter((a) => a.dual && matches(a, pane));
  const normal = res.accounts.filter((a) => !a.dual && matches(a, pane));
  const unk = res.unknown.filter((u) => !pane.query || u.name.toLowerCase().includes(pane.query.toLowerCase()));
  const showUnk = pane.filter === "all" || pane.filter === "unknown";
  $(pane.box).querySelector("[data-list]").innerHTML = `
    ${pane.filter !== "unknown" ? `
      <div class="sec-title dual"><h3>의심</h3><span class="count">${dual.length}</span></div>
      ${dual.length ? `<div class="acc-grid">${dual.map((a) => accCard(a, ctx)).join("")}</div>` : `<div class="empty">${s.dual ? "조건에 맞는 계정이 없어요" : "의심되는 계정이 없어요"}</div>`}
      ${pane.filter !== "dual" ? `
        <div class="sec-title"><h3>실질 길드원</h3><span class="count">${normal.length}</span><span class="muted">대표 캐릭터 기준으로 묶은 계정</span></div>
        ${normal.length ? `<div class="acc-grid">${normal.map((a) => accCard(a, ctx)).join("")}</div>` : `<div class="empty">조건에 맞는 계정이 없어요</div>`}` : ""}` : ""}
    ${showUnk ? `
      <div class="sec-title"><h3>판별 불가</h3><span class="count">${unk.length}</span><span class="muted">어느 계정인지 알 수 없어서 따로 모았어요</span></div>
      ${unk.length ? `<div class="unk-grid">${unk.map((u) => `<div class="ch">${avatar(u, "sm")}<div class="t"><b>${esc(u.name)}</b>
        <span>${esc(u.reason)}${u.level ? ` · Lv.${u.level}` : ""}</span></div>
        <a class="lnk tiny" href="${meaegiChar(u.name)}" target="_blank" rel="noopener">메애기</a></div>`).join("")}</div>` : `<div class="empty">없어요</div>`}` : ""}`;
}

// ── 길드 검색 결과: 길드원 캐릭터 조회만 (의심 판정 없음) ──
function renderGuild(res) {
  const g = res.guild, s = res.summary;
  const focus = $("#gFocus").value.split(",").map((x) => x.trim()).filter(Boolean);
  const byName = new Map(res.members.map((m) => [m.name, m]));
  const focusHtml = focus.map((f) => {
    const m = byName.get(f);
    return m
      ? `<div class="verdict bad">「${esc(f)}」 이 길드 길드원 목록에 있어요<span>Lv.${m.level ?? "?"} ${esc(m.job || "")}</span></div>`
      : `<div class="verdict ok">「${esc(f)}」 이 길드 길드원 목록에 없어요
          <span>캐릭터 정보에 이 길드로 나와도 목록에 없으면 이미 나간 것으로 보면 돼요.</span></div>`;
  }).join("");
  const card = (m) => {
    const hl = focus.includes(m.name);
    const note = !m.found ? "캐릭터를 찾을 수 없음 (개명?)"
      : m.guild !== g.name ? `캐릭터 정보엔 ${m.guild ? `「${esc(m.guild)}」` : "길드 없음"} (정보가 옛날 것)` : esc(lvjob(m));
    return `<div class="ch member ${hl ? "focus" : ""}" data-name="${esc(m.name.toLowerCase())}">${avatar(m, "sm")}
      <div class="t"><b>${esc(m.name)}</b><span>${m.found && m.guild === g.name ? note : `${m.level ? `Lv.${m.level} · ` : ""}${note}`}</span></div>
      ${m.away7 ? `<span class="badge sky">7일 미접속</span>` : ""}
      <a class="lnk tiny" href="${meaegiChar(m.name)}" target="_blank" rel="noopener">메애기</a></div>`;
  };
  $("#guildResult").innerHTML = `
    ${focusHtml}
    <div class="res-head">
      <div><h2>${esc(g.name)}<small>${esc(g.world)}${g.level ? ` · Lv.${g.level}` : ""}</small></h2>
        <div class="meta">최신 길드원 목록 · 길드 마스터 ${esc(g.master || "?")} · 길드원 ${s.characters}명</div></div>
      <div class="meta"><a class="lnk" href="${meaegiGuild(g.name, g.world)}" target="_blank" rel="noopener">메애기에서 보기</a><br>
        넥슨 API ${s.api_calls.toLocaleString()}번 · ${s.seconds}초</div>
    </div>
    <div class="toolbar"><input data-gsearch placeholder="캐릭터 이름 찾기"><span class="muted small" data-gcount>${s.characters}명</span></div>
    <div class="unk-grid member-grid">${res.members.map(card).join("")}</div>`;
  const items = [...$("#guildResult").querySelectorAll(".member")];
  // 입력칸은 그대로 두고 카드만 숨김 → 한글 조합이 안 끊김
  $("#guildResult").querySelector("[data-gsearch]").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    let n = 0;
    for (const el of items) { el.hidden = !!q && !el.dataset.name.includes(q); if (!el.hidden) n++; }
    $("#guildResult").querySelector("[data-gcount]").textContent = `${n}명`;
  });
  $("#guildResult").scrollIntoView({ behavior: "smooth", block: "start" });
}

function welcome() {
  return `<div class="welcome">
    <div class="card"><div class="num">1</div><h3>설정</h3><p>'설정' 탭에서 넥슨 API 키와 우리 길드를 한 번만 정해 두면 계속 유지돼요.</p></div>
    <div class="card"><div class="num">2</div><h3>검색</h3><p>길드원 정보를 최신으로 갱신하고, 계정마다 본캐를 찾아 다른 길드에 있는지 봐요.</p></div>
    <div class="card"><div class="num">3</div><h3>의심 확인</h3><p>다른 길드 목록과 대조해서 포착 / 정보 갱신 필요를 가려요. 길드 검색·메애기로 바로 이어져요.</p></div>
  </div>`;
}

// ── 지난 기록 (길드원 검색 탭 아래) ──
async function loadHistory() {
  const box = $("#historyList");
  let list;
  try { list = await api("/api/scans"); } catch (err) { return; }
  if (!list.length) { box.innerHTML = `<div class="empty">아직 검색한 기록이 없어요</div>`; return; }
  box.innerHTML = list.map((h) => {
    const s = h.summary || {}, o = h.options || {};
    const other = o.mode === "other";
    return `<div class="card hist" data-id="${h.id}">
      <div class="top-line"><span class="g">${esc(h.guild)}</span><span class="muted">${esc(h.world)}</span>
        ${other ? `<span class="badge gray">길드 검색</span>` : ""}</div>
      <div class="muted">${esc(h.created_at.replace("T", " "))}</div>
      <div class="nums">
        <span class="badge dual">의심 ${s.dual ?? "?"}</span>
        <span class="badge ok">계정 ${s.accounts ?? "?"}</span>
        <span class="badge">캐릭터 ${s.characters ?? "?"}</span>
        <span class="badge gold">판별 불가 ${s.unknown ?? "?"}</span>
      </div>
      <div class="foot"><span class="muted">${other ? (o.focus?.length ? `찾는 계정 ${o.focus.map(esc).join(", ")}` : "") : `역방향 ${o.reverse_top ? (o.reverse_top < 0 ? "전체" : "상위 " + o.reverse_top) : "끔"}`}</span>
        <button type="button" class="danger" data-del="${h.id}">삭제</button></div>
    </div>`;
  }).join("");
  box.querySelectorAll(".hist").forEach((el) => (el.onclick = (e) => {
    if (e.target.dataset.del) return;
    openScan(el.dataset.id);
  }));
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("이 검색 기록을 지울까요?")) return;
    await api(`/api/scans/${b.dataset.del}`, { method: "DELETE" });
    for (const m of ["ours"]) {
      if (panes[m].data && String(panes[m].data.id) === b.dataset.del) { panes[m].data = null; renderPane(m); }
    }
    loadHistory();
  }));
}

// ── 캐릭터명 검색 (스크린샷 → 닉네임 → 검수 → 조회) ──
async function runLookup() {
  const btn = $("#lkBtn");
  btn.disabled = true;
  btn.textContent = "조회 중…";
  try {
    renderLookup(await api("/api/lookup", { method: "POST", body: JSON.stringify({ names: $("#lkNames").value }) }));
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "④ 조회";
  }
}
$("#lookupForm").onsubmit = (e) => { e.preventDefault(); runLookup(); };

// 스크린샷 크기 맞추기: Claude가 받을 수 있는 최대(긴 변 2576px, 375만 화소)까지 원본 그대로 —
// 작은 스크린샷은 키워서 보낸다(최대 2배). 닉네임 글자가 작아서 모음 획(ㅗ/ㅛ) 하나가 잘 안 보이기 때문.
function shrink(file) {
  return new Promise((ok, fail) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(2, 2576 / Math.max(img.width, img.height), Math.sqrt(3.75e6 / (img.width * img.height)));
      URL.revokeObjectURL(img.src);
      if (Math.abs(k - 1) < 0.02 && file.size < 4.5e6 && /png|jpeg/.test(file.type)) return ok(file);  // 그대로
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      const g = c.getContext("2d");
      g.imageSmoothingQuality = "high";
      g.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((b) => (b ? ok(b) : fail(new Error("이미지를 줄이지 못했어요"))), "image/jpeg", 0.95);
    };
    img.onerror = () => fail(new Error("이미지를 열지 못했어요"));
    img.src = URL.createObjectURL(file);
  });
}

let shots = [];        // [{file, url}]
let reading = false;
let readInfo = null;

function paintShots() {
  $("#thumbs").innerHTML = shots.map((s, i) =>
    `<div class="thumb"><img src="${s.url}" alt="" data-view="${i}"><button type="button" data-rm="${i}" title="빼기">×</button></div>`).join("");
  $("#readBtn").disabled = !shots.length || reading;
  $("#readBtn").textContent = shots.length ? `② 닉네임 읽기 (${shots.length}장)` : "② 닉네임 읽기";
  $("#clearShots").hidden = !shots.length;
}

function addShots(files) {
  files = [...files].filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  const room = 10 - shots.length;
  if (files.length > room) toast(`스크린샷은 10장까지예요. ${Math.max(room, 0)}장만 넣었어요.`, true);
  shots.push(...files.slice(0, Math.max(room, 0)).map((f) => ({ file: f, url: URL.createObjectURL(f) })));
  paintShots();
}

$("#thumbs").onclick = (e) => {
  e.stopPropagation();  // 그림을 눌러도 파일 고르기 창이 뜨지 않게
  if (e.target.dataset.rm !== undefined) {
    const [s] = shots.splice(Number(e.target.dataset.rm), 1);
    URL.revokeObjectURL(s.url);
    paintShots();
  } else if (e.target.dataset.view !== undefined) {
    const v = document.createElement("div");
    v.className = "viewer";
    v.innerHTML = `<img src="${shots[Number(e.target.dataset.view)].url}" alt="">`;
    v.onclick = () => v.remove();
    document.body.append(v);
  }
};
$("#clearShots").onclick = () => { shots.forEach((s) => URL.revokeObjectURL(s.url)); shots = []; paintShots(); };

function countNames() {
  const n = new Set($("#lkNames").value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean)).size;
  $("#nameCount").textContent = n ? `${n}명` : "";
}
$("#lkNames").oninput = countNames;

$("#readBtn").onclick = async () => {
  if (!shots.length || reading) return;
  const have = $("#lkNames").value.trim();
  if (have && !confirm("텍스트 상자에 있는 이름을 지우고 새로 읽은 이름으로 바꿀까요?")) return;
  reading = true;
  paintShots();
  $("#readBtn").textContent = "읽는 중…";
  $("#readMsg").textContent = `${shots.length}장 읽는 중 (10~40초 정도)`;
  try {
    const fd = new FormData();
    for (const s of shots) { const b = await shrink(s.file); fd.append("images", b, b.type === "image/png" ? "shot.png" : "shot.jpg"); }
    const r = await fetch("/api/read-image", { method: "POST", body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `오류 ${r.status}`);
    const names = data.characters.map((c) => c.name);
    if (!names.length) throw new Error("이미지에서 닉네임을 찾지 못했어요. " + (data.note || ""));
    readInfo = data;
    $("#lkNames").value = names.join("\n");
    countNames();
    const unsure = data.characters.filter((c) => c.unsure).map((c) => c.name);
    const tips = [];
    if (unsure.length) tips.push(`확실하지 않은 이름: ${unsure.join(", ")} — 스크린샷과 꼭 비교해 주세요`);
    if (data.shown_count > names.length) tips.push(`핸즈에는 캐릭터 ${data.shown_count}개인데 ${names.length}개만 읽었어요 — 스크롤한 스크린샷을 더 넣거나 직접 추가해 주세요`);
    $("#unsure").hidden = !tips.length;
    $("#unsure").innerHTML = tips.map(esc).join("<br>");
    const u = data.usage;
    $("#readMsg").textContent = `${names.length}명 읽음 · ${data.note || ""}${u ? ` · ${u.model} 토큰 입력 ${u.input.toLocaleString()} / 출력 ${u.output.toLocaleString()}` : ""}`;
    $("#lkNames").focus();
  } catch (err) {
    $("#readMsg").textContent = "";
    toast(err.message, true);
  } finally {
    reading = false;
    paintShots();
  }
};

const drop = $("#drop");
const isTouch = matchMedia("(pointer: coarse)").matches;
// 폰: 갤러리 열기 / 컴: 사진 고르기·폴더째 넣기 (빈 곳을 눌러도 사진 고르기)
drop.onclick = (e) => { if (!isTouch && !e.target.closest("button, .thumb")) $("#lkFiles").click(); };
drop.onkeydown = (e) => { if (e.target === drop && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); $("#lkFiles").click(); } };
$("#pickGallery").onclick = $("#pickFiles").onclick = (e) => { e.stopPropagation(); $("#lkFiles").click(); };
$("#pickFolder").onclick = (e) => { e.stopPropagation(); $("#lkFolder").click(); };
$("#lkFiles").onchange = (e) => { addShots(e.target.files); e.target.value = ""; };
$("#lkFolder").onchange = (e) => {
  // 폴더 안 이미지만, 파일 이름순(스크린샷 찍은 순서)으로
  const files = [...e.target.files].filter((f) => f.type.startsWith("image/"))
    .sort((a, b) => a.name.localeCompare(b.name, "ko", { numeric: true }));
  if (!files.length) toast("그 폴더에 이미지가 없어요.", true);
  addShots(files);
  e.target.value = "";
};
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); addShots(e.dataTransfer.files); };
document.addEventListener("paste", (e) => {
  if ($("#view-chars").hidden) return;
  const files = [...(e.clipboardData?.items || [])].filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter(Boolean);
  if (files.length) { e.preventDefault(); addShots(files); }
});

function lookupCard(r, ours) {
  if (!r.found) {
    const sg = (r.suggest || []).map((x) => `<button type="button" class="fix ${x.same_account ? "same" : ""}" data-from="${esc(r.name)}" data-to="${esc(x.name)}">
        ${esc(x.name)} <small>${x.same_account ? "같은 계정 ✓" : `다른 계정${x.guild ? ` · ${esc(x.guild)}` : ""}`}${x.level ? ` · Lv.${x.level}` : ""}</small></button>`).join("");
    return `<article class="acc missing"><div class="main">${avatar(null)}<div class="who"><div class="name">${esc(r.name)}</div>
      <div class="sub">${esc(r.reason)}</div></div></div>
      <div class="checks">${suspectLinks(r.name, "", "")}</div>
      ${sg ? `<div class="alts"><div class="ttl">혹시 이 캐릭터? (누르면 이름을 바꿔요)</div><div class="fixes">${sg}</div></div>` : ""}</article>`;
  }
  const c = r.char, p = r.rep;
  const g = (x) => (x ? `「${esc(x)}」` : "길드 없음");
  const badges = [];
  if (r.left_guild) badges.push(`<span class="badge gold">${g(c.guild)} 나간 것으로 보임</span>`);
  else if (r.other_guild) badges.push(`<span class="badge dual">다른 길드 ${g(c.guild)}</span>`);
  else if (ours && c.guild === ours) badges.push(`<span class="badge ok">우리 길드</span>`);
  if (!p) badges.push(`<span class="badge gold">${esc(r.reason || "대표 캐릭터를 알 수 없음")}</span>`);
  else if (r.is_rep) badges.push(`<span class="badge ok">이 캐릭터가 대표(본캐)</span>`);
  else if (r.rep_other_guild) badges.push(`<span class="badge dual">대표는 ${g(p.guild)}</span>`);
  if (r.union_level) badges.push(`<span class="badge acc-c">유니온 ${Number(r.union_level).toLocaleString()}</span>`);
  if (c.access === "false") badges.push(`<span class="badge sky">7일 미접속</span>`);
  badges.push(r.fresh ? `<span class="badge ok">최신 갱신</span>` : `<span class="badge gray" title="최신 정보를 못 받아 어제 기준으로 봤어요">어제 기준</span>`);
  const why = [];
  if (r.other_guild) why.push(`이 캐릭터가 ${g(c.guild)}`);
  if (r.rep_other_guild) why.push(`대표(본캐)가 ${g(p.guild)}`);
  if (r.dual && !why.length) why.push(`이 캐릭터는 ${g(c.guild)}, 대표(본캐)는 ${g(p.guild)}`);
  // 대조 결과 + 길드 검색·메애기 링크
  const focus = p ? p.name : c.name;
  let checks = (r.checks || []).map((k) => checkLine(k, k.who) + suspectLinks(k.who, k.guild, r.world, k.who)).join("");
  if (!r.fresh && !checks) checks = `<div class="check gray"><b>최신 정보 없음</b> <span>메애기에서 직접 확인해 주세요</span></div>${suspectLinks(c.name, "", r.world)}`;
  return `<article class="acc ${r.dual ? "is-dual" : ""}">
    ${r.dual ? `<div class="flag">의심 <span>— ${why.join(" · ")}</span></div>` : ""}
    <div class="main">${avatar(c)}
      <div class="who"><div class="sub">${esc(r.world)} · ${r.left_guild ? `${g(c.guild)} <span class="muted">(그 길드 목록엔 없음)</span>` : g(c.guild)}</div><div class="name">${esc(c.name)}</div>
        <div class="sub">${esc(lvjob(c))}</div><div class="badges">${badges.join("")}</div></div></div>
    ${checks ? `<div class="checks">${checks}</div>` : ""}
    ${p && !r.is_rep ? `<div class="alts"><div class="ttl">유니온 대표 캐릭터</div>
      <div class="chars">${charChip(p, r.rep_other_guild || (r.dual && !r.other_guild))}</div></div>` : ""}
  </article>`;
}

function renderLookup(res) {
  const s = res.summary, ours = res.our_guild;
  const bad = res.results.filter((r) => r.dual);
  let v = "";
  if (bad.length) {
    v = `<div class="verdict bad">다른 길드에 있는 캐릭터 ${bad.length}명${s.confirmed ? ` — 그 길드 목록에서 확인(포착) ${s.confirmed}` : ""}
      <span>${bad.map((r) => `${esc(r.char.name)} → ${esc(r.other_guild ? r.char.guild : r.rep?.guild)}`).join(" · ")}</span></div>`;
  } else if (!s.not_found && !s.unknown_rep) {
    v = `<div class="verdict ok">✓ ${ours ? `「${esc(ours)}」 말고 다른 길드에 있는 캐릭터 없음` : "다른 길드 없음"}</div>`;
  }
  if (s.stale) {
    const left = res.results.filter((r) => (r.checks || []).some((k) => k.status === "stale"))
      .flatMap((r) => r.checks.filter((k) => k.status === "stale").map((k) => `${esc(k.who)} (${esc(k.guild)})`));
    v += `<div class="verdict warn">이미 나간 것으로 보이는 캐릭터 ${s.stale}명 — 의심에서 뺐어요
      <span>${[...new Set(left)].join(" · ")} — 캐릭터 정보엔 그 길드로 나오지만 길드원 목록엔 없어요. 확실히 하려면 그 캐릭터로 한 번 접속해 달라고 해 주세요(접속하면 정보가 갱신돼요).</span></div>`;
  }
  const autofix = res.results.filter((r) => !r.found && r.suggest && r.suggest.filter((x) => x.same_account).length === 1)
    .map((r) => [r.name, r.suggest.find((x) => x.same_account).name]);
  if (autofix.length) {
    v += `<div class="verdict fixbox">비슷한 이름의 같은 계정 캐릭터를 찾았어요 — 잘못 읽은 것 같아요
      <span>${autofix.map(([a, b]) => `${esc(a)} → <b>${esc(b)}</b>`).join(" · ")}</span>
      <button type="button" class="primary" id="fixAll">모두 바꾸고 다시 조회</button></div>`;
  }
  if (s.not_found || s.unknown_rep) {
    v += `<div class="verdict warn">확인 못 한 캐릭터가 있어요
      <span>없는 이름 ${s.not_found}명${s.unknown_rep ? ` · 유니온 랭킹에 없어 대표를 모름 ${s.unknown_rep}명` : ""} — 이름(오타·개명)을 확인하거나 메애기에서 찾아봐 주세요</span></div>`;
  }
  if (s.not_fresh) {
    v += `<div class="verdict warn">최신 정보를 못 받은 캐릭터 ${s.not_fresh}명
      <span>어제 기준 정보로 봤어요. 카드의 메애기 링크로 직접 확인해 주세요.</span></div>`;
  }
  const acc = s.accounts > 1
    ? `<div class="verdict warn">넣은 캐릭터가 계정 ${s.accounts}개로 나뉘어요
        <span>핸즈 목록(한 계정)이면 1개여야 해요. 다른 사람 캐릭터가 섞였거나 다른 월드 캐릭터예요.</span></div>` : "";
  const u = readInfo && readInfo.usage;
  $("#lookupResult").innerHTML = `${v}${acc}
    <div class="sub" style="margin:4px 2px 12px">최신 정보로 갱신 · 유니온 대표는 ${esc(res.date)} 랭킹 기준 · ${s.total}명 조회 · 계정 ${s.accounts}개${s.worlds.length ? ` · ${s.worlds.map(esc).join(", ")}` : ""}
      · 넥슨 API ${s.api_calls}번${u ? ` · 이미지 읽기 ${esc(u.model)} 토큰 입력 ${u.input.toLocaleString()} / 출력 ${u.output.toLocaleString()}` : ""}
      ${readInfo && readInfo.note ? `<br>이미지: ${esc(readInfo.note)}` : ""}</div>
    ${readInfo && readInfo.shown_count > readInfo.characters.length ? `<div class="verdict warn">스크린샷에서 ${readInfo.characters.length}개만 읽었어요
      <span>핸즈에는 캐릭터 ${readInfo.shown_count}개라고 나와요. 아래로 스크롤한 스크린샷도 같이 넣어 주세요.</span></div>` : ""}
    <div class="acc-grid">${res.results.map((r) => lookupCard(r, ours)).join("")}</div>`;
  $("#lookupResult").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#lookupResult").querySelectorAll(".fix").forEach((b) => (b.onclick = () => applyFixes([[b.dataset.from, b.dataset.to]])));
  if ($("#fixAll")) $("#fixAll").onclick = () => applyFixes(autofix);
}

// 텍스트 상자에서 잘못 읽은 이름을 바꾸고 다시 조회
function applyFixes(pairs) {
  const map = new Map(pairs);
  $("#lkNames").value = $("#lkNames").value.split("\n").map((l) => map.get(l.trim()) ?? l).join("\n");
  countNames();
  toast(`${pairs.length}개 이름을 바꿨어요`);
  runLookup();
}

// ── 시작: 사용 방식·로그인 확인 → 앱 ──
const lastKey = () => `gc_last_scan_${me.user?.id ?? 0}`;
let started = false;
async function boot() {
  markNewVersion();
  me = await fetch("/api/status").then((r) => r.json()).catch(() => ({ mode: null, user: null }));
  if (!me.mode) return showGate("choose");
  if (!me.user) return showGate("login");
  $("#gate").hidden = true;
  $("#app").hidden = false;
  document.querySelector("header nav").hidden = false;
  await loadSettings();
  showView();
  if (started) return;
  started = true;
  const running = await api("/api/jobs/current").catch(() => null);
  if (running) { setBusy(true); poll(running); }
  const last = store.get(lastKey());
  if (last && !panes.ours.data) {
    try { panes.ours.data = await api(`/api/scans/${last}`); } catch (e) { panes.ours.data = null; }
  }
  renderPane("ours");
}
boot();

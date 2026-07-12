// Kobevoice Cloud — zero-build dashboard (vanilla JS).
// Talks to the same-origin FastAPI control-plane.

const TOKEN_KEY = "kv_cloud_token";
let me = null;

const $ = (sel) => document.querySelector(sel);
const token = () => localStorage.getItem(TOKEN_KEY);
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
const clearToken = () => localStorage.removeItem(TOKEN_KEY);

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && token()) headers["Authorization"] = `Bearer ${token()}`;
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    throw new Error(detail);
  }
  return data;
}

// ---------- Auth view ----------
let registerMode = false;

function renderAuthMode() {
  $("#nameField").classList.toggle("hidden", !registerMode);
  $("#authTitle").textContent = registerMode ? "Create your account" : "Sign in to Kobevoice Cloud";
  $("#authSubmit").textContent = registerMode ? "Create account" : "Sign in";
  $("#authToggleText").textContent = registerMode ? "Already have an account?" : "No account?";
  $("#authToggle").textContent = registerMode ? "Sign in" : "Create one";
  $("#authError").textContent = "";
}

$("#authToggle").addEventListener("click", (e) => {
  e.preventDefault();
  registerMode = !registerMode;
  renderAuthMode();
});

$("#authSubmit").addEventListener("click", async () => {
  $("#authError").textContent = "";
  const email = $("#fEmail").value.trim();
  const password = $("#fPassword").value;
  try {
    let resp;
    if (registerMode) {
      resp = await api("/api/auth/register", {
        method: "POST", auth: false,
        body: { email, password, full_name: $("#fName").value.trim() || null },
      });
    } else {
      resp = await api("/api/auth/login", { method: "POST", auth: false, body: { email, password } });
    }
    setToken(resp.access_token);
    await boot();
  } catch (err) {
    $("#authError").textContent = err.message;
  }
});

$("#logoutBtn").addEventListener("click", () => { clearToken(); location.reload(); });

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const name = tab.dataset.tab;
    document.querySelectorAll("[data-pane]").forEach((p) =>
      p.classList.toggle("hidden", p.dataset.pane !== name));
    if (name === "plans") loadPlans();
    if (name === "generate") loadHistory();
    if (name === "keys") loadKeys();
    if (name === "admin") loadAdmin();
  });
});
$("#goPlansBtn").addEventListener("click", () => document.querySelector('.tab[data-tab="plans"]').click());

// ---------- Overview ----------
async function loadOverview() {
  const sub = await api("/api/billing/subscription");
  $("#planName").textContent = sub.plan.name;
  $("#planStatus").textContent = sub.status;
  $("#planRenew").textContent = sub.current_period_end
    ? `Renews ${new Date(sub.current_period_end).toLocaleDateString()}` : "No renewal — free plan";

  const u = await api("/api/usage");
  $("#usagePeriod").textContent = `Period ${u.period}`;
  const fmt = (lim) => (lim < 0 ? "∞" : lim);
  const pct = (used, lim) => (lim < 0 ? 4 : Math.min(100, Math.round((used / Math.max(lim, 1)) * 100)));
  $("#genLabel").textContent = `${u.generations_used} / ${fmt(u.generations_limit)}`;
  $("#genBar").style.width = pct(u.generations_used, u.generations_limit) + "%";
  $("#charLabel").textContent = `${u.characters_used} / ${fmt(u.characters_limit)}`;
  $("#charBar").style.width = pct(u.characters_used, u.characters_limit) + "%";
}

// ---------- Plans / billing ----------
async function loadPlans() {
  const grid = $("#plansGrid");
  grid.innerHTML = "Loading…";
  const [plans, sub] = await Promise.all([api("/api/billing/plans"), api("/api/billing/subscription")]);
  const current = sub.plan.slug;
  $("#portalBtn").hidden = current === "free";
  grid.innerHTML = "";
  plans.forEach((p) => {
    const price = p.price_cents === 0 ? "Free" : `$${(p.price_cents / 100).toFixed(2)}/${p.interval}`;
    const isCurrent = p.slug === current;
    const el = document.createElement("div");
    el.className = "plan" + (isCurrent ? " current" : "");
    el.innerHTML = `
      <div class="row"><strong>${p.name}</strong>${isCurrent ? '<span class="pill ok">Current</span>' : ""}</div>
      <div class="price">${price}</div>
      <ul>${p.features.map((f) => `<li>${f}</li>`).join("")}</ul>
      ${p.slug === "free" || isCurrent ? "" : `<button class="small" data-plan="${p.slug}">Upgrade</button>`}`;
    grid.appendChild(el);
  });
  grid.querySelectorAll("button[data-plan]").forEach((b) =>
    b.addEventListener("click", () => upgrade(b.dataset.plan)));
}

async function upgrade(slug) {
  $("#billingError").textContent = "";
  try {
    const resp = await api("/api/billing/checkout", { method: "POST", body: { plan_slug: slug } });
    window.location.href = resp.url;
  } catch (err) {
    $("#billingError").textContent = err.message.includes("not configured")
      ? "Billing isn't configured on this server yet (no Stripe key). The plan model is live; add Stripe keys to enable checkout."
      : err.message;
  }
}
$("#portalBtn").addEventListener("click", async () => {
  try { const r = await api("/api/billing/portal", { method: "POST" }); window.location.href = r.url; }
  catch (err) { $("#billingError").textContent = err.message; }
});

// ---------- Generate ----------
async function fetchAudioURL(audioUrl) {
  const res = await fetch(audioUrl, { headers: { Authorization: `Bearer ${token()}` } });
  if (!res.ok) throw new Error("Could not load audio");
  return URL.createObjectURL(await res.blob());
}

$("#speakBtn").addEventListener("click", async () => {
  $("#speakError").textContent = "";
  $("#speakBtn").disabled = true; $("#speakHint").textContent = "Generating…";
  try {
    const r = await api("/api/voice/speak", {
      method: "POST",
      body: { text: $("#speakText").value, profile_id: $("#speakVoice").value || null },
    });
    const player = $("#speakAudio");
    player.src = await fetchAudioURL(r.audio_url);
    player.classList.remove("hidden");
    player.play().catch(() => {});
    $("#speakHint").textContent = "Done — usage recorded.";
    loadOverview();
    loadHistory();
  } catch (err) {
    $("#speakHint").textContent = "";
    $("#speakError").textContent = err.message.includes("unavailable")
      ? "Quota OK, but the voice engine isn't running. Start it with: uvicorn backend.engine_lite.main:app --port 8000"
      : err.message;
  } finally { $("#speakBtn").disabled = false; }
});

async function loadHistory() {
  const gens = await api("/api/voice/generations");
  const box = $("#genHistory");
  if (!gens.length) { box.innerHTML = "No generations yet."; return; }
  box.innerHTML = "";
  gens.forEach((g) => {
    const row = document.createElement("div");
    row.className = "row";
    row.style.cssText = "padding:10px 0;border-bottom:1px solid var(--border)";
    const text = g.text.length > 60 ? g.text.slice(0, 60) + "…" : g.text;
    row.innerHTML = `<div style="flex:1"><div>${text}</div>
      <div class="muted" style="font-size:12px">${g.voice || "default"} · ${g.characters} chars · ${new Date(g.created_at).toLocaleString()}</div></div>`;
    const play = document.createElement("button");
    play.className = "ghost small"; play.textContent = "▶ Play";
    play.addEventListener("click", async () => {
      const player = $("#speakAudio");
      player.src = await fetchAudioURL(g.audio_url);
      player.classList.remove("hidden");
      player.play().catch(() => {});
    });
    const del = document.createElement("button");
    del.className = "danger small"; del.textContent = "Delete";
    del.addEventListener("click", async () => { await api(`/api/voice/generations/${g.id}`, { method: "DELETE" }); loadHistory(); });
    row.appendChild(play); row.appendChild(del);
    box.appendChild(row);
  });
}

// ---------- API keys ----------
async function loadKeys() {
  const keys = await api("/api/api-keys");
  const body = $("#keysBody"); body.innerHTML = "";
  keys.forEach((k) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><code>${k.prefix}…</code></td><td>${k.name || "—"}</td>
      <td>${new Date(k.created_at).toLocaleDateString()}</td>
      <td>${k.revoked ? '<span class="pill">revoked</span>' : '<span class="pill ok">active</span>'}</td>
      <td>${k.revoked ? "" : `<button class="danger small" data-id="${k.id}">Revoke</button>`}</td>`;
    body.appendChild(tr);
  });
  body.querySelectorAll("button[data-id]").forEach((b) =>
    b.addEventListener("click", async () => { await api(`/api/api-keys/${b.dataset.id}`, { method: "DELETE" }); loadKeys(); }));
}
$("#createKeyBtn").addEventListener("click", async () => {
  const r = await api("/api/api-keys", { method: "POST", body: { name: $("#keyName").value.trim() || null } });
  $("#newKey").innerHTML = `New key (copy now, shown once): <code>${r.key}</code>`;
  $("#keyName").value = "";
  loadKeys();
});

// ---------- Admin ----------
async function loadAdmin() {
  const stats = await api("/api/admin/stats");
  $("#adminStats").innerHTML = `
    ${statCard("Total users", stats.total_users)}
    ${statCard("Active subs", stats.active_subscriptions)}
    ${statCard("Gens this period", stats.generations_this_period)}
    ${statCard("MRR", "$" + (stats.mrr_cents / 100).toFixed(2))}`;
  const users = await api("/api/admin/users");
  const body = $("#adminUsers"); body.innerHTML = "";
  users.forEach((u) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${u.email}${u.is_admin ? ' <span class="pill">admin</span>' : ""}</td>
      <td>${u.plan || "—"}</td>
      <td>${u.is_active ? '<span class="pill ok">active</span>' : '<span class="pill">disabled</span>'}</td>
      <td>${u.generations_this_period}</td>
      <td>${u.is_admin ? "" : `<button class="ghost small" data-uid="${u.id}" data-act="${u.is_active ? "disable" : "enable"}">${u.is_active ? "Disable" : "Enable"}</button>`}</td>`;
    body.appendChild(tr);
  });
  body.querySelectorAll("button[data-uid]").forEach((b) =>
    b.addEventListener("click", async () => { await api(`/api/admin/users/${b.dataset.uid}/${b.dataset.act}`, { method: "POST" }); loadAdmin(); }));
}
const statCard = (label, val) => `<div class="plan"><div class="muted">${label}</div><div class="price">${val}</div></div>`;

// ---------- Boot ----------
async function boot() {
  if (!token()) { showAuth(); return; }
  try {
    me = await api("/api/auth/me");
  } catch (_) { clearToken(); showAuth(); return; }
  $("#authView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  $("#navUser").hidden = false;
  $("#navEmail").textContent = me.email;
  if (me.is_admin) $("#adminTabBtn").classList.remove("hidden");
  await loadOverview();
}
function showAuth() {
  $("#appView").classList.add("hidden");
  $("#authView").classList.remove("hidden");
  $("#navUser").hidden = true;
  renderAuthMode();
}

boot();

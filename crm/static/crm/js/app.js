/* LeadCRM – frontend: logowanie JWT, klient API i dashboard (bez bibliotek). */
const CRM = (() => {
  const LOGIN_URL = "/crm/login/";
  const HOME_URL = "/crm/";
  const STATUS = {
    new: ["Nowy", "#6366f1"], callback: ["Do oddzwonienia", "#f59e0b"],
    interested: ["Zainteresowany", "#10b981"], offer: ["Oferta wysłana", "#0ea5e9"],
    won: ["Wygrany", "#16a34a"], lost: ["Przegrany", "#94a3b8"],
    do_not_call: ["Nie dzwonić", "#ef4444"], unreachable: ["Nieosiągalny", "#a855f7"],
  };
  const ROLE = { manager: "Manager", agent: "Agent" };

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* brak dostępu */ } },
    del: (k) => { try { localStorage.removeItem(k); } catch { /* brak dostępu */ } },
  };

  function logout() {
    ["crm_access", "crm_refresh", "crm_user"].forEach(store.del);
    location.href = LOGIN_URL;
  }

  async function refreshToken() {
    const refresh = store.get("crm_refresh");
    if (!refresh) return false;
    const r = await fetch("/api/token/refresh/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    });
    if (!r.ok) return false;
    store.set("crm_access", (await r.json()).access);
    return true;
  }

  /* Zapytanie do API z tokenem; przy 401 próbuje odświeżyć token raz. */
  async function api(path, options = {}, retry = true) {
    const headers = { ...(options.headers || {}) };
    const access = store.get("crm_access");
    if (access) headers.Authorization = "Bearer " + access;
    const r = await fetch(path, { ...options, headers });
    if (r.status === 401 && retry && (await refreshToken())) return api(path, options, false);
    if (r.status === 401) { logout(); throw new Error("Sesja wygasła"); }
    return r;
  }

  function currentUser() {
    try { return JSON.parse(store.get("crm_user")); } catch { return null; }
  }

  /* Motyw: domyślnie jak w systemie, wybór ręczny zapamiętany w przeglądarce. */
  const root = document.documentElement;
  const themeMode = () => root.dataset.theme || "auto";
  const isDark = () => (root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches);
  function setTheme(mode) {
    if (mode === "auto") { delete root.dataset.theme; store.del("crm_theme"); }
    else { root.dataset.theme = mode; store.set("crm_theme", mode); }
  }

  /* Ekran logowania: pojedynczy przycisk motywu w rogu. */
  function initTheme() {
    const btn = $("theme-btn");
    if (!btn) return;
    const paint = () => { btn.textContent = isDark() ? "\u2600" : "\u263E"; };
    btn.addEventListener("click", () => { setTheme(isDark() ? "light" : "dark"); paint(); });
    paint();
  }

  /* Aplikacja: menu użytkownika (motyw + wylogowanie) po kliknięciu w profil. */
  function initUserMenu(user) {
    const btn = $("user-btn"), pop = $("user-popover");
    const paint = () => document.querySelectorAll("[data-theme-choice]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.themeChoice === themeMode())));
    const toggle = (open) => {
      pop.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    };
    $("pop-name").textContent = user.full_name;
    $("pop-role").textContent = ROLE[user.role] || "";
    btn.addEventListener("click", (e) => { e.stopPropagation(); toggle(pop.hidden); });
    pop.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => toggle(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggle(false); });
    document.querySelectorAll("[data-theme-choice]").forEach((b) =>
      b.addEventListener("click", () => { setTheme(b.dataset.themeChoice); paint(); }));
    $("logout-btn").addEventListener("click", logout);
    paint();
  }

  function initLogin() {
    initTheme();
    if (store.get("crm_access") && currentUser()) { location.href = HOME_URL; return; }
    const form = $("login-form"), err = $("login-error");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.hidden = true;
      const btn = form.querySelector("button");
      btn.disabled = true;
      try {
        const r = await fetch("/api/token/", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: $("username").value.trim(), password: $("password").value }),
        });
        if (!r.ok) throw new Error("Nieprawidłowy login lub hasło.");
        const tokens = await r.json();
        store.set("crm_access", tokens.access);
        store.set("crm_refresh", tokens.refresh);
        const me = await (await api("/api/me/")).json();
        if (me.role === "none") {
          ["crm_access", "crm_refresh"].forEach(store.del);
          throw new Error("To konto nie ma roli Agent ani Manager. Skontaktuj się z administratorem.");
        }
        store.set("crm_user", JSON.stringify(me));
        location.href = HOME_URL;
      } catch (ex) {
        err.textContent = ex.message || "Nie udało się zalogować.";
        err.hidden = false;
        btn.disabled = false;
      }
    });
  }

  function initShell() {
    const user = currentUser();
    if (!store.get("crm_access") || !user) { location.href = LOGIN_URL; return; }
    $("user-name").textContent = user.full_name;
    $("user-role").textContent = ROLE[user.role] || "";
    $("user-avatar").textContent = user.full_name.charAt(0).toUpperCase();
    initUserMenu(user);
  }

  const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");

  function renderKpis(d, manager) {
    const calls = d.agents.reduce((s, a) => s + a.calls, 0);
    const answered = d.agents.reduce((s, a) => s + a.answered, 0);
    const interested = d.agents.reduce((s, a) => s + a.interested, 0);
    const cards = [
      [manager ? "Wszystkie leady" : "Moje leady", d.total_leads],
      manager ? ["Leady w puli", d.pool_leads] : ["Wygrane", d.leads_by_status.won || 0],
      ["Rozmowy w okresie", calls],
      ["Odebrane", pct(calls ? answered / calls : null)],
      ["Zainteresowani", pct(answered ? interested / answered : null)],
    ];
    $("kpis").innerHTML = cards.map(([label, value]) =>
      `<div class="kpi"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  }

  function renderFunnel(by) {
    const max = Math.max(1, ...Object.values(by));
    $("funnel").innerHTML = Object.entries(STATUS).map(([code, [label, color]]) => {
      const n = by[code] || 0;
      return `<div class="frow"><span>${esc(label)}</span>
        <div class="track"><i style="width:${(n / max) * 100}%;background:${color}"></i></div>
        <b>${n}</b></div>`;
    }).join("");
  }

  function renderDaily(rows) {
    const max = Math.max(1, ...rows.map((r) => r.calls));
    $("daily").innerHTML = rows.map((r) => {
      const [, m, d] = r.date.split("-");
      return `<div class="bar" title="${esc(r.date)}: ${r.calls}"><b>${r.calls}</b>
        <i style="height:${(r.calls / max) * 100}%"></i><span>${d}.${m}</span></div>`;
    }).join("");
  }

  function renderAgents(rows) {
    $("agents").innerHTML = `<thead><tr><th>Agent</th><th>Leady aktywne</th><th>Wygrane</th>
      <th>Rozmowy</th><th>Odebrane</th><th>Zainteresowani</th></tr></thead><tbody>` +
      rows.map((a) => `<tr><td><strong>${esc(a.username)}</strong></td><td>${a.leads_active}</td>
        <td>${a.leads_won}</td><td>${a.calls}</td><td>${pct(a.answer_rate)}</td>
        <td>${pct(a.interest_rate)}</td></tr>`).join("") + "</tbody>";
  }

  function initDashboard() {
    const user = currentUser();
    if (!user) return;
    const manager = user.role === "manager";
    $("greeting").textContent = "Dzień dobry, " + user.full_name.split(" ")[0];
    $("agents-card").hidden = !manager;
    async function load() {
      $("page-error").hidden = true;
      try {
        const r = await api("/api/stats/?days=" + $("days").value);
        if (!r.ok) throw new Error("Nie udało się pobrać statystyk (" + r.status + ").");
        const d = await r.json();
        $("subtitle").textContent = manager ? "Podsumowanie pracy całego zespołu" : "Twoje wyniki i leady";
        renderKpis(d, manager);
        renderFunnel(d.leads_by_status);
        renderDaily(d.daily_calls);
        if (manager) renderAgents(d.agents);
      } catch (ex) {
        $("page-error").textContent = ex.message;
        $("page-error").hidden = false;
      }
    }
    $("days").addEventListener("change", load);
    load();
  }

  return { initLogin, initShell, initDashboard, api };
})();

/* LeadCRM – frontend: logowanie JWT, klient API i dashboard (bez bibliotek). */
const CRM = (() => {
  const LOGIN_URL = "/crm/login/";
  const HOME_URL = "/crm/";
  const STATUS = {
    new: ["Nowy", "#6366f1"], callback: ["Do oddzwonienia", "#f59e0b"],
    interested: ["Zainteresowany", "#10b981"], offer: ["Oferta wysłana", "#0ea5e9"],
    won: ["Sprzedany", "#16a34a"], lost: ["Bez sprzedaży", "#94a3b8"],
    do_not_call: ["Nie dzwonić", "#ef4444"], unreachable: ["Nieosiągalny", "#a855f7"],
  };
  /* Krótkie wyjaśnienia statusów klienta (karta "Moi klienci"). */
  const STATUS_HINT = {
    new: "nikt jeszcze nie dzwonił", callback: "umówiony kolejny kontakt",
    interested: "chce usłyszeć ofertę", offer: "czeka na decyzję klienta",
    won: "klient kupił", lost: "klient nie chce kupić",
    do_not_call: "zły numer lub prośba o brak kontaktu", unreachable: "nie udało się dodzwonić",
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
    document.querySelectorAll("[data-manager-only]").forEach((el) => { el.hidden = user.role !== "manager"; });
    initUserMenu(user);
  }

  const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");

  function renderKpis(d, manager) {
    const o = d.outcomes;
    const calls = o.no_answer + o.callback + o.interested + o.sold + o.refused + o.wrong_number;
    const answered = calls - o.no_answer;
    const active = d.agents.reduce((s, a) => s + a.leads_active, 0);
    const cards = [
      ["Rozmowy", calls, "w wybranym okresie"],
      ["Dodzwoniłem się", pct(calls ? answered / calls : null), `${answered} z ${calls} rozmów`],
      ["Zainteresowani", o.interested, answered ? `${pct(o.interested / answered)} odebranych` : ""],
      ["Sprzedane", o.sold, "sprzedaże w wybranym okresie"],
      manager ? ["Czekają na agenta", d.pool_leads, "klienci, których nikt jeszcze nie prowadzi"] : ["Moi klienci w toku", active, "jeszcze niezamknięci, do obsłużenia"],
    ];
    $("kpis").innerHTML = cards.map(([label, value, sub]) =>
      `<div class="kpi"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`).join("");
  }

  /* Jeden wiersz: nazwa, pasek proporcjonalny do liczby i liczba po prawej. */
  const orow = (label, n, color, max, hint) =>
    `<div class="orow"><span class="olabel">${esc(label)}${hint ? `<small>${esc(hint)}</small>` : ""}</span>
      <div class="track"><i style="width:${max ? (n / max) * 100 : 0}%;background:${color}"></i></div><b>${n}</b></div>`;

  function renderOutcomes(o) {
    const calls = o.no_answer + o.callback + o.interested + o.sold + o.refused + o.wrong_number;
    const lost = o.refused + o.wrong_number;
    $("outcomes").innerHTML =
      `<div class="big-total"><b>${calls}</b> rozmów w wybranym okresie</div>` +
      [
        ["Nie odebrał", o.no_answer, "#64748b", ""],
        ["Oddzwonić", o.callback, "#f59e0b", "klient zostaje u Ciebie, wraca w terminie"],
        ["Zainteresowany", o.interested, "#10b981", "chce usłyszeć ofertę, jeszcze nie kupił"],
        ["Sprzedane", o.sold, "#16a34a", "klient kupił – sprawa zamknięta"],
        ["Odmowa", o.refused, "#ef4444", "klient nie chce – sprawa zamknięta"],
        ["Zły numer", o.wrong_number, "#a855f7", "nie ten numer – sprawa zamknięta"],
      ].map(([l, n, c, h]) => orow(l, n, c, calls, h)).join("") +
      `<div class="closed-line"><span>Sprawy zamknięte w okresie: <b>${o.sold + lost}</b></span>
        <span class="ok">ze sprzedażą: <b>${o.sold}</b></span><span class="bad">bez sprzedaży: <b>${lost}</b></span></div>`;
  }

  function renderScheduled(s) {
    const rows = [
      ["Zaległe", s.overdue, s.overdue ? "#ef4444" : "#64748b", "termin już minął – zadzwoń teraz"],
      ["Dziś", s.today, "#f59e0b", "jeszcze dzisiaj"],
      ["Jutro", s.tomorrow, "#6366f1", ""],
      ["Później", s.later, "#0ea5e9", "po jutrze"],
    ];
    $("scheduled").innerHTML = rows.map(([l, n, c, h]) =>
      `<div class="srow${l === "Zaległe" && n ? " late" : ""}" style="--c:${c}"><span class="dot"></span>
        <span class="olabel">${esc(l)}${h ? `<small>${esc(h)}</small>` : ""}</span><b>${n}</b></div>`).join("") +
      `<div class="closed-line"><span>Razem zaplanowanych: <b>${s.overdue + s.today + s.tomorrow + s.later}</b></span></div>`;
  }

  function renderFunnel(by) {
    const max = Math.max(1, ...Object.values(by));
    $("funnel").innerHTML = Object.entries(STATUS).map(([code, [label, color]]) =>
      orow(label, by[code] || 0, color, max, STATUS_HINT[code])).join("") +
      `<div class="closed-line"><span>Razem klientów: <b>${Object.values(by).reduce((s, n) => s + n, 0)}</b></span></div>`;
  }

  /* Okres jednodniowy ("Dziś") = rozmowy godzina po godzinie, dłuższy = dzień po dniu. */
  function renderDaily(d) {
    const hourly = d.hourly_calls && d.hourly_calls.length > 0;
    let rows;
    if (hourly) {
      const busy = d.hourly_calls.filter((h) => h.calls > 0).map((h) => h.hour);
      const from = Math.min(8, ...busy), to = Math.max(20, ...busy);
      rows = d.hourly_calls.filter((h) => h.hour >= from && h.hour <= to)
        .map((h) => ({ label: h.hour + ":00", tip: `${h.hour}:00–${h.hour + 1}:00`, calls: h.calls }));
    } else {
      rows = d.daily_calls.map((r) => {
        const [, m, day] = r.date.split("-");
        return { label: day + "." + m, tip: r.date, calls: r.calls };
      });
    }
    $("daily-title").textContent = hourly ? "Rozmowy w ciągu dnia (godzina po godzinie)" : "Rozmowy dzień po dniu";
    const max = Math.max(1, ...rows.map((r) => r.calls));
    const n = rows.length, step = n <= 14 ? 1 : Math.ceil(n / 7);
    $("daily").style.gap = n > 31 ? "2px" : n > 14 ? "4px" : ".5rem";
    $("daily").innerHTML = rows.map((r, i) => {
      const showNum = r.calls > 0 || (n <= 14 && !hourly), showLabel = i % step === 0;
      return `<div class="bar" title="${esc(r.tip)}: ${r.calls}"><b>${showNum ? r.calls : ""}</b>
        <i style="height:${(r.calls / max) * 100}%"></i><span>${showLabel ? r.label : ""}</span></div>`;
    }).join("");
  }

  function renderAgents(rows) {
    $("agents").innerHTML = `<thead><tr><th>Agent</th><th>Klienci w toku</th><th>Sprzedani klienci (łącznie)</th>
      <th>Rozmowy</th><th>Nie odebrał</th><th>Oddzwonić</th><th>Zainteresowani</th>
      <th>Sprzedaże w okresie</th><th>Odmowy</th><th>Zły numer</th><th>Dodzwonienia</th></tr></thead><tbody>` +
      rows.map((a) => `<tr><td><strong>${esc(a.username)}</strong></td><td>${a.leads_active}</td>
        <td>${a.leads_won}</td><td>${a.calls}</td><td>${a.no_answer}</td><td>${a.callbacks}</td>
        <td>${a.interested}</td><td>${a.sold}</td><td>${a.refused}</td><td>${a.wrong_number}</td>
        <td>${pct(a.answer_rate)}</td></tr>`).join("") + "</tbody>";
  }

  function initDashboard() {
    const user = currentUser();
    if (!user) return;
    const manager = user.role === "manager";
    $("greeting").textContent = "Dzień dobry, " + user.full_name.split(" ")[0];
    $("agents-card").hidden = !manager;
    $("funnel-title").textContent = manager ? "Wszyscy klienci" : "Moi klienci";
    const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    $("date-to").value = iso(new Date());
    $("date-from").value = iso(new Date());
    function periodQuery() {
      if ($("days").value !== "custom") return "days=" + $("days").value;
      return "date_from=" + $("date-from").value + "&date_to=" + $("date-to").value;
    }
    async function load() {
      $("page-error").hidden = true;
      $("range").hidden = $("days").value !== "custom";
      if ($("days").value === "custom" && !($("date-from").value && $("date-to").value)) return;
      try {
        const r = await api("/api/stats/?" + periodQuery());
        if (!r.ok) throw new Error("Nie udało się pobrać statystyk (" + r.status + ").");
        const d = await r.json();
        const day = (v) => new Date(v + "T12:00:00").toLocaleDateString("pl-PL");
        const period = d.days === 1 && d.date_from === iso(new Date()) ? "dziś"
          : d.date_from === d.date_to ? day(d.date_from) : day(d.date_from) + " – " + day(d.date_to);
        $("subtitle").textContent = (manager ? "Praca całego zespołu" : "Twoje wyniki i klienci") + " · rozmowy: " + period;
        renderKpis(d, manager);
        $("outcomes-period").textContent = "· " + period;
        $("agents-period").textContent = "· " + period;
        renderOutcomes(d.outcomes);
        renderScheduled(d.scheduled);
        renderFunnel(d.leads_by_status);
        renderDaily(d);
        if (manager) renderAgents(d.agents);
      } catch (ex) {
        $("page-error").textContent = ex.message;
        $("page-error").hidden = false;
      }
    }
    ["days", "date-from", "date-to"].forEach((id) => $(id).addEventListener("change", load));
    load();
  }

  return { initLogin, initShell, initDashboard, api, esc, STATUS, $, currentUser };
})();

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

  /* Zdjęcie profilowe albo pierwsza litera nazwy (menu boczne i menu profilu). */
  function paintAvatars(user) {
    ["user-avatar", "pop-avatar"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (user.avatar) { el.style.backgroundImage = `url("${user.avatar}")`; el.textContent = ""; el.classList.add("has-img"); }
      else { el.style.backgroundImage = ""; el.textContent = user.full_name.charAt(0).toUpperCase(); el.classList.remove("has-img"); }
    });
    const rm = $("avatar-remove");
    if (rm) rm.hidden = !user.avatar;
  }

  /* Wycina środek zdjęcia do kwadratu 160 px i zapisuje jako JPEG (kilka KB). */
  function resizeToSquare(file, size = 160) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const side = Math.min(img.width, img.height);
        const c = document.createElement("canvas");
        c.width = c.height = size;
        c.getContext("2d").drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("To nie wygląda na zdjęcie.")); };
      img.src = url;
    });
  }

  function initAvatar(user) {
    const input = $("avatar-file"), msg = $("avatar-msg");
    const say = (text, bad) => { msg.textContent = text; msg.hidden = !text; msg.classList.toggle("bad", !!bad); };
    const save = (patch) => {
      const next = { ...(currentUser() || user), ...patch };
      store.set("crm_user", JSON.stringify(next));
      paintAvatars(next);
    };
    $("avatar-change").addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files[0];
      input.value = "";
      if (!file) return;
      say("Wysyłanie…");
      try {
        const image = await resizeToSquare(file);
        const r = await api("/api/me/avatar/", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image }),
        });
        if (!r.ok) throw new Error(((await r.json()).image || [])[0] || "Nie udało się zapisać zdjęcia.");
        save({ avatar: image });
        say("Zapisano.");
      } catch (ex) { say(ex.message, true); }
    });
    $("avatar-remove").addEventListener("click", async () => {
      const r = await api("/api/me/avatar/", { method: "DELETE" });
      if (r.ok) { save({ avatar: "" }); say("Usunięto zdjęcie."); } else say("Nie udało się usunąć zdjęcia.", true);
    });
  }

  /* Aplikacja: menu użytkownika (zdjęcie, motyw, wylogowanie) po kliknięciu w profil. */
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

  /* Zmiana hasła: dobrowolna (menu profilu) albo wymuszona po haśle tymczasowym od managera. */
  function initPassword(user) {
    const dlg = $("pw-dialog");
    let forced = false;
    const err = (msg) => { $("pw-error").textContent = msg || ""; $("pw-error").hidden = !msg; };
    function open(isForced) {
      forced = isForced;
      $("pw-form").reset();
      err("");
      $("pw-title").textContent = forced ? "Ustaw swoje hasło" : "Zmiana hasła";
      $("pw-intro").textContent = forced
        ? "Zalogowano hasłem tymczasowym od managera. Wybierz własne hasło, żeby korzystać z aplikacji."
        : "Wpisz obecne hasło i wybierz nowe.";
      $("pw-current-field").hidden = forced;  // hasło tymczasowe już wpisano przy logowaniu
      $("pw-cancel").textContent = forced ? "Wyloguj" : "Anuluj";
      if (!dlg.open) dlg.showModal();
      (forced ? $("pw-new") : $("pw-current")).focus();
    }
    dlg.addEventListener("cancel", (e) => { if (forced) e.preventDefault(); });
    $("pw-cancel").addEventListener("click", () => { if (forced) logout(); else dlg.close(); });
    $("change-pw-btn").addEventListener("click", () => {
      $("user-popover").hidden = true;
      $("user-btn").setAttribute("aria-expanded", "false");
      open(false);
    });
    $("pw-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      err("");
      const current = forced ? "" : $("pw-current").value, next = $("pw-new").value;
      if ((!forced && !current) || !next) { err("Wypełnij wszystkie pola."); return; }
      if (next !== $("pw-repeat").value) { err("Nowe hasła nie są takie same."); return; }
      $("pw-ok").disabled = true;
      try {
        const r = await api("/api/me/password/", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_password: current, new_password: next }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          const parts = Object.values(data).map((v) => (Array.isArray(v) ? v.join(" ") : String(v)));
          err(parts.join(" ") || "Nie udało się zmienić hasła.");
          return;
        }
        store.set("crm_access", data.access);
        store.set("crm_refresh", data.refresh);
        const me = currentUser() || {};
        store.set("crm_user", JSON.stringify({ ...me, must_change_password: false }));
        forced = false;
        dlg.close();
      } catch (ex) {
        err(ex.message);
      } finally {
        $("pw-ok").disabled = false;
      }
    });
    if (user.must_change_password) open(true);
    return { open };
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
    document.querySelectorAll("[data-manager-only]").forEach((el) => { el.hidden = user.role !== "manager"; });
    applyTeamAccess(user);
    initUserMenu(user);
    paintAvatars(user);
    initAvatar(user);
    const password = initPassword(user);
    /* Manager mógł zmienić uprawnienia od logowania, więc odświeżamy je z /api/me/. */
    api("/api/me/").then((r) => (r.ok ? r.json() : null)).then((me) => {
      if (!me || me.role === "none") return;
      if (me.role !== user.role) {  // manager zmienił rolę w trakcie sesji
        store.set("crm_user", JSON.stringify({ ...user, ...me }));
        location.reload();
        return;
      }
      const fresh = { ...user, full_name: me.full_name, username: me.username, can_view_team: me.can_view_team, avatar: me.avatar || "", must_change_password: !!me.must_change_password };
      if (me.must_change_password && !user.must_change_password) password.open(true);
      store.set("crm_user", JSON.stringify(fresh));
      applyTeamAccess(me);
      paintAvatars(fresh);
    }).catch(() => { /* zostaje zapamiętana wartość */ });
  }

  /* Pozycja "Ranking" w menu: manager zawsze, agent dopóki manager nie wyłączył mu podglądu. */
  function applyTeamAccess(user) {
    const allowed = user.role === "manager" || user.can_view_team !== false;
    document.querySelectorAll("[data-team-only]").forEach((el) => { el.hidden = !allowed; });
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
      ["Przychód", CRM.money(d.revenue), d.revenue && Number(d.revenue) > 0 ? "wartość sprzedanych produktów" : "wpisuj produkty przy sprzedaży"],
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

  /* Sprzedaż według produktów: ile sztuk i na jaką kwotę (pasek proporcjonalny do przychodu) */
  function renderSalesByProduct(rows, revenue) {
    const max = Math.max(1, ...rows.map((r) => Number(r.revenue)));
    $("sales-products").innerHTML = rows.length
      ? rows.map((r) => `<div class="orow money"><span class="olabel">${esc(r.product_name)}<small>ilość: ${r.quantity}</small></span>
          <div class="track"><i style="width:${(Number(r.revenue) / max) * 100}%;background:#16a34a"></i></div><b>${esc(CRM.money(r.revenue))}</b></div>`).join("") +
        `<div class="closed-line"><span>Razem: <b>${esc(CRM.money(revenue))}</b></span></div>`
      : `<p class="muted">W tym okresie nie wpisano sprzedaży z produktami.</p>`;
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
        const tip = new Date(r.date + "T12:00:00").toLocaleDateString("pl-PL",
          { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
        return { label: day + "." + m, tip, calls: r.calls };
      });
    }
    $("daily-title").textContent = hourly ? "Rozmowy w ciągu dnia (godzina po godzinie)" : "Rozmowy dzień po dniu";
    const max = Math.max(1, ...rows.map((r) => r.calls));
    const n = rows.length, step = n <= 14 ? 1 : Math.ceil(n / 7);
    const gap = n > 31 ? "2px" : n > 14 ? "4px" : ".5rem";
    const rozmow = (k) => k === 1 ? "rozmowa" : (k % 10 >= 2 && k % 10 <= 4 && (k % 100 < 12 || k % 100 > 14)) ? "rozmowy" : "rozmów";
    let lastNum = -99;
    const bars = rows.map((r, i) => {
      let showNum;
      if (n <= 14 && !hourly) showNum = true;
      else if (n <= 31) showNum = r.calls > 0;
      else showNum = r.calls > 0 && i - lastNum >= 3;
      if (showNum) lastNum = i;
      const h = r.calls > 0 ? Math.max(2, (r.calls / max) * 100) : 0;
      return `<div class="bar">
        <i class="${r.calls > 0 ? "" : "zero"}" style="height:${h}%">${showNum ? `<b>${r.calls}</b>` : ""}</i>
        <div class="tip"><strong>${esc(r.tip)}</strong><span>${r.calls} ${rozmow(r.calls)}</span></div></div>`;
    }).join("");
    /* Oś: zawsze pierwsza data z lewej i ostatnia z prawej, w środku co kilka słupków. */
    const axis = rows.map((r, i) => {
      const edge = n > 14 && (i === 0 || i === n - 1);
      const middle = i % step === 0 && i > 0 && i < n - 1 && (n - 1 - i) >= step * 0.6;
      const cls = n > 14 ? (i === 0 ? "first" : i === n - 1 ? "last" : "") : "";
      return `<span class="${cls}">${(n <= 14 || edge || middle) ? esc(r.label) : ""}</span>`;
    }).join("");
    $("daily").innerHTML = `<div class="bars" style="gap:${gap}">${bars}</div><div class="axis" style="gap:${gap}">${axis}</div>`;
  }

  function renderAgents(rows) {
    $("agents").innerHTML = `<thead><tr><th>Agent</th><th>Klienci w toku</th><th>Sprzedani klienci (łącznie)</th>
      <th>Rozmowy</th><th>Nie odebrał</th><th>Oddzwonić</th><th>Zainteresowani</th>
      <th>Sprzedaże w okresie</th><th>Przychód</th><th>Odmowy</th><th>Zły numer</th><th>Dodzwonienia</th></tr></thead><tbody>` +
      rows.map((a) => `<tr><td><strong>${esc(a.username)}</strong></td><td>${a.leads_active}</td>
        <td>${a.leads_won}</td><td>${a.calls}</td><td>${a.no_answer}</td><td>${a.callbacks}</td>
        <td>${a.interested}</td><td>${a.sold}</td><td class="nowrap">${esc(CRM.money(a.revenue))}</td><td>${a.refused}</td><td>${a.wrong_number}</td>
        <td>${pct(a.answer_rate)}</td></tr>`).join("") + "</tbody>";
  }

  function initDashboard() {
    const user = currentUser();
    if (!user) return;
    const isManager = user.role === "manager";
    /* Manager przełącza widok: cały zespół albo tylko jego własna praca (pamiętane w przeglądarce). */
    let manager = isManager && store.get("crm_scope") !== "me";
    $("greeting").textContent = "Dzień dobry, " + user.full_name.split(" ")[0];
    function applyScope() {
      $("agents-card").hidden = !manager;
      $("funnel-title").textContent = manager ? "Wszyscy klienci" : "Moi klienci";
      document.querySelectorAll("#scope-tabs button").forEach((b) =>
        b.setAttribute("aria-selected", String((b.dataset.scope === "team") === manager)));
    }
    if (isManager) {
      $("scope-tabs").hidden = false;
      document.querySelectorAll("#scope-tabs button").forEach((b) => b.addEventListener("click", () => {
        manager = b.dataset.scope === "team";
        store.set("crm_scope", manager ? "team" : "me");
        applyScope();
        load();
      }));
    }
    applyScope();
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
        const r = await api("/api/stats/?" + periodQuery() + (isManager && !manager ? "&scope=me" : ""));
        if (!r.ok) throw new Error("Nie udało się pobrać statystyk (" + r.status + ").");
        const d = await r.json();
        const day = (v) => new Date(v + "T12:00:00").toLocaleDateString("pl-PL");
        const period = d.days === 1 && d.date_from === iso(new Date()) ? "dziś"
          : d.date_from === d.date_to ? day(d.date_from) : day(d.date_from) + " – " + day(d.date_to);
        $("subtitle").textContent = (manager ? "Praca całego zespołu" : isManager ? "Twoja własna praca" : "Twoje wyniki i klienci") + " · rozmowy: " + period;
        renderKpis(d, manager);
        $("outcomes-period").textContent = "· " + period;
        $("agents-period").textContent = "· " + period;
        renderOutcomes(d.outcomes);
        $("sales-period").textContent = "· " + period;
        renderSalesByProduct(d.sales_by_product || [], d.revenue);
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

  return { initLogin, initShell, initDashboard, resizeToSquare, api, esc, STATUS, $, currentUser };
})();

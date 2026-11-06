/* Solid CRM – ekran „Dzwonienie”.
   Lewa kolumna: „Następny klient” + moi klienci w segmentach (oferty, oddzwonienia, nie odebrali, nowi).
   Środek: klient, „Zadzwoń”, wynik rozmowy (klawisze 1–8), oferta / sprzedaż / termin, dane klienta, historia.
   Prawa kolumna: liczby dnia, minuty rozmów (cel 15 min/h), terminy na dziś, informacje zespołu.
   Dół: pasek dialera (stan telefonu, czas połączenia). */
(() => {
  if (!CRM.esc || !CRM.leadTitle || typeof WHEN === "undefined") {
    document.getElementById("lead-pane").textContent = "Wczytała się stara wersja strony. Odśwież ją (Ctrl+F5).";
    return;
  }
  const { api, esc, $ } = CRM;
  const title = CRM.leadTitle;
  const me = CRM.currentUser() || {};
  const { parseWhen, fmtInput, fmtLong, fmtNative } = WHEN;

  /* ---------- słowniki ---------- */
  const SEG = {
    offer: { label: "Oferty", long: "Oferty i zainteresowani", color: "var(--seg-offer)", icon: "€" },
    callback: { label: "Oddzwonienia", long: "Umówione oddzwonienia", color: "var(--seg-callback)", icon: "↺" },
    retry: { label: "Nie odebrali", long: "Nie odebrali – ponowna próba", color: "var(--seg-retry)", icon: "…" },
    new: { label: "Nowi", long: "Nowi – pierwszy telefon", color: "var(--seg-new)", icon: "+" },
  };
  const SEG_ORDER = ["offer", "callback", "retry", "new"];
  const OUTCOMES = [
    { key: "no_answer", n: 1, label: "Nie odebrał", hint: "ponowna próba sama", color: "#475569" },
    { key: "callback", n: 2, label: "Oddzwonić", hint: "umów termin", color: "#d97706" },
    { key: "interested", n: 3, label: "Zainteresowany", hint: "oferta później", color: "#0d9488" },
    { key: "offer", n: 4, label: "Wyślij ofertę", hint: "oferta + follow-up", color: "#0f766e" },
    { key: "sold", n: 5, label: "Sprzedane", hint: "zapisz umowę", color: "#15803d" },
    { key: "refused", n: 6, label: "Odmowa", hint: "zamyka klienta", color: "#dc2626" },
    { key: "wrong_number", n: 7, label: "Zły numer", hint: "nie dzwonić", color: "#78716c" },
    { key: "skipped", n: 8, label: "Pomiń firmę", hint: "bez dzwonienia", color: "#57534e" },
  ];
  const OUT_LABEL = { ...Object.fromEntries(OUTCOMES.map((o) => [o.key, o.label])), offer: "Wysłana oferta", note: "Notatka", skipped: "Pominięty" };
  const OUT_COLOR = { ...Object.fromEntries(OUTCOMES.map((o) => [o.key, o.color])), note: "#94a3b8" };
  const SKIP_LABEL = Object.fromEntries(CRM.SKIP_REASONS);
  const REFUSE_CHIPS = ["Ma umowę w innej sieci", "Za drogo", "Nie jest zainteresowany", "Nie decyduje o tym", "Prosi o brak kontaktu"];
  /* Wyniki, po których była rozmowa: tylko przy nich pytamy o sieć klienta */
  const TALKED = ["callback", "interested", "offer", "sold", "refused"];
  /* Wzory komentarzy pasujące do wybranego wyniku (klik dopisuje do komentarza) */
  const PHRASES = {
    no_answer: ["Poczta głosowa", "Zajęte", "Odrzucił połączenie", "Sygnał, nikt nie odbiera"],
    callback: ["Zajęty, prosił później", "Decyduje szef / wspólnik", "Sekretariat – połączyć z decydującym", "W trasie, oddzwonić", "Na urlopie"],
    interested: ["Poda e-mail później", "Musi porozmawiać ze wspólnikiem", "Sprawdzi obecną umowę", "Umowa kończy się za kilka miesięcy", "Chce ofertę na więcej numerów"],
    offer: ["Porównuje z obecną umową", "Decyzja po rozmowie ze wspólnikiem", "Interesuje go też internet", "Ma ofertę konkurencji"],
    sold: ["Przeniesienie numerów", "Nowe numery", "Umowa przez kuriera", "Klient poleca znajomych"],
    refused: [],
    wrong_number: ["Numer prywatny – nie firma", "Numer nieaktywny", "Inna firma pod tym numerem"],
    skipped: [],
  };
  /* Spółka (sp. z o.o., S.A., sp.j., sp.k.) – rozpoznana po nazwie */
  const isCompanyEntity = (name) => /(sp\.?\s*z\s*o\.?\s*o|spółka|spolka|s\.\s?a\.|s\.a$|sp\.\s?[jkp]\.)/i.test(name || "");
  const CLOSED = ["won", "lost", "do_not_call", "unreachable"];
  const DUE_SOON_MS = 2 * 60000;

  const segOf = (l) => (["offer", "interested"].includes(l.status) ? "offer" : l.status === "callback" ? "callback"
    : l.status === "new" ? (l.attempts_count ? "retry" : "new") : "closed");
  const p2 = (n) => String(n).padStart(2, "0");
  const hhmm = (iso) => { const d = new Date(iso); return `${p2(d.getHours())}:${p2(d.getMinutes())}`; };
  const dayShort = (iso) => { const d = new Date(iso), t = new Date(), y = new Date(Date.now() - 864e5), tm = new Date(Date.now() + 864e5);
    if (d.toDateString() === t.toDateString()) return "dziś"; if (d.toDateString() === y.toDateString()) return "wczoraj";
    if (d.toDateString() === tm.toDateString()) return "jutro";
    return d.toLocaleDateString("pl-PL", { weekday: "short", day: "numeric", month: "numeric" }).replace(".,", ","); };
  const when = (iso) => `${dayShort(iso)} ${hhmm(iso)}`;
  function rel(iso) {
    const m = Math.round((new Date(iso) - Date.now()) / 60000), a = Math.abs(m);
    if (a < 1) return "teraz";
    const t = a < 60 ? `${a} min` : a < 1440 ? `${Math.round(a / 60)} h` : `${Math.round(a / 1440)} d`;
    return m < 0 ? `${t} temu` : `za ${t}`;
  }
  const few = (n) => n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20);
  const pl = (n, one, some, many) => `${n} ${n === 1 ? one : few(n) ? some : many}`;
  const money = CRM.money;
  const toastEl = () => $("toast");
  let toastTimer;
  function toast(msg, isError) {
    const t = toastEl(); t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
  }
  const errText = (d) => (d && d.detail) || Object.values(d || {}).flat().map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ") || "Nie udało się zapisać.";
  async function jsonReq(url, method, body) {
    if (tourDemo) return { error: "To tylko przykład z samouczka – tu nic się nie zapisuje." };
    try {
      const r = await api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json().catch(() => ({}));
      return r.ok ? { data, status: r.status } : { error: errText(data), status: r.status, data };
    } catch { return { error: "Brak połączenia z serwerem. Spróbuj ponownie." }; }
  }

  /* ---------- stan ---------- */
  let tourDemo = false;
  let leads = [], current = null, segFilter = "all", tab = "todo", summary = null, history = [];
  let plans = [], config = { default_term: 36, promo_label: "6 miesięcy gratis", mode: "forward", forward_name: "Czarek", forward_email: "" };
  let dialer = { online: false, paired: false, call: null };
  let form = null;   // stan formularza wyniku: { outcome, ... }

  /* ================= LEWA KOLUMNA ================= */
  async function loadLeads() {
    let url = "/api/my-leads/", all = [];
    for (let i = 0; i < 10 && url; i++) {
      const r = await api(url); if (!r.ok) break;
      const d = await r.json(); all = all.concat(d.results);
      url = d.next ? d.next.replace(location.origin, "") : null;
    }
    leads = all.filter((l) => !CLOSED.includes(l.status));
    renderList();
  }
  const dueAt = (l) => (l.next_contact_at ? new Date(l.next_contact_at).getTime() : null);
  const isDue = (l) => { const t = dueAt(l); return segOf(l) === "new" ? true : t !== null && t <= Date.now() + (segOf(l) === "retry" ? 0 : DUE_SOON_MS); };
  const isLate = (l) => { const t = dueAt(l); return t !== null && t < Date.now() - 60000 && ["offer", "callback"].includes(segOf(l)); };
  function sortKey(l) { return [isDue(l) ? 0 : 1, SEG_ORDER.indexOf(segOf(l)), dueAt(l) ?? Infinity]; }
  const cmp = (a, b) => { const x = sortKey(a), y = sortKey(b); for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; };

  function itemHtml(l) {
    const s = segOf(l), t = l.next_contact_at;
    const whenCls = isLate(l) ? " late" : t && dueAt(l) - Date.now() < 3600e3 && dueAt(l) > Date.now() ? " soon" : "";
    const whenTxt = t ? (isDue(l) ? (isLate(l) ? rel(t).replace(" temu", " spóźn.") : "teraz") : when(t)) : s === "new" ? "nowy" : "";
    const sub = [l.phone, l.company_city].filter(Boolean).map(esc).join(" · ");
    return `<button type="button" class="w-item${current && current.id === l.id ? " active" : ""}" data-id="${l.id}" style="--c:${SEG[s] ? SEG[s].color : "var(--line)"}">
      <span class="wi-top"><strong>${esc(title(l))}</strong><span class="wi-when${whenCls}">${esc(whenTxt)}</span></span>
      <span class="wi-sub">${sub}${s === "retry" ? ` · nie odebrał ${l.owner_no_answers || l.attempts_count}×` : ""}</span>
      ${l.next_contact_note ? `<span class="wi-topic">${esc(l.next_contact_note)}</span>` : ""}</button>`;
  }
  function renderList() {
    const box = $("lead-list");
    if (tab === "hist") { renderHistory(); return; }
    const list = leads.filter((l) => segFilter === "all" || segOf(l) === segFilter).sort(cmp);
    $("lead-count").textContent = leads.length ? `(${leads.length})` : "";
    if (!list.length) {
      box.innerHTML = `<p class="w-empty">${segFilter === "all" ? "Nie masz jeszcze klientów w pracy. Kliknij „Następny klient”." : "Nikogo w tej grupie."}</p>`;
      return;
    }
    const groups = [["Do zrobienia teraz", list.filter(isDue)], ["Później", list.filter((l) => !isDue(l))]];
    box.innerHTML = groups.filter(([, g]) => g.length).map(([name, g]) =>
      `<div class="w-group${name.startsWith("Do") && g.some(isLate) ? " late" : ""}">${name} · ${g.length}</div>${g.map(itemHtml).join("")}`).join("");
  }
  function renderHistory() {
    const box = $("lead-list");
    if (!history.length) { box.innerHTML = `<p class="w-empty">Dziś jeszcze nic nie zapisano.</p>`; return; }
    box.innerHTML = history.map((a) => `<button type="button" class="w-item hist" data-id="${a.lead}" style="--c:${OUT_COLOR[a.outcome] || "#64748b"}">
      <span class="wi-top"><strong>${esc(a.lead_name || "Klient #" + a.lead)}</strong><span class="wi-when">${hhmm(a.created_at)}</span></span>
      <span class="wi-out">${esc(OUT_LABEL[a.outcome] || a.outcome)}${a.call_seconds ? ` · ${Math.round(a.call_seconds / 60)} min` : ""}</span>
      ${a.note ? `<span class="wi-sub">${esc(a.note)}</span>` : ""}</button>`).join("");
  }
  async function loadHistory() {
    try {
      const r = await api(`/api/activities/?agent=${me.id}&ordering=-created_at`);
      if (!r.ok) return;
      const d = await r.json(), today = new Date().toDateString();
      history = (d.results || d).filter((a) => new Date(a.created_at).toDateString() === today);
      if (tab === "hist") renderHistory();
    } catch { /* lista historii jest dodatkiem */ }
  }
  function renderSegs() {
    const c = (summary && summary.counts) || {};
    const total = SEG_ORDER.reduce((s, k) => s + ((c[k] && c[k].total) || 0), 0);
    $("segs").innerHTML = `<button type="button" class="w-seg all" data-seg="all" aria-pressed="${segFilter === "all"}"><span>Wszyscy moi klienci</span><b>${leads.length || total}</b></button>` +
      SEG_ORDER.filter((k) => k !== "new").concat(["new"]).map((k) => {
        const n = c[k] || { total: 0, due: 0 };
        const due = k !== "new" && n.due ? `<small>${n.due} teraz</small>` : "";
        return `<button type="button" class="w-seg" data-seg="${k}" style="--c:${SEG[k].color}" aria-pressed="${segFilter === k}" title="${esc(SEG[k].long)}"><span>${esc(SEG[k].label)}</span><b>${n.total}${due}</b></button>`;
      }).join("");
  }
  function renderNextHint() {
    const c = (summary && summary.counts) || {};
    const hint = $("next-hint");
    const parts = [];
    if (c.offer && c.offer.due) parts.push(`<b>${pl(c.offer.due, "follow-up oferty", "follow-upy ofert", "follow-upów ofert")}</b>`);
    if (c.callback && c.callback.due) parts.push(`<b>${pl(c.callback.due, "oddzwonienie", "oddzwonienia", "oddzwonień")}</b>`);
    if (c.retry && c.retry.due) parts.push(`${pl(c.retry.due, "ponowna próba", "ponowne próby", "ponownych prób")}`);
    hint.innerHTML = parts.length ? `Najpierw: ${parts.join(", ")}` : "Teraz: nowa firma z Twojej bazy";
  }
  /* Ile jeszcze firm do obdzwonienia: własna baza (pasek postępu) i wspólna pula zespołu */
  function renderBase() {
    const b = summary && summary.base, box = $("base-progress");
    if (!b) { box.hidden = true; return; }
    box.hidden = false;
    const fmt = (n) => Number(n || 0).toLocaleString("pl-PL");
    const own = b.total ? `<div class="bp-text"><span>Twoja baza</span><b>zostało ${fmt(b.left)} z ${fmt(b.total)}</b></div>
      <div class="bp-track"><i style="width:${Math.round((b.done / b.total) * 100)}%"></i></div>
      <small>${b.left ? `${pl(b.left, "firma czeka", "firmy czekają", "firm czeka")} na pierwszy telefon` : "Baza obdzwoniona: nowi przyjdą ze wspólnej puli"}</small>` : "";
    const pool = `<div class="bp-text bp-pool"><span>Wspólna pula zespołu</span><b>${fmt(b.pool_left)} ${b.pool_left === 1 ? "firma" : few(b.pool_left || 0) ? "firmy" : "firm"}</b></div>`;
    box.innerHTML = own + pool + (!b.left && !b.pool_left ? `<small class="bp-empty">Nowych firm już nie ma – poproś managera o nową bazę.</small>` : "");
  }
  let summaryOpened = false;
  async function loadSummary() {
    try {
      // pierwsze wczytanie = agent otworzył „Dzwonienie” (liczy się jako praca); odświeżanie w tle – nie
      const r = await api("/api/work/summary/" + (summaryOpened ? "" : "?open=1"));
      summaryOpened = true;
      if (!r.ok) return;
      summary = await r.json();
      renderSegs(); renderNextHint(); renderBase(); renderSide(); renderDialbar();
    } catch { /* liczniki są dodatkiem */ }
  }

  /* ================= PRAWA KOLUMNA ================= */
  let dayPlan = [], announcements = [];
  async function loadSideData() {
    try {
      const [s, a] = await Promise.all([api("/api/my-leads/schedule/"), api("/api/announcements/")]);
      if (s.ok) { const d = await s.json(); dayPlan = [...d.overdue, ...d.scheduled]; }
      if (a.ok) announcements = await a.json();
      renderSide();
    } catch { /* panel boczny jest dodatkiem */ }
  }
  function renderSide() {
    const side = $("side");
    const show = !CRM.dayBarEnabled || CRM.dayBarEnabled();
    side.hidden = !show; $("work").classList.toggle("no-side", !show);
    if (!show || !summary) return;
    const t = summary.today, goal = t.goal_minutes_per_hour;
    const hourNow = new Date().getHours();
    const hours = t.minutes_by_hour.filter((h) => (h.hour >= 8 && h.hour <= 18) || h.minutes > 0);
    const pctHour = Math.min(100, Math.round((t.minutes_this_hour / goal) * 100));
    const minutesBlock = t.dialer_used || t.minutes_today
      ? `<div class="w-goal"><div class="g-top"><span>Ta godzina</span><b>${CRM.talk(t.minutes_this_hour)} / ${goal} min</b></div>
          <div class="g-track"><i class="${t.minutes_this_hour >= goal ? "ok" : ""}" style="width:${pctHour}%"></i></div>
          <div class="w-hours" aria-label="Minuty rozmów w godzinach">${hours.map((h) => `<div class="${h.hour === hourNow ? "now" : ""}" title="${h.hour}:00 – ${CRM.talk(h.minutes)}${h.hour === hourNow ? " (teraz)" : ""}"><i class="${h.minutes === 0 ? "zero" : h.minutes < goal ? "low" : ""}" style="height:${Math.max(4, Math.min(100, (h.minutes / goal) * 100))}%"></i><small>${h.hour}</small></div>`).join("")}</div>
          <p class="w-goal-note">Dziś: <b class="${t.day_goal && t.minutes_today >= t.day_goal ? "ok" : ""}">${CRM.talk(t.minutes_today)}</b> rozmów, powinno być <b>${t.day_goal} min</b>${workdayText()}. Cel: ${goal} min na każdą godzinę pracy.</p></div>`
      : `<p class="w-goal-note">Dziś powinno być <b>${t.day_goal} min</b> rozmów${workdayText()} – cel ${goal} min na każdą godzinę pracy. Minuty policzą się same, gdy połączysz telefon z aplikacją Solid Dialer.</p>`;
    const plan = dayPlan.slice().sort((a, b) => new Date(a.next_contact_at) - new Date(b.next_contact_at));
    side.innerHTML = `<div class="w-panel"><div class="w-side-head"><h3>Dziś</h3></div>
        <div class="w-stats">
          <div class="w-stat"><b>${t.calls}</b><span>telefonów</span></div>
          <div class="w-stat"><b>${t.talks}</b><span>rozmów</span></div>
          <div class="w-stat"><b>${t.offers}</b><span>ofert</span></div>
          <div class="w-stat wide"><span>Sprzedane: <b style="color:var(--ink)">${t.sold}</b></span><span>prowizja <b>${money(t.commission)}</b></span></div>
        </div>${minutesBlock}</div>
      <div class="w-panel"><h3>Terminy na dziś (${plan.length})</h3>
        ${plan.length ? `<ul class="w-day">${plan.map((l) => { const s = segOf(l), late = new Date(l.next_contact_at) < new Date();
          return `<li><button type="button" data-id="${l.id}" style="--c:${SEG[s] ? SEG[s].color : "var(--line)"}"><b class="${late && s !== "retry" ? "late" : ""}">${hhmm(l.next_contact_at)}</b><span>${esc(title(l))}</span></button></li>`; }).join("")}</ul>`
          : `<p class="w-goal-note">Na dziś nic nie jest umówione.</p>`}
        <p class="w-goal-note"><a href="/crm/calendar/">Cały kalendarz →</a></p></div>
      ${announcements.length ? `<div class="w-panel"><h3>Solid info</h3>${announcements.map((a) =>
        `<div class="w-info-item${a.pinned ? " pinned" : ""}"><strong>${esc(a.title)}</strong>${a.body ? `<p>${esc(a.body)}</p>` : ""}</div>`).join("")}</div>` : ""}`;
  }
  /* „ (pracujesz od 9:12, 3 h 20 min)” – z dnia pracy zapisanego przez serwer */
  function workdayText() {
    const w = summary && summary.workday;
    if (!w || !w.started_at) return "";
    const m = w.worked_minutes, h = Math.floor(m / 60), r = m % 60;
    const br = w.break_minutes ? `, w tym przerwy ${w.break_minutes} min` : "";
    return ` (${w.ended_at ? "praca" : "pracujesz"} od ${hhmm(w.started_at)}${w.ended_at ? ` do ${hhmm(w.ended_at)}` : ""}: ${h ? `${h} h ` : ""}${r} min${br})`;
  }
  document.addEventListener("crm:view", renderSide);

  /* ================= DIALER (pasek na dole) ================= */
  let callTimer = null, callStart = null;
  async function loadDialer() {
    try { const r = await api("/api/dialer/status/"); if (r.ok) dialer = await r.json(); } catch { /* brak sieci */ }
    renderDialbar();
  }
  function renderDialbar() {
    $("db-dot").className = "db-dot" + (dialer.online ? " on" : "");
    const call = dialer.call;
    let state = dialer.online ? "Telefon połączony – gotowy do pracy" : dialer.paired ? "Telefon nie odpowiada (otwórz Solid Dialer)" : "Bez telefonu: „Zadzwoń” otworzy wybieranie numeru";
    if (call && call.state === "pending") state = `Czekam na telefon: ${call.phone}`;
    if (call && call.state === "dialing") state = `Dzwonię: ${call.phone}`;
    $("db-state").textContent = state;
    $("db-setup").hidden = dialer.online;
    $("db-setup").textContent = dialer.paired ? "Telefon" : "Połącz telefon";
    const dialing = call && (call.state === "dialing" || call.state === "pending");
    $("db-timer").hidden = !dialing;
    if (dialing && !callTimer) { callStart = Date.now(); callTimer = setInterval(tick, 1000); tick(); }
    if (!dialing && callTimer) { clearInterval(callTimer); callTimer = null; }
    const t = summary && summary.today;
    $("db-min").textContent = t ? `Dziś: ${t.calls} tel. · ${CRM.talk(t.minutes_today)} rozmów · ta godzina ${CRM.talk(t.minutes_this_hour)} / ${t.goal_minutes_per_hour} min` : "";
    const btn = $("call-btn");
    if (btn) { btn.classList.toggle("busy", !!dialing); btn.querySelector("span").textContent = dialing ? "Dzwonię…" : "Zadzwoń"; }
  }
  function tick() { const s = Math.floor((Date.now() - callStart) / 1000); $("db-timer").textContent = `${p2(Math.floor(s / 60))}:${p2(s % 60)}`; }
  $("db-setup").addEventListener("click", () => { const b = $("dialer-open"); if (b) b.click(); });
  let dialerPoll = null;
  function pollDialerSoon() {
    clearInterval(dialerPoll);
    let n = 0;
    dialerPoll = setInterval(async () => {
      await loadDialer(); n++;
      const c = dialer.call;
      if (!c || c.state === "done" || c.state === "cancelled" || n > 600) {
        clearInterval(dialerPoll);
        if (c && c.state === "done") {
          setCallState(c.answered ? `Rozmowa zakończona: <b>${Math.floor((c.duration_seconds || 0) / 60)} min ${(c.duration_seconds || 0) % 60} s</b>. Wybierz wynik.` : "Klient <b>nie odebrał</b>. Wybierz „1 Nie odebrał”.");
          if (!c.answered && current && !form && c.lead === current.id) pick("no_answer");
          loadSummary();
        }
      }
    }, 2000);
  }
  const setCallState = (html) => { const el = $("call-state"); if (el) el.innerHTML = html; };
  async function callNow() {
    if (!current) return;
    if (tourDemo) { setCallState("W samouczku nie dzwonimy naprawdę. Normalnie telefon sam wybierze numer."); return; }
    if (dialer.online) {
      const r = await jsonReq("/api/dialer/call/", "POST", { lead: current.id });
      if (r.error) { toast(r.error, true); return; }
      dialer = { ...dialer, ...r.data }; renderDialbar();
      setCallState("Telefon wybiera numer… Po rozmowie wybierz wynik.");
      pollDialerSoon();
    } else {
      location.href = "tel:" + current.phone.replace(/[^\d+]/g, "");
      setCallState("Otwarto wybieranie numeru. Po rozmowie wybierz wynik (klawisze 1–8).");
    }
  }

  /* ================= ŚRODEK: KLIENT ================= */
  /* empty = komunikat, gdy „Następny klient” nic nie znalazł (pokazany w środku, nie w dymku nad paskiem „Cofnij”) */
  function welcome(empty) {
    current = null; form = null; renderList();
    const later = leads.filter((l) => !isDue(l)).sort(cmp)[0];
    $("lead-pane").innerHTML = empty
      ? `<div class="w-panel w-welcome done"><h2>Na teraz wszystko zrobione ✓</h2>
          <p>${esc(empty)}</p>
          ${later ? `<p>Najbliższy kontakt: <b>${esc(title(later))}</b> · ${esc(when(later.next_contact_at))}. Wróci tu sam, gdy przyjdzie jego pora.</p>` : ""}
          <p class="muted">Możesz dodać klienta z polecenia („+ Nowy kontakt” po lewej) albo zadzwonić na dowolny numer z paska na dole.</p>
          <button type="button" class="w-next" data-next-btn>▶ Sprawdź jeszcze raz <kbd>N</kbd></button></div>`
      : `<div class="w-panel w-welcome"><h2>Gotowy do dzwonienia?</h2>
      <p>„Następny klient” sam wybierze, do kogo zadzwonić: najpierw follow-upy po ofertach, potem umówione oddzwonienia, ponowne próby, a na końcu nowe firmy z Twojej bazy.</p>
      <button type="button" class="w-next" data-next-btn>▶ Następny klient <kbd>N</kbd></button></div>`;
  }
  /* Nowy klient = karta od początku: środkowa kolumna ma własny pasek przewijania (window.scrollTo go nie rusza) */
  function toTop() { $("lead-pane").scrollTop = 0; window.scrollTo({ top: 0 }); }
  async function openLead(id, reason) {
    const r = await api(`/api/leads/${id}/`);
    if (!r.ok) { toast("Nie udało się wczytać klienta.", true); return false; }
    current = await r.json(); current._reason = reason || null; form = null;
    renderCard(); renderList();
    toTop();
    const item = document.querySelector(`#lead-list [data-id="${id}"]`); if (item) item.scrollIntoView({ block: "nearest" });
    return true;
  }
  CRM.openLead = async (id) => { if (!leads.some((l) => l.id === id)) await loadLeads(); const ok = await openLead(id); window.scrollTo({ top: 0 }); return ok; };

  const lastOffer = (d) => (d.activities || []).find((a) => a.outcome === "offer" && a.offer);
  const lastNote = (d) => (d.activities || []).find((a) => a.note);
  function reasonHtml(d) {
    const s = segOf(d), t = d.next_contact_at, late = t && new Date(t) < Date.now() - 60000 && ["offer", "callback"].includes(s);
    const lateTxt = late ? ` · <b style="color:var(--late)">spóźnione ${rel(t).replace(" temu", "")}</b>` : t ? ` · ${esc(when(t))}` : "";
    let head = "", desc = "";
    if (s === "offer") {
      const o = lastOffer(d);
      head = d.status === "offer" ? "Follow-up po wysłanej ofercie" : "Zainteresowany – wyślij ofertę";
      desc = o ? `Oferta <b>${esc(o.offer.product_name)} × ${o.offer.quantity}</b>, ${o.offer.term_months} mies., <b>${money(o.offer.monthly_total)}</b>/mies. · wysłana ${esc(when(o.created_at))}${o.offer.client_email ? ` na ${esc(o.offer.client_email)}` : ""}` : "Klient chce poznać ofertę.";
    } else if (s === "callback") {
      head = "Umówione oddzwonienie";
      desc = d.knows_caller ? "Klient <b>wie, że dzwonimy z ofertą T-Mobile</b>." : "<b>Nie mówiliśmy jeszcze, skąd dzwonimy</b> – zacznij od przedstawienia się.";
    } else if (s === "retry") {
      head = `Ponowna próba – nie odebrał ${d.owner_no_answers || d.attempts_count}×`;
      desc = "Jeszcze z nim nie rozmawialiśmy.";
    } else if (s === "new") { head = "Nowa firma – pierwszy telefon"; desc = "Nikt jeszcze tu nie dzwonił."; }
    else { head = "Klient zamknięty"; desc = esc((CRM.STATUS[d.status] || [d.status])[0]); }
    const topic = d.next_contact_note ? `<div class="wr-d"><em>Sprawa:</em> ${esc(d.next_contact_note)}</div>` : "";
    const note = lastNote(d);
    const noteHtml = note ? `<div class="wr-d"><em>Ostatni komentarz (${note.agent === me.id ? "Ty" : esc(note.agent_name || "")}, ${esc(when(note.created_at))}):</em> ${esc(note.note)}</div>` : "";
    return `<div class="w-reason${late ? " late" : ""}" style="--c:${SEG[s] ? SEG[s].color : "var(--muted)"}"><span class="wr-ic">${SEG[s] ? SEG[s].icon : "•"}</span>
      <div><div class="wr-t">${head}${lateTxt}</div><div class="wr-d">${desc}</div>${topic}${noteHtml}</div></div>`;
  }
  /* Najważniejsze na karcie: firma, telefon, osoba (gdy jest) i e-mail. NIP, REGON i adres – małym drukiem. */
  const hasCompany = (d) => !!(d.company_name && d.company_name.trim());
  function personHtml(d) {
    if (d.contact_name && hasCompany(d)) return `<div class="w-person"><span aria-hidden="true">👤</span> ${esc(d.contact_name)}</div>`;
    if (hasCompany(d)) return `<div class="w-person none">Osoba kontaktowa nieznana · <button type="button" class="w-link" data-edit>dopisz</button></div>`;
    return "";
  }
  function factsHtml(d) {
    const mail = d.email
      ? `<span class="w-mail-l">E-mail</span><a class="w-mail" href="mailto:${esc(d.email)}">${esc(d.email)}</a> ${CRM.copyButton(d.email, "e-mail")}`
      : `<span class="w-mail-l">E-mail</span><span class="w-mail none">brak</span>`;
    return `<div class="w-mail-row">${mail}<button type="button" class="btn-mini" id="edit-data">${d.email && d.contact_name ? "Zmień dane" : "Uzupełnij dane"}</button></div>`;
  }
  function metaHtml(d) {
    const parts = [];
    if (d.company_nip) parts.push(`NIP ${esc(d.company_nip)} ${CRM.copyButton(d.company_nip, "NIP")} <button type="button" class="w-link" data-reg="${esc(d.company_nip)}" title="Kopiuje NIP i otwiera wyszukiwarkę firm">CEIDG ↗</button>`);
    if (d.company_regon) parts.push(`REGON ${esc(d.company_regon)}`);
    if (d.company_krs) parts.push(`KRS ${esc(d.company_krs)}`);
    const addr = [d.company_street, [d.company_postal_code, d.company_city].filter(Boolean).join(" ")].filter(Boolean).join(", ");
    if (addr) parts.push(esc(addr));
    return parts.length ? `<div class="w-meta">${parts.join('<span class="sep">·</span>')}</div>` : "";
  }
  function renderCard() {
    const d = current;
    const tel = d.phone.replace(/[^\d+]/g, "");
    const st = CRM.STATUS[d.status] || [d.status, "#64748b"];
    const badges = [`<span class="w-badge" style="--c:${st[1]}">${esc(st[0])}</span>`];
    if (isCompanyEntity(d.company_name)) badges.push(`<span class="w-badge spolka" title="Spółka: zwykle trzeba trafić do osoby decyzyjnej (zarząd, właściciel)">Spółka</span>`);
    if (d.knows_caller) badges.push(`<span class="w-badge" style="--c:var(--ok)">Wie, skąd dzwonimy</span>`);
    if (d.operators) badges.push(`<span class="w-badge">Sieć: ${esc(d.operators.split(",").map((c) => (CRM.OPERATORS.find((o) => o[0] === c) || [c, c])[1]).join(", "))}</span>`);
    if (d.transferred_from) badges.push(`<span class="w-badge" style="--c:#2563eb">Przepisany od ${esc(d.transferred_from)}</span>`);
    if (d.missed_calls > 0) badges.push(`<span class="w-badge" style="--c:var(--late)">Nie odebrał w terminie ×${d.missed_calls}</span>`);
    const closed = CLOSED.includes(d.status);
    $("lead-pane").innerHTML = `
      ${reasonHtml(d)}
      <div class="w-panel">
        <div class="w-head"><div><h1 class="w-name">${esc(title(d))}</h1>${personHtml(d)}<div class="w-badges">${badges.join("")}</div></div></div>
        <div class="w-call">
          <a class="w-phone" href="tel:${esc(tel)}">${esc(d.phone)}</a>
          <button type="button" class="w-call-btn" id="call-btn"><svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor" aria-hidden="true"><path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1l-2.3 2.2z"/></svg><span>Zadzwoń</span> <kbd class="w-kbd">Z</kbd></button>
          ${CRM.copyButton(d.phone)}
          ${d.phone_alt ? `<span class="w-alt">drugi numer: <a href="tel:${esc(d.phone_alt.replace(/[^\d+]/g, ""))}">${esc(d.phone_alt)}</a></span>` : ""}
          <span class="w-call-state" id="call-state"></span>
        </div>
        <div class="w-facts" id="facts">${factsHtml(d)}</div>
        ${metaHtml(d)}
      </div>
      ${closed ? "" : `<div class="w-panel" id="out-panel">
        <div class="w-out-title"><h2>Jak poszła rozmowa?</h2><small>klawisze 1–8</small></div>
        <div class="w-outs" role="radiogroup">${OUTCOMES.map((o) => `<button type="button" class="w-out" role="radio" aria-checked="false" data-out="${o.key}" style="--c:${o.color}"><kbd>${o.n}</kbd><b>${o.label}</b><small>${o.hint}</small></button>`).join("")}</div>
        <div id="step"></div>
        <div class="w-comment"><label for="act-note">Komentarz (opcjonalnie)</label><div class="phrases" id="phrases" role="group" aria-label="Wzory komentarzy"></div><textarea id="act-note" rows="2" placeholder="Krótko: co powiedział klient"></textarea></div>
        <p class="w-error" id="act-error" hidden></p>
        <div class="w-save">
          <button type="button" class="btn btn-primary" id="save-next" disabled>Zapisz i dalej ↵</button>
          <button type="button" class="btn btn-secondary" id="save-only" disabled>Tylko zapisz</button>
          <button type="button" class="link-btn" id="note-only" title="Zapisuje sam komentarz w historii, bez wyniku rozmowy">Tylko notatka</button>
          <button type="button" class="link-btn" id="reset-form">Wyczyść (Esc)</button>
          <span class="w-save-hint" id="save-hint">Wybierz wynik rozmowy</span>
        </div>
      </div>`}
      <div class="w-panel" id="client-panel">
        <div class="w-sec-title"><h2>Stałe informacje o kliencie</h2><small class="w-status" id="cl-status"></small></div>
        <div class="w-field w-note-box"><label for="cl-comment" class="sr-only">Informacje o kliencie (zostają na stałe)</label>
          <textarea id="cl-comment" rows="2" maxlength="2000" placeholder="np. 3 numery w Play, umowa do marca, decyduje właściciel">${esc(d.client_comment || "")}</textarea></div>
      </div>
      <div class="w-panel"><div class="w-sec-title"><h2>Historia kontaktów (${d.activities.length})</h2></div><ul class="w-hist" id="hist">${histHtml(d)}</ul></div>`;
    if (!closed) { paintPhrases(null); $("act-note").addEventListener("keydown", (e) => e.stopPropagation()); $("act-note").addEventListener("input", syncChips); }
    wireClient();
    if (dialer.call && dialer.call.lead === d.id && dialer.call.state === "done") setCallState(dialer.call.answered ? `Ostatnia rozmowa: <b>${Math.round((dialer.call.duration_seconds || 0) / 60)} min</b>` : "Ostatnio: nie odebrał");
    renderDialbar();
  }
  function histHtml(d) {
    if (!d.activities.length) return `<li><span></span><span class="muted">To pierwszy kontakt z tym klientem.</span></li>`;
    return d.activities.map((a) => {
      const who = a.agent === me.id ? "Ty" : esc(a.agent_name || "–");
      let extra = "";
      if (a.offer) extra += `<div class="h-extra">Oferta <b>${esc(a.offer.product_name)} × ${a.offer.quantity}</b>, ${a.offer.term_months} mies., ${money(a.offer.monthly_total)}/mies.${a.offer.recipient ? ` → ${esc(a.offer.recipient)}` : ""}${a.offer.sent ? " (wysłana)" : ""}</div>`;
      if (a.sale_items && a.sale_items.length) extra += `<div class="h-extra">${a.sale_items.map((i) => `${esc(i.product_name)} × ${i.quantity}${i.term_months ? `, ${i.term_months} mies.` : ""}: <b>${money(i.total)}</b>${Number(i.commission) ? ` (prowizja ${money(i.commission)})` : ""}`).join("<br>")}</div>`;
      if (a.outcome === "skipped") extra += `<div class="h-extra">Powód: ${esc(SKIP_LABEL[a.skip_reason] || "inny")}</div>`;
      if (a.rescheduled_to) extra += `<div class="h-extra">Następny kontakt: <b>${esc(when(a.rescheduled_to))}</b></div>`;
      if (a.call_seconds) extra += `<div class="h-extra">Rozmowa: ${Math.floor(a.call_seconds / 60)} min ${a.call_seconds % 60} s</div>`;
      return `<li style="--c:${OUT_COLOR[a.outcome] || "#64748b"}"><div class="h-when"><b>${hhmm(a.created_at)}</b>${esc(dayShort(a.created_at))}</div>
        <div><span class="h-out">${esc(OUT_LABEL[a.outcome] || a.outcome)}</span><span class="h-who">${who}</span>${a.note ? `<p class="h-note">${esc(a.note)}</p>` : ""}${extra}</div></li>`;
    }).join("");
  }

  /* ---------- dane klienta: sieć, informacje (zapis od razu), edycja osoby / numeru / e-maila ---------- */
  function wireClient() {
    const d = current;
    const status = (t, ok) => { const el = $("cl-status"); if (el) { el.textContent = t; el.classList.toggle("ok", !!ok); } };
    const save = async (body) => {
      status("Zapisuję…");
      const r = await jsonReq(`/api/leads/${d.id}/`, "PATCH", body);
      if (r.error) { status(r.error); return false; }
      Object.assign(d, body); status("Zapisano ✓", true); return true;
    };
    CRM.saveLeadPatch = save;
    const ta = $("cl-comment"); let timer = null, last = ta.value;
    const flush = () => { clearTimeout(timer); if (ta.value.trim() === last.trim()) return; last = ta.value; save({ client_comment: ta.value.trim() }); };
    ta.addEventListener("input", () => { clearTimeout(timer); status("Zapisze się za chwilę…"); timer = setTimeout(flush, 900); });
    ta.addEventListener("blur", flush);
    ta.addEventListener("keydown", (e) => e.stopPropagation());
  }
  function editData() {
    const d = current, box = $("facts");
    box.innerHTML = `<label class="w-field">Osoba kontaktowa<input id="ed-name" maxlength="150" placeholder="np. Pan Marek (właściciel)"></label>
      <label class="w-field">Dodatkowy telefon (np. do szefa, druga komórka)<input id="ed-alt" maxlength="30" inputmode="tel"></label>
      <label class="w-field">E-mail<input id="ed-mail" type="email" maxlength="254" placeholder="np. biuro@firma.pl"></label>
      <div class="w-edit-actions"><button type="button" class="btn btn-primary" id="ed-save">Zapisz</button><button type="button" class="btn btn-secondary" id="ed-cancel">Anuluj</button></div>
      <p class="w-error" id="ed-err" hidden></p>`;
    $("ed-name").value = d.contact_name || ""; $("ed-alt").value = d.phone_alt || ""; $("ed-mail").value = d.email || "";
    $("ed-name").focus();
    const done = () => { box.innerHTML = factsHtml(d); };
    const submit = async () => {
      const body = { contact_name: $("ed-name").value.trim(), phone_alt: $("ed-alt").value.trim(), email: $("ed-mail").value.trim() };
      const r = await jsonReq(`/api/leads/${d.id}/`, "PATCH", body);
      if (r.error) { $("ed-err").textContent = r.error; $("ed-err").hidden = false; return; }
      Object.assign(d, body); renderCard(); toast("Dane klienta zapisane."); loadLeads();
    };
    $("ed-save").addEventListener("click", submit); $("ed-cancel").addEventListener("click", done);
    box.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); submit(); } if (e.key === "Escape") done(); }));
  }

  /* ---------- sieć klienta: jeden wybór (zwykle klient jest w jednej sieci), pytamy tylko po rozmowie ---------- */
  function networkHtml(d) {
    const cur = (d.operators || "").split(",").filter(Boolean)[0] || "";
    return `<div class="w-field w-net"><span>W jakiej sieci jest teraz klient? <small class="muted">(zapisuje się od razu, kliknij ponownie, żeby wyczyścić)</small></span>
      <div class="w-ops" role="radiogroup">${CRM.OPERATORS.map(([c, l]) => `<button type="button" class="w-chip" role="radio" data-op="${c}" aria-checked="${c === cur}" aria-pressed="${c === cur}">${esc(l)}</button>`).join("")}</div></div>`;
  }
  function wireNetwork() {
    document.querySelectorAll("#step [data-op]").forEach((b) => b.addEventListener("click", async () => {
      const on = b.getAttribute("aria-pressed") !== "true";
      const prev = current.operators || "";
      document.querySelectorAll("#step [data-op]").forEach((x) => { const v = on && x === b; x.setAttribute("aria-pressed", String(v)); x.setAttribute("aria-checked", String(v)); });
      const value = on ? b.dataset.op : "";
      const ok = CRM.saveLeadPatch ? await CRM.saveLeadPatch({ operators: value }) : false;
      if (!ok) { current.operators = prev; toast("Nie udało się zapisać sieci.", true); }
    }));
  }
  function paintPhrases(o) {
    const box = $("phrases"); if (!box) return;
    const list = (o && PHRASES[o]) || [];
    box.hidden = !list.length;
    box.innerHTML = list.map((p) => `<button type="button" data-phrase="${esc(p)}" aria-pressed="false">${esc(p)}</button>`).join("");
    box.querySelectorAll("[data-phrase]").forEach((b) => b.addEventListener("click", () => toggleNote(b.dataset.phrase)));
    syncChips();
  }
  /* Wzory i powody odmowy działają jak przełączniki: klik dopisuje do komentarza, drugi klik usuwa.
     Komentarz to części rozdzielone „; ” – gdy agent sam usunie tekst, przycisk się odznacza. */
  const noteParts = () => ($("act-note") ? $("act-note").value.split(";").map((x) => x.trim()).filter(Boolean) : []);
  const same = (a, b) => a.toLowerCase() === b.toLowerCase();
  function toggleNote(text) {
    const ta = $("act-note"); if (!ta) return;
    const parts = noteParts();
    const i = parts.findIndex((p) => same(p, text));
    if (i >= 0) parts.splice(i, 1); else parts.push(parts.length ? text.toLowerCase() : text);
    if (parts.length) parts[0] = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
    ta.value = parts.join("; ");
    syncChips();
  }
  function syncChips() {
    const parts = noteParts();
    document.querySelectorAll("#out-panel [data-phrase], #out-panel [data-refuse]").forEach((b) => {
      const t = b.dataset.phrase || b.dataset.refuse;
      b.setAttribute("aria-pressed", String(parts.some((p) => same(p, t))));
    });
  }

  /* ================= WYNIK ROZMOWY ================= */
  const atHour = (days, h, m = 0) => { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(h, m, 0, 0); return d; };
  const nextWorkday = (h, m = 0) => { const d = atHour(1, h, m); while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1); return d; };
  const DAY_SHORT = ["Niedz.", "Pon.", "Wt.", "Śr.", "Czw.", "Pt.", "Sob."];
  /* „Jutro 10:00”, a w piątek / sobotę „Pon. 10:00” – żeby było widać, że weekend jest pomijany */
  const wdLabel = (h, text) => () => { const d = nextWorkday(h), t = new Date(Date.now() + 864e5); return `${d.toDateString() === t.toDateString() ? "Jutro" : DAY_SHORT[d.getDay()]} ${text}`; };
  const PRESETS = {
    callback: [["Za 1 h", () => new Date(Date.now() + 3600e3)], ["Za 2 h", () => new Date(Date.now() + 7200e3)], ["Dziś 15:00", () => atHour(0, 15)], [wdLabel(9, "9:00"), () => nextWorkday(9)], [wdLabel(13, "13:00"), () => nextWorkday(13)], ["Za tydzień", () => atHour(7, 10)]],
    interested: [["Za 1 h", () => new Date(Date.now() + 3600e3)], ["Dziś 15:00", () => atHour(0, 15)], [wdLabel(10, "10:00"), () => nextWorkday(10)], ["Za 2 dni", () => atHour(2, 10)], ["Za tydzień", () => atHour(7, 10)]],
    offer: [[wdLabel(10, "10:00"), () => nextWorkday(10)], [wdLabel(15, "15:00"), () => nextWorkday(15)], ["Za 2 dni", () => atHour(2, 10)], ["Za tydzień", () => atHour(7, 10)]],
    no_answer: [["Za 2 h", () => new Date(Date.now() + 7200e3)], [wdLabel(9, "rano"), () => nextWorkday(9)], [wdLabel(15, "po 15"), () => nextWorkday(15, 30)]],
  };
  function whenField(kind, label, value, required) {
    return `<div class="w-row"><label class="w-field grow">${label}${required ? "" : " (opcjonalnie)"}
        <input id="f-when" autocomplete="off" placeholder="np. jutro 10:30 · pt 9 · za 2h" value="${esc(value ? fmtInput(value) : "")}"></label>
        <label class="w-field">lub z kalendarza<input type="datetime-local" id="f-native"></label></div>
      <div class="w-chips">${PRESETS[kind].map(([l], i) => `<button type="button" class="w-chip" data-preset="${kind}:${i}">${typeof l === "function" ? l() : l}</button>`).join("")}</div>
      <div class="w-when-preview" id="f-preview"></div>`;
  }
  function planPicker(sel) {
    if (!plans.length) return `<p class="w-info">Brak taryf w katalogu. Manager dodaje je w „Zarządzanie → Taryfy i produkty”.</p>`;
    return `<div class="w-field"><span>Taryfa</span><div class="w-chips" id="f-plans">${plans.map((p) =>
      `<button type="button" class="w-chip plan" data-plan="${p.id}" aria-pressed="${p.id === sel.product}"><b>${esc(p.name)}</b><small>${money(sel.promo && p.promo_price ? p.promo_price : p.price)}</small></button>`).join("")}</div></div>
      <div class="w-row">
        <div class="w-field"><span>Liczba numerów</span><div class="w-qty"><button type="button" data-qty="-1" aria-label="Mniej">−</button><input id="f-qty" type="number" min="1" max="500" value="${sel.quantity}" inputmode="numeric"><button type="button" data-qty="1" aria-label="Więcej">+</button></div></div>
        <div class="w-field"><span>Okres umowy</span><div class="w-toggle" id="f-term"><button type="button" data-term="36" aria-pressed="${sel.term === 36}">36 mies.</button><button type="button" data-term="24" aria-pressed="${sel.term === 24}">24 mies.</button></div></div>
        <label class="w-check"><input type="checkbox" id="f-promo" ${sel.promo ? "checked" : ""}> Promocja: ${esc(config.promo_label || "tak")}</label>
      </div>
      <div class="w-sum" id="f-sum"></div>`;
  }
  function calcPlan(sel) {
    const p = plans.find((x) => x.id === sel.product);
    if (!p) return null;
    const first = sel.promo && p.promo_price != null ? Number(p.promo_price) : Number(p.price);
    const extra = Number(p.extra_price || 0), q = Math.max(1, sel.quantity || 1);
    const monthly = extra ? first + (q - 1) * extra : first * q;
    const mult = Number(sel.term === 24 ? config.commission_24 : config.commission_36) || 0;
    return { p, monthly, commission: monthly * mult };
  }
  function paintSum() {
    const el = $("f-sum"); if (!el || !form.plan) return;
    const c = calcPlan(form.plan);
    el.innerHTML = c ? `<div><span>Zobowiązanie miesięczne (netto)</span><br><b>${money(c.monthly)}</b>${form.plan.quantity > 1 ? ` <span>(${money(c.monthly / form.plan.quantity)} za numer)</span>` : ""}</div>
      <div class="comm"><span>Twoja prowizja przy sprzedaży (ok.)</span><br><b>${money(c.commission)}</b></div>` : `<span>Wybierz taryfę.</span>`;
    document.querySelectorAll("#f-plans [data-plan]").forEach((b) => { const p = plans.find((x) => x.id === Number(b.dataset.plan)); b.setAttribute("aria-pressed", String(Number(b.dataset.plan) === form.plan.product)); b.querySelector("small").textContent = money(form.plan.promo && p.promo_price != null ? p.promo_price : p.price); });
    document.querySelectorAll("#f-term [data-term]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.term) === form.plan.term)));
    const prev = $("f-mail-preview"); if (prev && !prev.hidden) prev.textContent = offerPreviewText();
  }
  function wirePlan() {
    document.querySelectorAll("#f-plans [data-plan]").forEach((b) => b.addEventListener("click", () => { form.plan.product = Number(b.dataset.plan); paintSum(); }));
    document.querySelectorAll("#f-term [data-term]").forEach((b) => b.addEventListener("click", () => { form.plan.term = Number(b.dataset.term); paintSum(); }));
    const q = $("f-qty");
    if (q) {
      q.addEventListener("input", () => { form.plan.quantity = Math.max(1, Math.min(500, parseInt(q.value, 10) || 1)); paintSum(); });
      document.querySelectorAll("[data-qty]").forEach((b) => b.addEventListener("click", () => { form.plan.quantity = Math.max(1, Math.min(500, form.plan.quantity + Number(b.dataset.qty))); q.value = form.plan.quantity; paintSum(); }));
    }
    const pr = $("f-promo"); if (pr) pr.addEventListener("change", () => { form.plan.promo = pr.checked; paintSum(); });
    paintSum();
  }
  function defaultPlan() {
    const o = current && lastOffer(current);
    if (o) { const p = plans.find((x) => x.id === o.offer.product || x.name === o.offer.product_name); return { product: p ? p.id : (plans[0] || {}).id, quantity: o.offer.quantity, term: o.offer.term_months, promo: o.offer.promo }; }
    const m = plans.find((p) => p.name === "M") || plans[0] || {};
    return { product: m.id, quantity: 1, term: Number(config.default_term) || 36, promo: false };
  }
  function offerPreviewText() {
    const c = calcPlan(form.plan); if (!c) return "";
    const lines = [`Oferta: ${c.p.name}`, `Liczba numerów: ${form.plan.quantity}`, `Okres umowy: ${form.plan.term} miesięcy`];
    if (form.plan.promo && config.promo_label) lines.push(`Promocja: ${config.promo_label}`);
    lines.push(`Zobowiązanie miesięczne: ${money(c.monthly)} netto`);
    if (c.p.features) lines.push("", ...c.p.features.split("\n").filter(Boolean).map((f) => "• " + f));
    return lines.join("\n");
  }

  function stepHtml(o) {
    const d = current;
    if (o === "no_answer") {
      const auto = d.suggested_retry_at ? new Date(d.suggested_retry_at) : null;
      const missed = ["offer", "callback"].includes(segOf(d)) && d.next_contact_at && new Date(d.next_contact_at) <= Date.now() + 5 * 60000;
      const info = missed ? "Umówiony kontakt nie doszedł do skutku. Klient zostaje u Ciebie, ponowna próba za godzinę (możesz zmienić)."
        : auto ? `System sam ustawi ponowną próbę na <b>${esc(fmtLong(auto))}</b> (o innej porze dnia niż poprzednio). Możesz wpisać własny termin.`
          : "To ostatnia próba: po zapisie klient wróci do wspólnej puli.";
      return `<div class="w-step"><p class="w-info">${info}</p>${auto || missed ? whenField("no_answer", "Własny termin ponownej próby", missed ? new Date(Date.now() + 3600e3) : null, false) : ""}</div>`;
    }
    if (o === "callback") {
      return `<div class="w-step"><h3>Kiedy oddzwonić?</h3>${whenField("callback", "Termin", null, true)}
        <label class="w-field grow">W jakiej sprawie (opcjonalnie)<input id="f-topic" maxlength="255" value="${esc(d.next_contact_note || "")}" placeholder="np. zajęty, prosił po 15; decyduje wspólnik"></label>
        <label class="w-check"><input type="checkbox" id="f-intro" ${d.knows_caller ? "checked" : ""}> Klient wie, że dzwonimy z ofertą T-Mobile</label></div>`;
    }
    if (o === "interested") {
      return `<div class="w-step"><h3>Zainteresowany – kiedy wrócić do klienta?</h3>
        <p class="w-info">Klient trafia do grupy <b>Oferty</b> (najważniejsi). Gdy dostaniesz e-mail, wybierz przy nim „4 Wyślij ofertę”.</p>
        ${whenField("interested", "Termin", nextWorkday(10), true)}
        <label class="w-field grow">Czego brakuje do oferty<input id="f-topic" maxlength="255" value="${esc(d.next_contact_note || "Wysłać ofertę – dopytać o e-mail")}"></label>
        <label class="w-field grow">E-mail klienta (jeśli podał)<input id="f-email" type="email" maxlength="254" value="${esc(d.email || "")}" placeholder="np. biuro@firma.pl"></label></div>`;
    }
    if (o === "offer") {
      const target = config.mode === "direct" ? "prosto na e-mail klienta" : `do: <b>${esc(config.forward_name || "osoby wysyłającej")}</b>${config.forward_email ? ` (${esc(config.forward_email)})` : " – adres ustawia manager"}`;
      return `<div class="w-step"><h3>Oferta dla klienta</h3>${planPicker(form.plan)}
        <label class="w-field grow">E-mail klienta${config.mode === "direct" ? "" : " (żeby osoba wysyłająca wiedziała, dokąd)"}<input id="f-email" type="email" maxlength="254" value="${esc(d.email || "")}" placeholder="np. biuro@firma.pl"></label>
        ${whenField("offer", "Kiedy zadzwonić w sprawie oferty (follow-up)", nextWorkday(10), true)}
        <label class="w-field grow">Sprawa przy follow-upie<input id="f-topic" maxlength="255" value="Omówić wysłaną ofertę"></label>
        <p class="w-info">Oferta pójdzie ${target}. <button type="button" class="w-link" id="f-show-preview">Podgląd oferty</button></p>
        <pre class="w-preview" id="f-mail-preview" hidden></pre></div>`;
    }
    if (o === "sold") {
      return `<div class="w-step"><h3>Co klient kupił?</h3>${planPicker(form.plan)}<p class="w-info">Klient zostanie zamknięty jako <b>sprzedany</b>. Prowizja doliczy się do Twoich wyników.</p>
        <p class="w-info">Zapytaj o polecenie: „Kogo Pan zna, kto jeszcze mógłby skorzystać?” → <button type="button" class="w-link" data-referral>+ dodaj polecony kontakt</button></p></div>`;
    }
    if (o === "refused") return `<div class="w-step"><h3>Dlaczego odmówił? (dopisze się do komentarza)</h3><div class="w-chips">${REFUSE_CHIPS.map((c) => `<button type="button" class="w-chip" data-refuse="${esc(c)}">${esc(c)}</button>`).join("")}</div></div>`;
    if (o === "wrong_number") return `<div class="w-step"><h3>Zły numer</h3>
      <p class="w-info">Bez nowego numeru klient zostanie zamknięty (<b>nie dzwonić</b>).</p>
      <label class="w-field grow">Dostałeś inny numer do tej firmy? (np. żona podała numer męża, sekretariat – numer szefa)<input id="f-newphone" maxlength="30" inputmode="tel" placeholder="np. 600 100 200"></label>
      <p class="w-info" id="f-newphone-info">Jeśli wpiszesz nowy numer, klient <b>zostaje u Ciebie</b>: nowy numer staje się głównym, stary zapisze się jako dodatkowy, a w historii zostanie notatka.</p></div>`;
    if (o === "skipped") return `<div class="w-step"><h3>Dlaczego pomijasz tę firmę?</h3><div class="w-chips" role="radiogroup">${CRM.SKIP_REASONS.map(([c, l]) => `<button type="button" class="w-chip" role="radio" aria-checked="false" data-skip="${c}">${esc(l)}</button>`).join("")}</div><p class="w-info">Pominięcie nie liczy się jako telefon ani próba.</p></div>`;
    return "";
  }
  const SAVE_HINT = { no_answer: "Enter zapisuje i bierze następnego", callback: "Wpisz termin, Enter zapisuje", interested: "Sprawdź termin, Enter zapisuje", offer: "Wybierz taryfę i termin follow-upu", sold: "Wybierz taryfę i liczbę numerów", refused: "Enter zapisuje i bierze następnego", wrong_number: "Enter zapisuje i bierze następnego", skipped: "Wybierz powód pominięcia" };
  function pick(o) {
    if (!current || CLOSED.includes(current.status) || !$("out-panel")) return;
    if (form && form.outcome === o) { resetForm(); return; }
    form = { outcome: o, plan: ["offer", "sold"].includes(o) ? defaultPlan() : null, skip: "" };
    document.querySelectorAll("[data-out]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.out === o)));
    $("step").innerHTML = stepHtml(o) + (TALKED.includes(o) ? `<div class="w-step w-step-net">${networkHtml(current)}</div>` : "");
    paintPhrases(o);
    $("act-error").hidden = true;
    $("save-next").disabled = $("save-only").disabled = false;
    $("save-hint").textContent = keysOn() ? SAVE_HINT[o] || "" : "";
    wireStep();
    const focusEl = $("f-when") && o === "callback" ? $("f-when") : null;
    if (focusEl) { focusEl.focus(); focusEl.select(); } else $("save-next").focus({ preventScroll: true });
    $("out-panel").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  function resetForm() {
    form = null;
    if (!$("out-panel")) return;
    document.querySelectorAll("[data-out]").forEach((b) => b.setAttribute("aria-checked", "false"));
    $("step").innerHTML = ""; $("act-note").value = ""; $("act-error").hidden = true; paintPhrases(null);
    $("save-next").disabled = $("save-only").disabled = true;
    $("save-hint").textContent = "Wybierz wynik rozmowy";
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  }
  function paintWhen() {
    const inp = $("f-when"), pv = $("f-preview"); if (!inp || !pv) return;
    const v = inp.value.trim();
    if (!v) { pv.textContent = ""; pv.className = "w-when-preview"; return; }
    const d = parseWhen(v);
    if (!d) { pv.textContent = "Nie rozumiem terminu. Spróbuj np. „jutro 10:30”, „pt 9”, „za 2h”."; pv.className = "w-when-preview bad"; return; }
    pv.textContent = (d < new Date() ? "Ten termin już minął: " : "✓ ") + fmtLong(d); pv.className = "w-when-preview " + (d < new Date() ? "bad" : "ok");
  }
  function wireStep() {
    const inp = $("f-when");
    if (inp) {
      inp.addEventListener("input", paintWhen);
      inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); save(true); } if (e.key === "Escape") resetForm(); });
      const nat = $("f-native");
      nat.min = fmtNative(new Date());
      nat.addEventListener("change", () => { if (nat.value) { inp.value = fmtInput(new Date(nat.value)); paintWhen(); } });
      paintWhen();
    }
    document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
      const [kind, i] = b.dataset.preset.split(":"); inp.value = fmtInput(PRESETS[kind][Number(i)][1]()); paintWhen();
      document.querySelectorAll("[data-preset]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }));
    document.querySelectorAll("#step input:not(#f-when):not([type=checkbox])").forEach((i) => i.addEventListener("keydown", (e) => {
      e.stopPropagation(); if (e.key === "Enter" && i.id !== "f-qty") { e.preventDefault(); save(true); } if (e.key === "Escape") resetForm();
    }));
    if (form.plan) wirePlan();
    wireNetwork();
    const pv = $("f-show-preview");
    if (pv) pv.addEventListener("click", () => { const box = $("f-mail-preview"); box.hidden = !box.hidden; box.textContent = offerPreviewText(); });
    document.querySelectorAll("[data-refuse]").forEach((b) => b.addEventListener("click", () => toggleNote(b.dataset.refuse)));
    syncChips();
    document.querySelectorAll("[data-skip]").forEach((b) => b.addEventListener("click", () => {
      form.skip = b.dataset.skip;
      document.querySelectorAll("[data-skip]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      $("save-hint").textContent = "Enter zapisuje i bierze następnego";
      $("save-next").focus({ preventScroll: true });
    }));
  }
  const fail = (m) => { const e = $("act-error"); e.textContent = m; e.hidden = false; };
  function readWhen(required) {
    const inp = $("f-when"); if (!inp) return { ok: true, value: null };
    const raw = inp.value.trim();
    if (!raw) return required ? { ok: false, error: "Podaj termin." } : { ok: true, value: null };
    const d = parseWhen(raw);
    if (!d) return { ok: false, error: "Nie rozumiem terminu. Spróbuj np. „jutro 10:30”." };
    if (d < new Date()) return { ok: false, error: "Ten termin już minął." };
    return { ok: true, value: d };
  }

  let saving = false;
  async function save(goNext, noteOnly) {
    if (saving || !current) return;
    const note = $("act-note") ? $("act-note").value.trim() : "";
    if (noteOnly) {
      if (!note) { fail("Wpisz treść notatki."); return; }
      return submit({ lead: current.id, outcome: "note", note }, false);
    }
    if (!form) { fail("Wybierz wynik rozmowy (klawisze 1–8)."); return; }
    const o = form.outcome, lead = current.id;
    if (o === "offer") {
      if (!form.plan || !form.plan.product) { fail("Wybierz taryfę."); return; }
      const w = readWhen(true); if (!w.ok) { fail(w.error); return; }
      const email = $("f-email").value.trim();
      if (config.mode === "direct" && !email) { fail("Podaj e-mail klienta."); return; }
      return submitOffer({ lead, product: form.plan.product, quantity: form.plan.quantity, term_months: form.plan.term, promo: form.plan.promo,
        client_email: email, next_contact_at: w.value.toISOString(), next_contact_note: $("f-topic").value.trim(), note }, goNext);
    }
    const body = { lead, outcome: o, note };
    if (o === "interested") {
      const email = $("f-email").value.trim();
      if (email && email !== (current.email || "")) {
        const r = await jsonReq(`/api/leads/${lead}/`, "PATCH", { email });
        if (r.error) { fail(r.error); return; }
        current.email = email;
      }
    }
    if (o === "callback" || o === "no_answer" || o === "interested") {
      const w = readWhen(o !== "no_answer"); if (!w.ok) { fail(w.error); return; }
      if (w.value) { body.next_contact_at = w.value.toISOString(); if ($("f-topic")) body.next_contact_note = $("f-topic").value.trim(); }
      if (o === "callback") body.introduced = $("f-intro").checked;
    }
    if (o === "sold") {
      if (plans.length) {
        if (!form.plan.product) { fail("Wybierz taryfę."); return; }
        body.sale_lines = [{ product: form.plan.product, quantity: form.plan.quantity, term_months: form.plan.term, promo: form.plan.promo }];
      }
    }
    if (o === "skipped") { if (!form.skip) { fail("Wybierz powód pominięcia."); return; } body.skip_reason = form.skip; }
    if (o === "wrong_number" && $("f-newphone").value.trim()) {
      const fresh = $("f-newphone").value.trim(), old = current.phone;
      lock(true);
      const r = await jsonReq(`/api/leads/${lead}/`, "PATCH", { phone: fresh, phone_alt: old });
      if (r.error) { lock(false); fail(r.error); return; }
      const n = await jsonReq("/api/activities/", "POST", { lead, outcome: "note", note: `Zły numer ${old} – nowy numer: ${fresh}${note ? `. ${note}` : ""}` });
      lock(false);
      if (n.error) { fail(n.error); return; }
      toast(`Nowy numer zapisany: ${fresh}. Możesz od razu zadzwonić.`);
      await refreshAll(); await openLead(lead);
      setCallState(`Nowy numer: <b>${esc(fresh)}</b> – kliknij „Zadzwoń”.`);
      return;
    }
    return submit(body, goNext);
  }
  function lock(on) { saving = on; ["save-next", "save-only"].forEach((id) => { const b = $(id); if (b) b.disabled = on; }); }
  async function submit(body, goNext) {
    lock(true);
    const r = await jsonReq("/api/activities/", "POST", body);
    lock(false);
    if (r.error) { fail(r.error); return; }
    afterSave(r.data, goNext);
  }
  async function submitOffer(body, goNext) {
    lock(true);
    const r = await jsonReq("/api/offers/", "POST", body);
    lock(false);
    if (r.error) { fail(r.error); return; }
    const off = r.data.offer;
    const saved = { id: r.data.activity, outcome: "offer", rescheduled_to: body.next_contact_at };
    if (off.sent) { toast(`Oferta wysłana na ${off.recipient}. Follow-up zapisany.`); afterSave(saved, goNext); return; }
    // oferta do ręcznej wysyłki: pasek „Cofnij” i następny klient dopiero po zamknięciu okna z mailem
    pendingAfter = () => afterSave(saved, goNext);
    showOfferDialog(r.data);
  }
  let pendingAfter = null;
  $("offer-dialog").addEventListener("close", () => { const run = pendingAfter; pendingAfter = null; if (run) run(); });
  /* Oferta bez serwera poczty: agent kopiuje gotowy, sformatowany list (wkleja się w Gmailu / Outlooku
     z pogrubieniami jak we wzorniku) albo pobiera plik oferty i dodaje go jako załącznik. */
  async function offerHtml(id) {
    const r = await api(`/api/offers/${id}/html/`);
    if (!r.ok) throw new Error("Nie udało się pobrać oferty.");
    return { blob: await r.blob(), name: ((r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/) || [])[1] || "oferta.html" };
  }
  function showOfferDialog(d) {
    const dlg = $("offer-dialog"), o = d.offer;
    const who = config.mode === "direct" ? "do klienta" : `do osoby wysyłającej oferty${config.forward_name ? ` (${esc(config.forward_name)})` : ""}`;
    $("od-title").textContent = "Oferta zapisana – wyślij ją";
    $("od-text").innerHTML = o.recipient ? `CRM nie ma jeszcze skonfigurowanej poczty, więc wyślij ofertę na <b>${esc(o.recipient)}</b> sam:`
      : "Brak adresu do przekazywania ofert (ustawia go manager). Wyślij ofertę ręcznie:";
    // „Otwórz w poczcie” (mailto) przenosi tylko zwykły tekst i nie umie dodać załącznika – to ograniczenie
    // wszystkich programów pocztowych (Thunderbird, Outlook). Dlatego oferta z formatowaniem kopiuje się sama,
    // a agent wkleja ją w mailu.
    $("od-steps").innerHTML = `<li>Kliknij <b>„Otwórz w poczcie”</b> – otworzy się mail ${who} z tematem i danymi klienta, a oferta <b>sama się skopiuje</b>.</li>
      <li>W mailu kliknij <b>prawym przyciskiem myszy</b> pod danymi klienta i wybierz <b>„Wklej”</b> – oferta wklei się z formatowaniem.</li>
      <li>Jeśli potrzebny jest załącznik: <b>„Pobierz plik oferty”</b>, a w poczcie przycisk <b>„Załącz”</b> i wybierz plik z folderu Pobrane.</li>`;
    $("od-body").value = `${o.subject}\n\n${o.body}`;
    $("od-letter").innerHTML = '<p class="muted">Wczytuję podgląd…</p>';
    let file = null;
    offerHtml(o.id).then(async (f) => { file = f; const doc = new DOMParser().parseFromString(await f.blob.text(), "text/html"); $("od-letter").innerHTML = doc.body.innerHTML; })
      .catch(() => { $("od-letter").textContent = o.body; });
    $("od-mail").href = d.mailto; $("od-mail").hidden = !o.recipient;
    const flash = (btn, text, back) => { btn.textContent = text; setTimeout(() => { btn.textContent = back; }, 1800); };
    async function copyLetter() {
      // Białe tło jako tabela z bgcolor – tak zachowuje je każda poczta, także ciemny Thunderbird
      // (sam ciemny tekst bez tła byłby tam czarny na czarnym). Nowe oferty mają już tę tabelę z serwera.
      const inner = $("od-letter").innerHTML;
      const html = inner.includes("data-offer-card") ? inner
        : `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="background:#ffffff;border-collapse:collapse;max-width:760px"><tr><td bgcolor="#ffffff" style="background:#ffffff;color:#25212a;padding:20px 24px">${inner}</td></tr></table>`;
      const text = $("od-letter").innerText;
      try {
        await navigator.clipboard.write([new ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([text], { type: "text/plain" }) })]);
        return "html";
      } catch { return (await CRM.copyText(text)) ? "text" : ""; }
    }
    $("od-copy").onclick = async () => {
      const how = await copyLetter();
      flash($("od-copy"), how === "html" ? "Skopiowano ✓ – wklej w mailu" : how ? "Skopiowano (bez formatowania)" : "Nie udało się", "Kopiuj ofertę (z formatowaniem)");
    };
    $("od-mail").onclick = async (e) => {
      e.preventDefault();
      const how = await copyLetter();
      if (how !== "html") { location.href = d.mailto; return; }   // bez schowka: stary mail z ofertą zwykłym tekstem
      // krótki mail: dane klienta + miejsce na wklejenie sformatowanej oferty
      const head = String(o.body).split(/\n-{10,}\n/)[0].replace(/^Proszę o wysłanie oferty do klienta\.\s*/, "").trim();
      const body = `${head}\n\n(Tu wklej ofertę: prawy przycisk myszy → Wklej)\n`;
      location.href = `mailto:${encodeURIComponent(o.recipient)}?subject=${encodeURIComponent(o.subject)}&body=${encodeURIComponent(body)}`;
      flash($("od-mail"), "Oferta skopiowana ✓ – wklej w mailu", "Otwórz w poczcie");
    };
    $("od-file").onclick = async () => {
      try {
        const f = file || await offerHtml(o.id);
        const url = URL.createObjectURL(f.blob);
        const a = Object.assign(document.createElement("a"), { href: url, download: f.name });
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
        flash($("od-file"), "Pobrano ✓", "Pobierz plik oferty");
      } catch (ex) { toast(ex.message, true); }
    };
    dlg.showModal();
  }

  /* ---------- po zapisie: pasek „Cofnij” i następny klient ---------- */
  const UNDO_SECONDS = 10;
  let undoTimer = null;
  function hideUndo() { clearInterval(undoTimer); const el = $("undo-bar"); if (el) el.remove(); }
  function showUndo(saved, leadId, text) {
    hideUndo(); if (!saved || !saved.id) return;
    if (CRM.undoBarEnabled && !CRM.undoBarEnabled()) { toast(text.replace(/<[^>]+>/g, "")); return; }
    const bar = document.createElement("div");
    bar.id = "undo-bar"; bar.className = "undo-bar"; bar.setAttribute("role", "status"); bar.style.bottom = "calc(var(--dialbar-h) + 14px)";
    bar.innerHTML = `<span class="ub-text">${text}</span><button type="button" class="ub-btn">Cofnij <span class="ub-sec">${UNDO_SECONDS}</span></button><span class="ub-time" aria-hidden="true"></span>`;
    document.body.appendChild(bar);
    bar.querySelector(".ub-time").style.animationDuration = UNDO_SECONDS + "s";
    let left = UNDO_SECONDS;
    undoTimer = setInterval(() => { left -= 1; const s = bar.querySelector(".ub-sec"); if (s) s.textContent = String(Math.max(left, 0)); if (left <= 0) hideUndo(); }, 1000);
    bar.querySelector(".ub-btn").addEventListener("click", async () => {
      const r = await jsonReq(`/api/activities/${saved.id}/undo/`, "POST", {});
      hideUndo();
      if (r.error) { toast(r.error, true); return; }
      toast("Zapis cofnięty."); await refreshAll(); openLead(leadId);
    });
  }
  async function afterSave(saved, goNext) {
    const name = current ? title(current) : "", leadId = current ? current.id : null;
    const again = saved && saved.rescheduled_to ? ` · następny kontakt ${esc(when(saved.rescheduled_to))}` : "";
    showUndo(saved, leadId, `<b>Zapisano:</b> ${esc(OUT_LABEL[saved.outcome] || "")} · ${esc(name)}${again}`);
    if (CRM.refreshReminders) CRM.refreshReminders();
    await refreshAll();
    if (goNext) { await nextClient(); return; }
    if (leadId) openLead(leadId);
  }
  async function refreshAll() { await Promise.all([loadLeads(), loadSummary(), loadHistory(), loadSideData()]); }

  /* ---------- „Następny klient” ---------- */
  const REASON_TOAST = { offer: "Follow-up po ofercie", callback: "Umówione oddzwonienie", retry: "Ponowna próba", new: "Nowa firma" };
  async function nextClient() {
    const btns = document.querySelectorAll("#claim-btn, [data-next-btn]");
    btns.forEach((b) => { b.disabled = true; });
    try {
      const r = await api("/api/work/next/", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (r.status === 404) { welcome(d.detail || "Na teraz nie ma nic do zrobienia."); return; }
      if (!r.ok) { toast(d.detail || "Nie udało się pobrać klienta.", true); return; }
      if (!leads.some((l) => l.id === d.lead.id)) await loadLeads();
      await openLead(d.lead.id, d.reason);
      loadSummary();
      window.scrollTo({ top: 0 });
    } catch { toast("Brak połączenia z serwerem.", true); } finally { btns.forEach((b) => { b.disabled = false; }); }
  }

  /* ---------- wyszukiwarka z górnego paska ---------- */
  document.addEventListener("crm:search", async (e) => {
    e.preventDefault();
    const q = (e.detail && e.detail.q || "").trim();
    if (q.length < 2) return;
    const r = await api("/api/leads/lookup/?q=" + encodeURIComponent(q));
    if (!r.ok) { toast("Nie udało się wyszukać.", true); return; }
    const found = await r.json();
    if (!found.length) { toast(`Nie znaleziono klienta dla „${q}”.`, true); return; }
    await openLead(found[0].id);
    if (found.length > 1) toast(`Znaleziono ${found.length} klientów – otwieram najlepiej pasującego.`);
  });

  /* ---------- kliknięcia ---------- */
  document.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.closest("#claim-btn, [data-next-btn]")) { nextClient(); return; }
    const seg = t.closest("[data-seg]"); if (seg) { segFilter = seg.dataset.seg; tab = "todo"; paintTabs(); renderSegs(); renderList(); return; }
    const item = t.closest("#lead-list [data-id], #side [data-id]"); if (item) { openLead(Number(item.dataset.id)); return; }
    if (t.closest("#tab-todo")) { tab = "todo"; paintTabs(); renderList(); return; }
    if (t.closest("#tab-hist")) { tab = "hist"; paintTabs(); loadHistory(); renderHistory(); return; }
    if (t.closest("#call-btn")) { callNow(); return; }
    const out = t.closest("[data-out]"); if (out) { pick(out.dataset.out); return; }
    if (t.closest("#save-next")) { save(true); return; }
    if (t.closest("#save-only")) { save(false); return; }
    if (t.closest("#note-only")) { save(false, true); return; }
    if (t.closest("#reset-form")) { resetForm(); return; }
    if (t.closest("#edit-data, [data-edit]")) { editData(); return; }
    if (t.closest("[data-referral]")) { openContact(); return; }
    const reg = t.closest("[data-reg]");
    if (reg) {
      const ok = await CRM.copyText(reg.dataset.reg);
      window.open("https://www.biznes.gov.pl/pl/wyszukiwarka-firm/", "_blank", "noopener");
      toast(ok ? "NIP skopiowany. W wyszukiwarce kliknij pole prawym przyciskiem myszy i wybierz „Wklej”." : "Otworzyłem wyszukiwarkę firm – przepisz NIP.");
    }
  });
  const paintTabs = () => { $("tab-todo").setAttribute("aria-selected", String(tab === "todo")); $("tab-hist").setAttribute("aria-selected", String(tab === "hist")); };

  /* ---------- klawisze (tylko pojedyncze, bez kombinacji) ---------- */
  const keysOn = () => !CRM.keysEnabled || CRM.keysEnabled();
  const paintKeys = () => document.body.classList.toggle("no-keys", !keysOn());
  document.addEventListener("crm:view", paintKeys);
  paintKeys();
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || CRM.tourActive || !keysOn()) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (document.querySelector("dialog[open]")) return;
    if (e.key === "Escape") { resetForm(); return; }
    if (typing) return;
    const k = e.key.toLowerCase();
    if (k === "n") { e.preventDefault(); nextClient(); return; }
    if (k === "z" && current) { e.preventDefault(); callNow(); return; }
    if (e.key === "Enter" && form) { e.preventDefault(); save(true); return; }
    const o = OUTCOMES.find((x) => String(x.n) === e.key);
    if (o && current) { e.preventDefault(); pick(o.key); }
  });

  /* ---------- nowy kontakt (np. z polecenia) i dzwonienie na dowolny numer ---------- */
  function openContact(prefill = {}) {
    const dlg = $("contact-dialog");
    $("nc-phone").value = prefill.phone || ""; $("nc-company").value = ""; $("nc-person").value = ""; $("nc-email").value = ""; $("nc-note").value = "";
    $("nc-error").hidden = true;
    const ref = current && !tourDemo ? current : null;
    $("nc-ref-row").hidden = !ref;
    $("nc-ref").checked = !!ref && !prefill.phone;
    if (ref) $("nc-ref-text").textContent = `Polecił go: ${title(ref)}`;
    dlg.showModal();
    $("nc-phone").focus();
  }
  async function saveContact(andCall) {
    const body = { phone: $("nc-phone").value.trim(), company_name: $("nc-company").value.trim(), contact_name: $("nc-person").value.trim(),
      email: $("nc-email").value.trim(), note: $("nc-note").value.trim() };
    if (!body.phone) { $("nc-error").textContent = "Podaj numer telefonu."; $("nc-error").hidden = false; return; }
    if (current && !$("nc-ref-row").hidden && $("nc-ref").checked) body.referred_by = current.id;
    const r = await jsonReq("/api/work/contact/", "POST", body);
    if (r.status === 409) {
      $("nc-error").innerHTML = esc(r.data.detail || "Ten numer jest już w CRM.") + (r.data.lead ? ` <button type="button" class="w-link" id="nc-open">Otwórz tego klienta</button>` : "");
      $("nc-error").hidden = false;
      const b = $("nc-open"); if (b) b.addEventListener("click", async () => { $("contact-dialog").close(); await loadLeads(); openLead(r.data.lead); });
      return;
    }
    if (r.error) { $("nc-error").textContent = r.error; $("nc-error").hidden = false; return; }
    $("contact-dialog").close();
    toast("Nowy kontakt dodany do Twoich klientów.");
    await refreshAll();
    await openLead(r.data.lead.id);
    if (andCall) callNow();
  }
  $("contact-open").addEventListener("click", () => openContact());
  $("nc-cancel").addEventListener("click", () => $("contact-dialog").close());
  $("contact-form").addEventListener("submit", (e) => { e.preventDefault(); saveContact(false); });
  $("nc-call").addEventListener("click", () => saveContact(true));

  function openDial() {
    $("dl-phone").value = ""; $("dl-found").textContent = ""; $("dl-error").hidden = true;
    $("dial-dialog").showModal(); $("dl-phone").focus();
  }
  let dialLookup = null;
  $("dl-phone").addEventListener("input", () => {
    clearTimeout(dialLookup);
    const q = $("dl-phone").value.replace(/\D/g, "");
    if (q.length < 9) { $("dl-found").textContent = ""; return; }
    dialLookup = setTimeout(async () => {
      const r = await api("/api/leads/lookup/?q=" + encodeURIComponent(q)).catch(() => null);
      const found = r && r.ok ? await r.json() : [];
      const hit = found.find((x) => x.match === "exact") || null;
      $("dl-found").innerHTML = hit ? `Ten numer jest w CRM: <b>${esc(hit.display_name || hit.company_name || hit.contact_name || hit.phone)}</b> · <button type="button" class="w-link" data-open-found="${hit.id}">otwórz kartę</button>` : "Numeru nie ma w CRM – możesz zadzwonić albo dodać go jako klienta.";
    }, 350);
  });
  $("dl-found").addEventListener("click", (e) => { const b = e.target.closest("[data-open-found]"); if (b) { $("dial-dialog").close(); openLead(Number(b.dataset.openFound)); } });
  async function dialAny() {
    const phone = $("dl-phone").value.trim();
    if ((phone.replace(/\D/g, "")).length < 9) { $("dl-error").textContent = "Numer musi mieć co najmniej 9 cyfr."; $("dl-error").hidden = false; return; }
    $("dial-dialog").close();
    if (dialer.online) {
      const r = await jsonReq("/api/dialer/call/", "POST", { phone });
      if (r.error) { toast(r.error, true); return; }
      dialer = { ...dialer, ...r.data }; renderDialbar(); pollDialerSoon();
      toast(`Telefon wybiera numer ${phone}.`);
    } else {
      location.href = "tel:" + phone.replace(/[^\d+]/g, "");
    }
  }
  $("dial-open").addEventListener("click", openDial);
  $("db-dial").addEventListener("click", openDial);
  $("dl-cancel").addEventListener("click", () => $("dial-dialog").close());
  $("dial-form").addEventListener("submit", (e) => { e.preventDefault(); dialAny(); });
  $("dl-add").addEventListener("click", () => { const phone = $("dl-phone").value.trim(); $("dial-dialog").close(); openContact({ phone }); });

  /* ---------- samouczek: raz dla każdego agenta, potem z „Moje ustawienia” ---------- */
  const DEMO_LEAD = { id: 0, phone: "500 100 200", phone_alt: "", company_name: "Przykładowa Firma Jan Nowak", display_name: "Przykładowa Firma Jan Nowak",
    contact_name: "Jan Nowak", email: "", company_nip: "0000000000", company_regon: "", company_krs: "", company_street: "ul. Przykładowa 1",
    company_postal_code: "00-001", company_city: "Olsztyn", status: "new", activities: [], attempts_count: 0, operators: "", client_comment: "",
    next_contact_at: null, next_contact_note: "", knows_caller: false, missed_calls: 0 };
  /* Haki dla samouczka (tour.js): przykładowy klient zamiast prawdziwego, podgląd wyników bez zapisu */
  let tourBefore = null;
  CRM.tourQueue = {
    prepare() {
      if (tourDemo) return;
      tourBefore = current ? current.id : null;
      tourDemo = true; current = { ...DEMO_LEAD }; renderCard();
    },
    demo(o) {
      if (!tourDemo || !$("out-panel")) return;
      if (o && (!form || form.outcome !== o)) pick(o);
      if (!o && form) resetForm();
    },
    cleanup() {
      if (!tourDemo) return;
      tourDemo = false; current = null;
      if (tourBefore) openLead(tourBefore); else welcome();
    },
  };
  const startTour = () => CRM.tour && CRM.tour.start();
  CRM.startTour = startTour;

  /* ---------- start ---------- */
  setInterval(() => { if (!document.hidden && tab === "todo") renderList(); }, 30000);
  setInterval(() => { if (!document.hidden) { loadSummary(); loadDialer(); } }, 20000);
  setInterval(() => { if (!document.hidden) loadSideData(); }, 120000);
  (async () => {
    const [pr, cf] = await Promise.all([api("/api/products/?is_active=true").catch(() => null), api("/api/offer-config/").catch(() => null)]);
    try { if (pr && pr.ok) { const d = await pr.json(); plans = (d.results || d).filter((p) => p.is_active); } } catch { /* bez taryf */ }
    try { if (cf && cf.ok) config = { ...config, ...(await cf.json()) }; } catch { /* domyślne ustawienia */ }
    await Promise.all([loadLeads(), loadSummary(), loadDialer(), loadSideData(), loadHistory()]);
    const params = new URLSearchParams(location.search);
    const wanted = Number(params.get("lead")), find = params.get("find");
    if (find) { const input = $("gsearch-input"); if (input) input.value = find; document.dispatchEvent(new CustomEvent("crm:search", { detail: { q: find }, cancelable: true })); return; }
    if (wanted && await openLead(wanted)) return;
    if (CRM.tour) {
      if (params.get("tour") === "1") { window.history.replaceState(null, "", location.pathname); welcome(); setTimeout(startTour, 300); return; }
      if (CRM.tour.resume()) return;
      if (CRM.tour.shouldAutoStart()) { welcome(); setTimeout(startTour, 300); return; }
    }
    const first = leads.slice().sort(cmp)[0];
    if (first && isDue(first) && segOf(first) !== "new") openLead(first.id); else welcome();
  })().catch(() => { $("lead-pane").innerHTML = `<div class="w-panel"><p class="w-error">Nie udało się wczytać danych. Odśwież stronę (F5).</p></div>`; });
})();

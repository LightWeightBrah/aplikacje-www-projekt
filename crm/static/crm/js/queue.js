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
  /* Powody odmowy i wzory komentarzy: listy ustawia manager („Ustawienia i oferty”), tu tylko zapas na start */
  let REFUSE_CHIPS = ["Ma umowę w innej sieci", "Za drogo", "Nie jest zainteresowany", "Nie decyduje o tym", "Prosi o brak kontaktu"];
  // „Pomiń firmę”: najpierw powody „nie dzwonić więcej”, potem „zamknięty” (listy też ustawia manager)
  let SKIP_CHIPS = ["Firma nie istnieje / zawieszona", "Brak numeru / numer stacjonarny", "Duplikat", "Już jest w T-Mobile", "Inny powód"];
  let reqSkip = false, reqRefuse = false;   // czy powód pominięcia / odmowy jest wymagany (domyślnie nie)
  /* Wyniki, po których była rozmowa: tylko przy nich pytamy o sieć klienta */
  const TALKED = ["callback", "interested", "offer", "sold", "refused"];
  /* Wzory komentarzy pasujące do wybranego wyniku (klik dopisuje do komentarza) */
  let PHRASES = {
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
    let url = "/api/my-leads/?page_size=500", all = [];
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
  const showPart = (part) => !CRM.showPart || CRM.showPart(part);   // „Moje ustawienia”: drobne napisy można schować
  function renderBase() {
    const b = summary && summary.base, box = $("base-progress");
    if (!b || !showPart("base")) { box.hidden = true; return; }
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
    const minutesBlock = !showPart("goal") ? "" : t.dialer_used || t.minutes_today
      ? `<div class="w-goal"><div class="g-top"><span>Ta godzina</span><b>${CRM.talk(t.minutes_this_hour)} / ${goal} min</b></div>
          <div class="g-track"><i class="${t.minutes_this_hour >= goal ? "ok" : ""}" style="width:${pctHour}%"></i></div>
          <div class="w-hours" aria-label="Minuty rozmów w godzinach">${hours.map((h) => `<div class="${h.hour === hourNow ? "now" : ""}" title="${h.hour}:00 – ${CRM.talk(h.minutes)}${h.hour === hourNow ? " (teraz)" : ""}"><i class="${h.minutes === 0 ? "zero" : h.minutes < goal ? "low" : ""}" style="height:${Math.max(4, Math.min(100, (h.minutes / goal) * 100))}%"></i><small>${h.hour}</small></div>`).join("")}</div>
          <p class="w-goal-note">Dziś: <b class="${t.day_goal && t.minutes_today >= t.day_goal ? "ok" : ""}">${CRM.talk(t.minutes_today)}</b> rozmów, powinno być <b>${CRM.talk(t.day_goal)}</b>${workdayText()}. Cel: ${goal} min na każdą godzinę pracy.</p></div>`
      : `<p class="w-goal-note">Dziś powinno być <b>${CRM.talk(t.day_goal)}</b> rozmów${workdayText()} – cel ${goal} min na każdą godzinę pracy. Minuty policzą się same, gdy połączysz telefon z aplikacją Solid Dialer.</p>`;
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
    // długie wyjścia (od 45 min) nie liczą się do pracy – krótkie przerwy tak
    const short = (w.break_minutes || 0) - (w.away_minutes || 0);
    const br = (short > 0 ? `, w tym przerwy ${CRM.talk(short)}` : "") + (w.away_minutes ? `; poza pracą ${CRM.talk(w.away_minutes)} – nie liczy się` : "");
    return ` (${w.ended_at ? "praca" : "pracujesz"} od ${hhmm(w.started_at)}${w.ended_at ? ` do ${hhmm(w.ended_at)}` : ""}: ${h ? `${h} h ` : ""}${r} min${br})`;
  }
  document.addEventListener("crm:view", () => { renderSide(); renderBase(); renderDialbar(); });

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
    // „Moje ustawienia”: kropkę i napis o stanie telefonu można schować (połączony, bez telefonu, nie odpowiada);
    // „Dzwonię…” / „Czekam na telefon” zostają, a przycisk „Połącz telefon” jest zawsze
    const quiet = !(call && (call.state === "dialing" || call.state === "pending")) && !showPart("phone");
    $("db-state").hidden = quiet; $("db-dot").hidden = quiet;
    $("db-setup").hidden = dialer.online;
    $("db-setup").textContent = dialer.paired ? "Telefon" : "Połącz telefon";
    const dialing = call && (call.state === "dialing" || call.state === "pending");
    $("db-timer").hidden = !dialing;
    if (dialing && !callTimer) { callStart = Date.now(); callTimer = setInterval(tick, 1000); tick(); }
    if (!dialing && callTimer) { clearInterval(callTimer); callTimer = null; }
    const t = summary && summary.today;
    $("db-min").textContent = t && showPart("footer") ? `Dziś: ${t.calls} tel. · ${CRM.talk(t.minutes_today)} rozmów · ta godzina ${CRM.talk(t.minutes_this_hour)} / ${t.goal_minutes_per_hour} min` : "";
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
  /* Samo dzwonienie po „Zapisz i dalej”: odliczanie nad numerem (ile sekund – „Moje ustawienia”, domyślnie 4),
     „Wstrzymaj” albo Esc przerywa. Tylko z telefonem połączonym z CRM (bez niego „tel:” otwierałby okno systemu bez pytania). */
  const autoDialSeconds = () => (CRM.autoDialSeconds ? CRM.autoDialSeconds() : 4);
  let autoDial = null;
  function stopAutoDial(text) {
    if (!autoDial) return;
    clearInterval(autoDial.timer); autoDial = null;
    const box = document.querySelector(".w-autodial"); if (box) box.remove();
    if (text) setCallState(text);
  }
  function armAutoDial() {
    stopAutoDial();
    if (!current || !dialer.online || tourDemo || !(CRM.autoDialEnabled && CRM.autoDialEnabled())) return;
    if (dialer.call && ["pending", "dialing"].includes(dialer.call.state)) return;
    const lead = current.id, wrap = document.querySelector(".w-call");
    if (!wrap) return;
    const total = autoDialSeconds();
    let left = total;
    const box = document.createElement("div");
    box.className = "w-autodial"; box.setAttribute("role", "status");
    box.innerHTML = `<span>Dzwonię za <b data-left>${left}</b> s…</span><button type="button" class="btn btn-secondary" data-autodial-stop>Wstrzymaj <kbd class="w-kbd">Esc</kbd></button><i style="animation-duration:${total}s"></i>`;
    wrap.after(box);
    box.querySelector("[data-autodial-stop]").addEventListener("click", () => stopAutoDial("Wstrzymane. Zadzwoń, kiedy będziesz gotowy."));
    autoDial = { lead, timer: setInterval(() => {
      if (!current || current.id !== lead || form) { stopAutoDial(); return; }
      left -= 1;
      const el = box.querySelector("[data-left]"); if (el) el.textContent = String(Math.max(left, 0));
      if (left <= 0) { stopAutoDial(); callNow(); }
    }, 1000) };
  }
  CRM.stopAutoDial = stopAutoDial;
  async function callNow() {
    stopAutoDial();
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
    current = null; form = null;
    const later = leads.filter((l) => !isDue(l)).sort(cmp)[0];
    const pane = $("lead-pane");
    const html = empty
      ? `<div class="w-panel w-welcome done"><h2>Na teraz wszystko zrobione ✓</h2>
          <p>${esc(empty)}</p>
          ${later ? `<p>Najbliższy kontakt: <b>${esc(title(later))}</b> · ${esc(when(later.next_contact_at))}. Wróci tu sam, gdy przyjdzie jego pora.</p>` : ""}
          <p class="muted">Możesz dodać klienta z polecenia („+ Nowy kontakt” po lewej) albo zadzwonić na dowolny numer z paska na dole.</p>
          <button type="button" class="w-next" data-next-btn>▶ Sprawdź jeszcze raz <kbd>N</kbd></button>
          <p class="muted small w-checked" data-checked></p></div>`
      : `<div class="w-panel w-welcome"><h2>Gotowy do dzwonienia?</h2>
      <p>„Następny klient” sam wybierze, do kogo zadzwonić: najpierw follow-upy po ofertach, potem umówione oddzwonienia, ponowne próby, a na końcu nowe firmy z Twojej bazy.</p>
      <button type="button" class="w-next" data-next-btn>▶ Następny klient <kbd>N</kbd></button></div>`;
    // Ten sam komunikat co przed chwilą („Sprawdź jeszcze raz” i dalej nic): karty nie rysujemy od nowa
    // (to mrugało), tylko dopisujemy, kiedy sprawdzono
    if (empty && pane.dataset.welcome === html && pane.querySelector(".w-welcome.done")) {
      const at = new Date(), line = pane.querySelector("[data-checked]");
      if (line) line.textContent = `Sprawdzone o ${at.getHours()}:${String(at.getMinutes()).padStart(2, "0")}:${String(at.getSeconds()).padStart(2, "0")} – nadal nic nowego.`;
      return;
    }
    renderList();
    pane.innerHTML = html;
    pane.dataset.welcome = html;
  }
  /* Nowy klient = karta od początku: środkowa kolumna ma własny pasek przewijania (window.scrollTo go nie rusza) */
  function toTop() { $("lead-pane").scrollTop = 0; CRM.scrollTop(); }
  async function openLead(id, reason, data) {
    let lead = data;   // „Następny klient” od razu oddaje pełną kartę – bez drugiego zapytania
    if (!lead) {
      const r = await api(`/api/leads/${id}/`);
      if (!r.ok) { toast("Nie udało się wczytać klienta.", true); return false; }
      lead = await r.json();
    }
    current = lead; current._reason = reason || null; form = null;
    renderCard(); renderList();
    toTop();
    const item = document.querySelector(`#lead-list [data-id="${id}"]`); if (item) item.scrollIntoView({ block: "nearest" });
    return true;
  }
  CRM.openLead = async (id) => { if (!leads.some((l) => l.id === id)) await loadLeads(); const ok = await openLead(id); CRM.scrollTop(); return ok; };

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
  /* „Ostatnia zmiana: Anna Nowak, dziś 14:20” – widać, kto z zespołu dopisał stałe informacje */
  function clientByHtml(d) {
    if (!d.client_comment_at || !(d.client_comment || "").trim()) return "";
    const who = d.client_comment_by === me.id ? "Ty" : esc(d.client_comment_by_name || "ktoś z zespołu");
    return `Ostatnia zmiana: <b>${who}</b>, ${esc(when(d.client_comment_at))}`;
  }
  function renderCard() {
    const d = current;
    const tel = d.phone.replace(/[^\d+]/g, "");
    const st = CRM.STATUS[d.status] || [d.status, "#64748b"];
    const badges = [`<span class="w-badge" style="--c:${st[1]}">${esc(st[0])}</span>`];
    if (isCompanyEntity(d.company_name)) badges.push(`<span class="w-badge spolka" title="Spółka: zwykle trzeba trafić do osoby decyzyjnej (zarząd, właściciel)">Spółka</span>`);
    if (d.knows_caller) badges.push(`<span class="w-badge" style="--c:var(--ok)">Wie, skąd dzwonimy</span>`);
    if (d.operators) badges.push(`<span class="w-badge">Sieć: ${esc(d.operators.split(",").map((c) => (CRM.OPERATORS.find((o) => o[0] === c) || [c, c])[1]).join(", "))}</span>`);
    const ct = contractBadge(d); if (ct) badges.push(ct);
    if (d.transferred_from) badges.push(`<span class="w-badge moved">↪ Przepisany od ${esc(d.transferred_from)}</span>`);
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
        <div class="w-info" id="client-panel">
          <div class="w-info-head"><label for="cl-comment">Stałe informacje o kliencie</label><small class="w-info-by" id="cl-by">${clientByHtml(d)}</small><small class="w-status" id="cl-status"></small></div>
          <textarea id="cl-comment" rows="2" maxlength="2000" placeholder="np. 3 numery w Play, umowa do marca, decyduje właściciel – widzą to wszyscy agenci">${esc(d.client_comment || "")}</textarea>
        </div>
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
      if (a.outcome === "skipped") extra += `<div class="h-extra">Powód: ${esc(SKIP_LABEL[a.skip_reason] || a.skip_reason || "bez powodu")}</div>`;
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
    const flush = async () => {
      clearTimeout(timer); if (ta.value.trim() === last.trim()) return; last = ta.value;
      if (await save({ client_comment: ta.value.trim() })) {
        Object.assign(d, { client_comment_by: me.id, client_comment_at: new Date().toISOString() });
        const by = $("cl-by"); if (by) by.innerHTML = clientByHtml(d);
      }
    };
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
  /* ---------- koniec umowy u obecnego operatora: miesiąc i rok (zapis od razu) ----------
     Numer można przenieść najwcześniej 12 miesięcy przed końcem umowy (models.porting_from).
     Przy „Odmowie” z późniejszą datą serwer sam umawia telefon na ten dzień (services.contract_callback). */
  const MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień"];
  const MONTHS_GEN = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"];
  function portingFrom(iso) {
    if (!iso) return null;
    const [y, m] = iso.split("-").map(Number), idx = y * 12 + (m - 1) - 11;
    return new Date(Math.floor(idx / 12), idx % 12, 1);
  }
  const monthYear = (iso) => { const [y, m] = iso.split("-"); return `${m}.${y}`; };
  function contractBadge(d) {
    if (d.contract_open_ended) return `<span class="w-badge" style="--c:var(--ok)">Umowa bez terminu – można przenosić</span>`;
    if (!d.contract_end) return "";
    const from = portingFrom(d.contract_end), now = from <= new Date();
    return `<span class="w-badge"${now ? ' style="--c:var(--ok)"' : ""}>Umowa do ${esc(monthYear(d.contract_end))}${now ? " – można przenosić" : ` · przenosić od ${String(from.getMonth() + 1).padStart(2, "0")}.${from.getFullYear()}`}</span>`;
  }
  function contractInfo(d) {
    const sel = { m: $("ct-month") ? $("ct-month").value : "", y: $("ct-year") ? $("ct-year").value : "" };
    if (d.contract_open_ended) return "Umowa bez terminu: numery można przenieść od razu.";
    if (!d.contract_end) return sel.m && !sel.y ? "Wybierz jeszcze rok." : sel.y && !sel.m ? "Wybierz jeszcze miesiąc." : "Klient mówi „mamy umowę”? Zapytaj: „A do kiedy?”. CRM sam przypomni, kiedy będzie można przenieść numery.";
    const from = portingFrom(d.contract_end);
    if (from <= new Date()) return "Do końca umowy zostało mniej niż 12 miesięcy: <b>numery można przenieść już teraz</b>.";
    const date = `${from.getDate()} ${MONTHS_GEN[from.getMonth()]} ${from.getFullYear()}`;
    return form && form.outcome === "refused"
      ? `Po zapisie klient <b>się nie zamknie</b>: CRM sam umówi telefon na <b>${date}</b> (od wtedy można przenieść numery).`
      : `Numery można przenieść od <b>${date}</b>. Przy wyniku „Odmowa” CRM sam umówi wtedy telefon.`;
  }
  function contractHtml(d) {
    const [cy, cm] = d.contract_end ? d.contract_end.split("-").map(Number) : [0, 0];
    const year = new Date().getFullYear(), years = [];
    for (let y = year; y <= year + 4; y++) years.push(y);
    if (cy && !years.includes(cy)) years.unshift(cy);
    return `<div class="w-field w-contract"><span>Do kiedy klient ma umowę? <small class="muted">(zapisuje się od razu)</small></span>
      <div class="w-ct-row">
        <select id="ct-month" class="select" aria-label="Miesiąc końca umowy"><option value="">miesiąc…</option>${MONTHS.map((n, i) => `<option value="${i + 1}"${cm === i + 1 ? " selected" : ""}>${n}</option>`).join("")}</select>
        <select id="ct-year" class="select" aria-label="Rok końca umowy"><option value="">rok…</option>${years.map((y) => `<option value="${y}"${cy === y ? " selected" : ""}>${y}</option>`).join("")}</select>
        <button type="button" class="w-chip" id="ct-open" aria-pressed="${!!d.contract_open_ended}">Bez terminu</button>
        <button type="button" class="w-link" id="ct-clear"${d.contract_end || d.contract_open_ended ? "" : " hidden"}>wyczyść</button>
      </div>
      <p class="w-info" id="ct-info">${contractInfo(d)}</p></div>`;
  }
  let contractSaving = null;   // zapis daty trwa: „Zapisz” poczeka, żeby serwer znał już koniec umowy
  function wireContract() {
    const m = $("ct-month"), y = $("ct-year"); if (!m) return;
    const paint = () => {
      $("ct-info").innerHTML = contractInfo(current);
      $("ct-open").setAttribute("aria-pressed", String(!!current.contract_open_ended));
      $("ct-clear").hidden = !(current.contract_end || current.contract_open_ended);
    };
    const store = async (body) => {
      const prev = { contract_end: current.contract_end, contract_open_ended: current.contract_open_ended };
      contractSaving = CRM.saveLeadPatch ? CRM.saveLeadPatch(body) : Promise.resolve(false);
      const ok = await contractSaving; contractSaving = null;
      if (!ok) { Object.assign(current, prev); toast("Nie udało się zapisać końca umowy.", true); }
      paint();
    };
    const onPick = () => {
      if (!m.value || !y.value) { paint(); return; }
      store({ contract_end: `${y.value}-${String(m.value).padStart(2, "0")}-01`, contract_open_ended: false });
    };
    [m, y].forEach((el) => { el.addEventListener("change", onPick); el.addEventListener("keydown", (e) => e.stopPropagation()); });
    $("ct-open").addEventListener("click", () => {
      const on = !current.contract_open_ended;
      if (on) { m.value = ""; y.value = ""; }
      store({ contract_open_ended: on, contract_end: null });
    });
    $("ct-clear").addEventListener("click", () => { m.value = ""; y.value = ""; store({ contract_open_ended: false, contract_end: null }); });
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
  /* Dni pracy z ustawień managera (1 = pon … 7 = niedz.); domyślnie pon–pt. Dzień wolny w podpowiedziach jest pomijany. */
  let workDays = new Set([1, 2, 3, 4, 5]);
  const isWorkDay = (d) => workDays.has(d.getDay() || 7);
  const toWorkday = (d) => { for (let i = 0; i < 7 && !isWorkDay(d); i++) d.setDate(d.getDate() + 1); return d; };
  const nextWorkday = (h, m = 0) => toWorkday(atHour(1, h, m));
  const inWorkdays = (days, h, m = 0) => toWorkday(atHour(days, h, m));   // „Za 2 dni” w piątek -> poniedziałek
  const DAY_SHORT = ["Niedz.", "Pon.", "Wt.", "Śr.", "Czw.", "Pt.", "Sob."];
  /* „Jutro 10:00”, a przed dniem wolnym np. „Pon. 10:00” – żeby było widać, że dzień wolny jest pomijany */
  const wdLabel = (h, text) => () => { const d = nextWorkday(h), t = new Date(Date.now() + 864e5); return `${d.toDateString() === t.toDateString() ? "Jutro" : DAY_SHORT[d.getDay()]} ${text}`; };
  const PRESETS = {
    callback: [["Za 1 h", () => new Date(Date.now() + 3600e3)], ["Za 2 h", () => new Date(Date.now() + 7200e3)], ["Dziś 15:00", () => atHour(0, 15)], [wdLabel(9, "9:00"), () => nextWorkday(9)], [wdLabel(13, "13:00"), () => nextWorkday(13)], ["Za tydzień", () => inWorkdays(7, 10)]],
    interested: [["Za 1 h", () => new Date(Date.now() + 3600e3)], ["Dziś 15:00", () => atHour(0, 15)], [wdLabel(10, "10:00"), () => nextWorkday(10)], ["Za 2 dni", () => inWorkdays(2, 10)], ["Za tydzień", () => inWorkdays(7, 10)]],
    offer: [[wdLabel(10, "10:00"), () => nextWorkday(10)], [wdLabel(15, "15:00"), () => nextWorkday(15)], ["Za 2 dni", () => inWorkdays(2, 10)], ["Za tydzień", () => inWorkdays(7, 10)]],
    no_answer: [["Za 2 h", () => new Date(Date.now() + 7200e3)], [wdLabel(9, "rano"), () => nextWorkday(9)], [wdLabel(15, "po 15"), () => nextWorkday(15, 30)]],
  };
  function whenField(kind, label, value, required) {
    // jedno pole: wpisany termin + przycisk kalendarza doklejony z prawej (otwiera zwykły wybór daty)
    return `<div class="w-row"><div class="w-field w-when"><label for="f-when">${label}${required ? "" : " (opcjonalnie)"}</label>
        <div class="w-when-box"><input id="f-when" autocomplete="off" placeholder="np. jutro 10:30 · pt 9 · za 2h" value="${esc(value ? fmtInput(value) : "")}">
        <span class="w-cal"><button type="button" class="w-cal-btn" id="f-cal" title="Wybierz z kalendarza"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="4" width="15" height="13.5" rx="2"/><path d="M2.5 8.5h15M6.5 2v4M13.5 2v4"/></svg>Kalendarz</button>
        <input type="datetime-local" id="f-native" tabindex="-1" aria-hidden="true"></span></div></div></div>
      <div class="w-chips">${PRESETS[kind].map(([l], i) => `<button type="button" class="w-chip" data-preset="${kind}:${i}">${typeof l === "function" ? l() : l}</button>`).join("")}</div>
      <div class="w-when-preview" id="f-preview"></div>`;
  }
  /* Taryfy w trzech grupach (Komórkowe / Światłowód / Telewizja). Światłowód ma dwie ceny:
     „… – blok” (MFH) i „… – dom” (SFH) – agent wybiera rodzaj budynku przełącznikiem, a nie osobny kafelek.
     Zestaw: kilka usług naraz (np. numery + światłowód + TV) – form.basket to lista pozycji,
     form.plan to pozycja, którą agent właśnie ustawia. */
  const KINDS = [["mobile", "Komórkowe"], ["fiber", "Światłowód"], ["tv", "Telewizja"]];
  const INFO_ICON = '<svg class="w-info-ico" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9"/><path d="M10 9v5.5M10 5.6v.2"/></svg>';
  const HOME_RE = /\s*[–-]\s*(blok|dom)\s*$/i;
  const kindOf = (p) => /światłowód|swiatlowod|internet/i.test(p.name) ? "fiber" : /\btv\b/i.test(p.name) ? "tv" : "mobile";
  const homeOf = (p) => { const m = p.name.match(HOME_RE); return m ? m[1].toLowerCase() : null; };
  const baseOf = (p) => p.name.replace(HOME_RE, "");
  // na kafelku krótko jak przy komórkowych: „M”, „L”, „VIP” (pełna nazwa idzie do oferty)
  const chipName = (p) => baseOf(p).replace(/^(magenta\s+)?(światłowód|swiatlowod)(\s+biznes)?\s+|^magenta\s+tv\s+/i, "").trim() || baseOf(p);
  const planOf = (sel) => plans.find((x) => x.id === sel.product);
  /* Promocje taryfy: najpierw główna (np. światłowód „12 miesięcy za 50 zł”, pusta nazwa = „6 miesięcy gratis”
     z „Ustawienia i oferty”), potem inne do wyboru (np. „6 miesięcy za 0 zł”). Każda: miesiące, cena w nich, cena potem. */
  function promosOf(p) {
    if (!p) return [];
    const out = [];
    if (p.promo_price != null) {
      const label = p.promo_label || config.promo_label || "";
      out.push({ label, months: p.promo_months != null ? Number(p.promo_months) : parseInt(label, 10) || 0,
        price: Number(p.promo_month_price || 0), after: Number(p.promo_price) });
    }
    (Array.isArray(p.promos) ? p.promos : []).forEach((x) => out.push({ label: x.label, months: Number(x.months) || 0, price: Number(x.month_price) || 0, after: Number(x.price) }));
    return out;
  }
  const hasPromo = (p) => promosOf(p).length > 0;
  const promoOf = (p, option) => { const l = promosOf(p); return l[option] || l[0] || { label: "", months: 0, price: 0, after: p ? Number(p.price) : 0 }; };
  const linePromo = (sel) => !!sel.promo && hasPromo(planOf(sel));
  const lineOption = (sel) => { const l = promosOf(planOf(sel)); return linePromo(sel) && sel.option < l.length ? sel.option || 0 : 0; };
  const chipPrice = (sel, p) => sel.promo && hasPromo(p) ? promoOf(p, sel.option).after : Number(p.price);
  // światłowód i np. JUMP są wg materiałów na 24 miesiące: CRM sam ustawia 24, 36 da się kliknąć, ale widać podpowiedź
  const only24 = (p) => !!p && (kindOf(p) === "fiber" || /umowa na 24 miesiące/i.test(p.features || ""));
  const speedOf = (p) => { const m = (p.features || "").match(/(\d+)\s*Mb\/s/i); return m ? m[1] + " Mb/s" : ""; };
  /* Kafelki wybranej grupy: przy światłowodzie jeden kafelek na wariant (M/L/VIP) w wybranym rodzaju budynku */
  function plansOf(kind, home) {
    const list = plans.filter((p) => kindOf(p) === kind), seen = new Map();
    list.forEach((p) => {
      const key = baseOf(p), h = homeOf(p), had = seen.get(key);
      if (!had || (h === home && homeOf(had) !== home)) seen.set(key, p);
    });
    return [...seen.values()];
  }
  function planChips(sel) {
    // światłowód: na kafelku też prędkość (ta sama w bloku i w domu – różni się tylko cena)
    return plansOf(sel.kind, sel.home).map((p) =>
      `<button type="button" class="w-chip plan" data-plan="${p.id}" aria-pressed="${p.id === sel.product}" title="${esc(p.name)}"><b>${esc(chipName(p))}</b>${sel.kind === "fiber" && speedOf(p) ? `<em>${esc(speedOf(p))}</em>` : ""}<small>${money(chipPrice(sel, p))}</small></button>`).join("");
  }
  /* Promocja: przyciski „Bez promocji” + każda promocja taryfy (światłowód ma kilka do wyboru) */
  function promoButtons(sel) {
    const list = promosOf(planOf(sel));
    if (!list.length) return `<span class="w-promo-none">Ta taryfa nie ma promocji</span>`;
    const on = linePromo(sel), opt = lineOption(sel);
    return `<button type="button" data-promo="-1" aria-pressed="${!on}">Bez promocji</button>`
      + list.map((x, i) => `<button type="button" data-promo="${i}" aria-pressed="${on && opt === i}">${esc(x.label || "Promocja")}</button>`).join("");
  }
  function syncKind(sel) {   // grupa i rodzaj budynku wynikają z wybranej taryfy
    const p = planOf(sel);
    if (p) { sel.kind = kindOf(p); if (homeOf(p)) sel.home = homeOf(p); }
    sel.kind = sel.kind || "mobile"; sel.home = sel.home || "blok";
  }
  function pickerInner(sel) {
    syncKind(sel);
    const kinds = KINDS.filter(([k]) => plans.some((p) => kindOf(p) === k));
    const homes = plans.some((p) => kindOf(p) === "fiber" && homeOf(p));
    const head = (kinds.length > 1 ? `<div class="w-toggle" id="f-kind">${kinds.map(([k, l]) => `<button type="button" data-kind="${k}" aria-pressed="${sel.kind === k}">${l}</button>`).join("")}</div>` : "")
      + (homes ? `<div class="w-home" id="f-home" ${sel.kind === "fiber" ? "" : "hidden"}><span>Gdzie światłowód?</span><div class="w-toggle"><button type="button" data-home="blok" aria-pressed="${sel.home === "blok"}">Blok / mieszkanie</button><button type="button" data-home="dom" aria-pressed="${sel.home === "dom"}">Dom jednorodzinny</button></div></div>` : "");
    return `${head ? `<div class="w-plan-head">${head}</div>` : ""}
      <div class="w-field"><span>Taryfa</span><div class="w-chips" id="f-plans">${planChips(sel)}</div></div>
      <p class="w-fiber-note" id="f-fiber-note" ${sel.kind === "fiber" ? "" : "hidden"}>Prędkość jest taka sama w bloku i w domu – różni się tylko cena (dom +10 zł).</p>
      <button type="button" class="w-pinfo-btn" id="f-pinfo" title="Otwiera okno z pełną informacją o tej taryfie (także lista kanałów TV z wyszukiwarką)">${INFO_ICON}<span>Co zawiera <b id="f-pinfo-name"></b> i informacje dla agenta</span><span class="w-pinfo-go">Pokaż ›</span></button>
      <div class="w-field" id="f-promo-row"><span>Promocja</span><div class="w-toggle w-promos" id="f-promos">${promoButtons(sel)}</div></div>
      <div class="w-row">
        <div class="w-field"><span id="f-qty-label">Liczba numerów</span><div class="w-qty"><button type="button" data-qty="-1" aria-label="Mniej">−</button><input id="f-qty" type="number" min="1" max="500" value="${sel.quantity}" inputmode="numeric"><button type="button" data-qty="1" aria-label="Więcej">+</button></div></div>
        <div class="w-field"><span>Okres umowy</span><div class="w-toggle" id="f-term"><button type="button" data-term="36" aria-pressed="${sel.term === 36}">36 mies.</button><button type="button" data-term="24" aria-pressed="${sel.term === 24}">24 mies.</button></div><small class="w-term-hint" id="f-term-hint" hidden>W materiałach: umowa na 24 miesiące</small></div>
      </div>`;
  }
  function planPicker() {
    if (!plans.length) return `<p class="w-info">Brak taryf w katalogu. Manager dodaje je w „Zarządzanie → Taryfy i produkty”.</p>`;
    return `<div class="w-basket" id="f-basket"></div>
      <div id="f-picker">${pickerInner(form.plan)}</div>
      <div class="w-sum" id="f-sum"></div>`;
  }
  function calcPlan(sel) {
    const p = planOf(sel);
    if (!p) return null;
    const promo = linePromo(sel), pr = promoOf(p, lineOption(sel));
    const first = promo ? pr.after : Number(p.price);
    const extra = Number(p.extra_price || 0), q = Math.max(1, sel.quantity || 1);
    const monthly = extra ? first + (q - 1) * extra : first * q;
    const mult = Number(sel.term === 24 ? config.commission_24 : config.commission_36) || 0;
    const perNumber = extra > 0;   // komórkowe liczą numery; światłowód i TV – sztuki
    // jak we wzorniku: przy promocji średni koszt w całym okresie umowy (np. 6 miesięcy po 0 zł, 12 po 50 zł)
    const months = promo && pr.months > 0 && pr.months < sel.term ? pr.months : 0;
    const start = months ? pr.price * q : null;   // opłata w miesiącach promocji
    const average = months ? (start * months + monthly * (sel.term - months)) / sel.term : null;
    return { p, monthly, commission: monthly * mult, perNumber, average, months, start, promo, label: pr.label };
  }
  function basketTotals() {
    const cs = form.basket.map(calcPlan).filter(Boolean);
    const monthly = cs.reduce((s, c) => s + c.monthly, 0), commission = cs.reduce((s, c) => s + c.commission, 0);
    const average = cs.some((c) => c.average != null) ? cs.reduce((s, c) => s + (c.average != null ? c.average : c.monthly), 0) : null;
    return { cs, monthly, commission, average };
  }
  const lineLabel = (sel) => { const p = planOf(sel); return p ? `${p.name}${sel.quantity > 1 ? " ×" + sel.quantity : ""}` : "?"; };
  function paintBasket() {
    const el = $("f-basket"); if (!el) return;
    const used = new Set(form.basket.map((l) => l.kind));
    const next = KINDS.find(([k]) => !used.has(k) && plans.some((p) => kindOf(p) === k));
    el.innerHTML = (form.basket.length > 1 ? form.basket.map((l, i) =>
      `<span class="w-bitem${l === form.plan ? " on" : ""}"><button type="button" data-bpick="${i}" title="Zmień tę usługę">${i + 1}. ${esc(lineLabel(l))}</button><button type="button" class="x" data-bdel="${i}" aria-label="Usuń z zestawu" title="Usuń z zestawu">×</button></span>`).join("") : "")
      + `<button type="button" class="w-badd" id="f-badd">+ Dodaj kolejną usługę${next ? ` (np. ${next[1].toLowerCase()})` : ""}</button>`;
  }
  function paintSum() {
    const el = $("f-sum"); if (!el || !form.plan) return;
    const c = calcPlan(form.plan);
    if (form.basket.length > 1) {
      const t = basketTotals();
      el.innerHTML = `<div><span>Razem miesięcznie za ${form.basket.length} usługi (netto)</span><br><b>${money(t.monthly)}</b>${t.average != null ? `<br><span>średnio ${money(t.average)} w okresie umowy</span>` : ""}
        <ul class="w-blines">${t.cs.map((x, i) => `<li>${esc(lineLabel(form.basket[i]))}: ${money(x.monthly)}${x.start != null ? ` <span>(${money(x.start)} przez ${x.months} mies.)</span>` : ""}</li>`).join("")}</ul></div>
        <div class="comm"><span>Twoja prowizja przy sprzedaży (ok.)</span><br><b>${money(t.commission)}</b></div>`;
    } else {
      el.innerHTML = c ? `<div><span>${c.perNumber ? "Zobowiązanie miesięczne" : "Opłata miesięczna"} (netto)</span><br><b>${money(c.monthly)}</b>${form.plan.quantity > 1 && c.perNumber ? ` <span>(${money(c.monthly / form.plan.quantity)} za numer)</span>` : ""}${c.start ? `<br><span>${money(c.start)} przez pierwsze ${c.months} mies., potem ${money(c.monthly)}</span>` : ""}${c.average != null ? `<br><span>średnio ${money(c.average)} w ${form.plan.term} mies.</span>` : ""}</div>
      <div class="comm"><span>Twoja prowizja przy sprzedaży (ok.)</span><br><b>${money(c.commission)}</b></div>` : `<span>Wybierz taryfę.</span>`;
    }
    document.querySelectorAll("#f-plans [data-plan]").forEach((b) => { const p = plans.find((x) => x.id === Number(b.dataset.plan)); b.setAttribute("aria-pressed", String(Number(b.dataset.plan) === form.plan.product)); b.querySelector("small").textContent = money(chipPrice(form.plan, p)); });
    document.querySelectorAll("#f-term [data-term]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.term) === form.plan.term)));
    const th = $("f-term-hint"); if (th) th.hidden = !(c && only24(c.p));
    if (c) {
      const ql = $("f-qty-label"); if (ql) ql.textContent = c.perNumber ? "Liczba numerów" : "Ilość";
      // promocje zależą od taryfy (TV nie ma żadnej, światłowód ma kilka)
      const pb = $("f-promos"); if (pb) pb.innerHTML = promoButtons(form.plan);
      const info = $("f-pinfo-name");
      if (info) info.textContent = c.p.name;
    }
    document.querySelectorAll("#f-kind [data-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.kind === form.plan.kind)));
    document.querySelectorAll("#f-home [data-home]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.home === form.plan.home)));
    const fh = $("f-home"); if (fh) fh.hidden = form.plan.kind !== "fiber";
    const fn = $("f-fiber-note"); if (fn) fn.hidden = form.plan.kind !== "fiber";
    paintBasket();
    const prev = $("f-mail-preview"); if (prev && !prev.hidden) prev.textContent = offerPreviewText();
  }
  /* Nowa pozycja zestawu: pierwsza grupa, której jeszcze nie ma (numery → światłowód → TV), taryfa „M” albo pierwsza */
  function newLine(kind) {
    const list = plansOf(kind, "blok"), m = list.find((p) => chipName(p) === "M") || list[0] || plans[0] || {};
    // światłowód ma umowę na 24 miesiące (regulamin T-Mobile), reszta – domyślny okres z ustawień
    return { product: m.id, quantity: 1, term: kind === "fiber" ? 24 : Number(config.default_term) || 36, promo: false, option: 0, kind, home: "blok" };
  }
  function renderPicker() {
    const box = $("f-picker"); if (!box) return;
    box.innerHTML = pickerInner(form.plan);
    wirePicker();
    paintSum();
  }
  function wirePicker() {
    const box = $("f-plans");
    if (box) box.addEventListener("click", (e) => { const b = e.target.closest("[data-plan]"); if (b) { form.plan.product = Number(b.dataset.plan); if (only24(planOf(form.plan))) form.plan.term = 24; paintSum(); } });
    /* Zmiana grupy albo rodzaju budynku: ta sama taryfa (np. „L”) w nowym wariancie, inaczej pierwsza z grupy */
    const pick = () => {
      const old = planOf(form.plan), list = plansOf(form.plan.kind, form.plan.home);
      const same = old && list.find((p) => chipName(p) === chipName(old));
      const m = same || list.find((p) => chipName(p) === "M") || list[0];
      if (m) form.plan.product = m.id;
      if (box) box.innerHTML = planChips(form.plan);
      paintSum();
    };
    // światłowód: umowa tylko na 24 miesiące (regulamin T-Mobile)
    document.querySelectorAll("#f-kind [data-kind]").forEach((b) => b.addEventListener("click", () => { form.plan.kind = b.dataset.kind; if (b.dataset.kind === "fiber") form.plan.term = 24; pick(); }));
    document.querySelectorAll("#f-home [data-home]").forEach((b) => b.addEventListener("click", () => { form.plan.home = b.dataset.home; pick(); }));
    document.querySelectorAll("#f-term [data-term]").forEach((b) => b.addEventListener("click", () => { form.plan.term = Number(b.dataset.term); paintSum(); }));
    const q = $("f-qty");
    if (q) {
      q.addEventListener("input", () => { form.plan.quantity = Math.max(1, Math.min(500, parseInt(q.value, 10) || 1)); paintSum(); });
      document.querySelectorAll("#f-picker [data-qty]").forEach((b) => b.addEventListener("click", () => { form.plan.quantity = Math.max(1, Math.min(500, form.plan.quantity + Number(b.dataset.qty))); q.value = form.plan.quantity; paintSum(); }));
    }
    const pr = $("f-promos");
    if (pr) pr.addEventListener("click", (e) => { const b = e.target.closest("[data-promo]"); if (!b) return; const i = Number(b.dataset.promo); form.plan.promo = i >= 0; form.plan.option = Math.max(0, i); paintSum(); });
    const det = $("f-pinfo"); if (det) det.addEventListener("click", () => openProductInfo(form.plan.product));
  }
  function wirePlan() {
    const basket = $("f-basket");
    if (basket) basket.addEventListener("click", (e) => {
      const add = e.target.closest("#f-badd"), pick = e.target.closest("[data-bpick]"), del = e.target.closest("[data-bdel]");
      if (add) {
        const used = new Set(form.basket.map((l) => l.kind));
        const kind = (KINDS.find(([k]) => !used.has(k) && plans.some((p) => kindOf(p) === k)) || [form.plan.kind])[0];
        form.plan = newLine(kind); form.basket.push(form.plan);
      } else if (pick) {
        form.plan = form.basket[Number(pick.dataset.bpick)];
      } else if (del) {
        const i = Number(del.dataset.bdel), was = form.basket[i] === form.plan;
        form.basket.splice(i, 1);
        if (was || !form.basket.includes(form.plan)) form.plan = form.basket[Math.max(0, i - 1)];
      } else return;
      renderPicker();
    });
    wirePicker();
    paintSum();
  }
  function defaultBasket() {
    const o = current && lastOffer(current);
    const find = (id, name) => plans.find((x) => x.id === id || x.name === name);
    if (o) {   // ostatnia oferta (także zestaw kilku usług) – agent zwykle sprzedaje to, co wysłał
      const lines = (o.offer.lines && o.offer.lines.length ? o.offer.lines : [o.offer])
        .map((l) => { const p = find(l.product, l.product_name); return p && { product: p.id, quantity: l.quantity, term: l.term_months, promo: !!l.promo, option: Number(l.option) || 0 }; })
        .filter(Boolean);
      if (lines.length) return lines;
    }
    return [newLine("mobile")];
  }
  /* Tekst do podglądu „jak zobaczy klient” – ten sam układ co oferta z serwera */
  function offerPreviewText() {
    const out = [];
    form.basket.forEach((sel, i) => {
      const c = calcPlan(sel); if (!c) return;
      if (i) out.push("", "-".repeat(30), "");
      out.push(`Oferta: ${c.p.name}`, `${c.perNumber ? "Liczba numerów" : "Ilość"}: ${sel.quantity}`, `Okres umowy: ${sel.term} miesięcy`);
      if (c.promo && c.label) out.push(`Promocja: ${c.label}`);
      out.push(`${c.perNumber ? "Zobowiązanie miesięczne" : "Opłata miesięczna"}: ${money(c.monthly)} netto`);
      if (c.start) out.push(`Przez pierwsze ${c.months} miesięcy: ${money(c.start)} netto miesięcznie, od ${c.months + 1}. miesiąca: ${money(c.monthly)} netto`);
      if (c.average != null) out.push(`Średni miesięczny koszt w okresie ${sel.term} miesięcy: ${money(c.average)} netto`);
      if (c.p.features) out.push("", ...c.p.features.split("\n").filter(Boolean).map((f) => "• " + f));
    });
    if (form.basket.length > 1) {
      const t = basketTotals();
      out.push("", "=".repeat(30), `RAZEM miesięcznie za wszystkie usługi: ${money(t.monthly)} netto`);
      if (t.average != null) out.push(`Średni miesięczny koszt w okresie umowy: ${money(t.average)} netto`);
    }
    return out.join("\n");
  }
  const basketLines = () => form.basket.filter((l) => planOf(l)).map((l) => ({ product: l.product, quantity: l.quantity, term_months: l.term, promo: linePromo(l), option: lineOption(l) }));

  /* ---------- informacje o produktach i kanały TV: okno „Produkty i kanały TV” ---------- */
  /* Szukanie bez względu na wielkość liter, polskie znaki i spacje: „tvp1” = „TVP 1”, „polsat sport” = „Polsat Sport” */
  const normFind = (s) => String(s || "").toLowerCase().replace(/ł/g, "l").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9+]/g, "");
  // jak klienci mówią na kanały → jak są zapisane na liście T-Mobile
  const CH_ALIASES = { jedynka: "tvp1", dwojka: "tvp2", trojka: "tvp3", czworka: "tv4", szostka: "tv6", siodemka: "tvnsiedem", tvn7: "tvnsiedem",
    canalplus: "canal+", kanalplus: "canal+", canal: "canal+", natgeo: "nationalgeographic", nationalgeo: "nationalgeographic", tvpinfo: "tvpinfo" };
  const findKey = (q) => { const n = normFind(q); return CH_ALIASES[n] || n; };
  /* Oficjalna lista kanałów (T-Mobile) przychodzi razem ze stroną; pakiety się sumują: M = S + …, L = S + M + … */
  const TV = (() => { try { return JSON.parse(document.getElementById("tv-channels").textContent); } catch { return null; } })();
  const TV_PLANS = ["S", "M", "L"];
  /* Wiersz informacji: „Kategoria: kanał, kanał, …” to lista kanałów (pokazujemy jako plakietki), reszta – zwykły punkt */
  // plakietki tylko dla list kanałów TV – zwykłe „Roaming UE: 8,95 GB” albo „15 zł netto, umowa…” zostają tekstem
  function infoRows(text, chips) {
    return String(text || "").split("\n").map((x) => x.trim()).filter(Boolean).map((x) => {
      const m = x.match(/^([^:]{2,30}):\s*(.+)$/);
      if (chips && m && !/^za dopłatą/i.test(x) && m[2].includes(",")) return { label: m[1], items: m[2].split(",").map((s) => s.trim()).filter(Boolean) };
      return { label: m && m[1].length < 40 ? m[1] : "", text: m && m[1].length < 40 ? m[2] : x, line: x };
    });
  }
  const tvPlanOf = (p) => { const m = kindOf(p) === "tv" && p.name.match(/\b(S|M|L)\s*$/i); return m ? m[1].toUpperCase() : null; };
  function rowsHtml(rows, key) {
    const hit = (t) => key && normFind(t).includes(key);
    if (key) rows = rows.map((r) => r.items ? { ...r, items: r.items.filter(hit) } : r).filter((r) => r.items ? r.items.length : hit(r.line));
    return rows.map((r) => r.items
      ? `<li class="w-chrow"><b>${esc(r.label)}:</b> ${r.items.map((c) => `<span class="w-ch${hit(c) ? " hit" : ""}">${esc(c)}</span>`).join("")}</li>`
      : `<li${hit(r.line) ? ' class="hit"' : ""}>${r.label ? `<b>${esc(r.label)}:</b> ${esc(r.text)}` : esc(r.text)}</li>`).join("");
  }
  function productInfoHtml(p, find) {
    const key = find ? findKey(find) : "";
    const promos = promosOf(p);
    const price = `<div class="w-pinfo-price">${money(p.price)} netto${Number(p.extra_price) > 0 ? ` · kolejny numer +${money(p.extra_price)}` : ""}</div>`
      + (promos.length ? `<ul class="w-pinfo-promos">${promos.map((x) => `<li><b>${esc(x.label || "Promocja")}</b>: ${x.months ? `${money(x.price)} przez ${x.months} mies., potem ` : ""}${money(x.after)}</li>`).join("")}</ul>` : "");
    const feat = p.features ? rowsHtml(infoRows(p.features, kindOf(p) === "tv"), key) : "";
    // TV bez ściągi w taryfie: pokazujemy kanały z oficjalnej listy (tylko te, które dochodzą w tym pakiecie)
    const plan = tvPlanOf(p);
    const own = !p.info && plan && TV ? TV.packages[plan].map((g) => ({ label: g.group, items: g.channels })) : null;
    const info = own ? rowsHtml(own, key) : p.info ? rowsHtml(infoRows(p.info, kindOf(p) === "tv"), key) : "";
    const head = own && plan !== "S" ? `<li class="w-pinfo-sum">Wszystkie kanały z pakietu ${plan === "M" ? "S" : "S i M"}, a do tego:</li>` : "";
    return (key ? "" : price)
      + (feat ? `<div class="w-pinfo-h">W ofercie dla klienta</div><ul>${feat}</ul>` : "")
      + (info ? `<div class="w-pinfo-h">${own ? `Kanały (lista T-Mobile, stan na ${esc(TV.as_of)})` : "Dla agenta"}</div><ul>${key ? "" : head}${info}</ul>` : "");
  }
  /* Kanały pasujące do wpisu, najlepsze na górze: „1” → najpierw TVP 1, Polsat Sport 1, Eurosport 1 (całe słowo),
     potem kanały zaczynające się od wpisu, na końcu reszta. Każdy: od którego pakietu jest (-1 = tylko za dopłatą). */
  function channelMatches(find) {
    const key = findKey(find); if (!key || !TV) return [];
    const words = String(find).toLowerCase().trim();
    const found = new Map();
    TV_PLANS.forEach((plan, i) => TV.packages[plan].forEach((g) => g.channels.forEach((c) => {
      if (normFind(c).includes(key) && !found.has(c)) found.set(c, i);
    })));
    TV.extra.forEach((c) => { if (normFind(c).includes(key) && !found.has(c)) found.set(c, -1); });
    const wordRe = new RegExp("(^|[\\s(])" + words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "($|[\\s)])", "i");
    const rank = (c) => wordRe.test(c) ? 0 : normFind(c).startsWith(key) ? 1 : 2;
    return [...found].map(([name, from], i) => ({ name, from, r: rank(name), i })).sort((a, b) => a.r - b.r || a.i - b.i);
  }
  function channelAnswer(find) {
    if (!TV) return `<p class="w-info">Lista kanałów nie wczytała się – odśwież stronę.</p>`;
    const all = channelMatches(find);
    if (!all.length) return `<div class="w-chans no"><b>✕ Nie ma takiego kanału</b> w żadnym pakiecie Magenta TV (lista T-Mobile, stan na ${esc(TV.as_of)}).<br><span class="muted">Sprawdź pisownię, np. „TVN 24”, „Polsat Sport”, „jedynka”.</span></div>`;
    const tvProducts = plans.filter((p) => kindOf(p) === "tv");
    const priceOf = (plan) => { const p = tvProducts.find((x) => tvPlanOf(x) === plan); return p ? ` <span class="muted">(${money(p.price)})</span>` : ""; };
    const LIMIT = 20;
    const rows = all.slice(0, LIMIT).map(({ name, from }) => {
      if (from < 0) return `<li><span class="w-ch hit">${esc(name)}</span> <b>tylko za dopłatą</b> – pakiet CANAL+ SUPER SPORT (do S, M albo L)</li>`;
      return `<li><span class="w-ch hit">${esc(name)}</span> ${from === 0 ? "jest <b>we wszystkich pakietach</b>" : `jest od pakietu <b>${TV_PLANS[from]}</b>`}:
        ${TV_PLANS.map((x, i) => i >= from ? `<span class="w-pk yes">✓ ${x}${priceOf(x)}</span>` : `<span class="w-pk no">✕ ${x}</span>`).join("")}</li>`;
    }).join("");
    return `<div class="w-chans"><div class="w-pinfo-h">Czy jest ten kanał? (${all.length})</div><ul>${rows}</ul>${all.length > LIMIT ? `<p class="muted small">…i jeszcze ${all.length - LIMIT}. Wpisz dokładniej.</p>` : ""}</div>`;
  }
  /* Okno ma dwie zakładki: „Kanały TV” (klient pyta „czy jest Jedynka?”) i „Taryfy i produkty” (ceny, co zawiera,
     szukanie po produktach, np. „300” → Światłowód M). Każda ma swoje pole szukania. */
  const PINFO_HINT = { tv: "Czy jest kanał…? Wpisz np. jedynka, 1, HBO, Eurosport, TVN 24, Polsat Sport",
    prod: "Szukaj w taryfach, np. 300 Mb/s, roaming, JUMP, dekoder" };
  function openProductInfo(productId) {
    let dlg = $("pinfo-dialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "pinfo-dialog"; dlg.className = "dialog w-pdialog";
      dlg.innerHTML = `<div class="w-pdhead"><h2>Produkty i kanały TV</h2><button type="button" class="btn-mini" data-close>Zamknij</button></div>
        <div class="w-toggle w-pmode" id="pinfo-mode"><button type="button" data-mode="tv">Kanały TV</button><button type="button" data-mode="prod">Taryfy i produkty</button></div>
        <input id="pinfo-find" class="select" type="search" autocomplete="off">
        <div class="w-toggle" id="pinfo-kinds"></div>
        <p class="muted small" id="pinfo-note"></p>
        <div id="pinfo-list" class="w-plist"></div>`;
      document.body.appendChild(dlg);
      dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
      const findEl = dlg.querySelector("#pinfo-find");
      findEl.addEventListener("keydown", (e) => e.stopPropagation());
      findEl.addEventListener("input", () => { dlg.dataset["find_" + dlg.dataset.mode] = findEl.value; paintProductInfo(); });
      dlg.querySelector("#pinfo-mode").addEventListener("click", (e) => { const b = e.target.closest("[data-mode]"); if (b) { setPinfoMode(dlg, b.dataset.mode); findEl.focus(); } });
      dlg.querySelector("#pinfo-kinds").addEventListener("click", (e) => { const b = e.target.closest("[data-pk]"); if (b) { dlg.dataset.kind = b.dataset.pk; dlg.dataset.only = ""; paintProductInfo(); } });
      dlg.querySelector("#pinfo-list").addEventListener("click", (e) => { if (e.target.closest("[data-pall]")) { dlg.dataset.only = ""; paintProductInfo(); } });
    }
    const p = productId && plans.find((x) => x.id === Number(productId));
    dlg.dataset.only = p ? String(p.id) : "";
    if (p) dlg.dataset.kind = kindOf(p);
    else if (!dlg.dataset.kind) dlg.dataset.kind = form && form.plan ? form.plan.kind || "mobile" : "mobile";
    dlg.dataset.find_tv = ""; dlg.dataset.find_prod = "";
    setPinfoMode(dlg, p ? "prod" : "tv");   // z „Co zawiera …” – od razu ta taryfa; z lewego panelu – kanały
    dlg.showModal();
    dlg.querySelector("#pinfo-find").focus();
  }
  function setPinfoMode(dlg, mode) {
    dlg.dataset.mode = mode;
    const f = dlg.querySelector("#pinfo-find");
    f.placeholder = PINFO_HINT[mode]; f.value = dlg.dataset["find_" + mode] || "";
    dlg.querySelectorAll("#pinfo-mode [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    dlg.querySelector("#pinfo-kinds").hidden = mode !== "prod";
    dlg.querySelector("#pinfo-note").textContent = mode === "tv"
      ? `Pakiety TV się sumują: M ma wszystko z S, a L wszystko z S i M.${TV ? ` Lista kanałów: T-Mobile, stan na ${TV.as_of}.` : ""}`
      : "Ceny netto. Szukanie przeszukuje wszystkie grupy taryf.";
    paintProductInfo();
  }
  function paintProductInfo() {
    const dlg = $("pinfo-dialog"); if (!dlg) return;
    const list = dlg.querySelector("#pinfo-list");
    const find = dlg.querySelector("#pinfo-find").value.trim(), key = find ? findKey(find) : "";
    if (dlg.dataset.mode === "tv") {
      list.innerHTML = key ? channelAnswer(find)
        : `<p class="w-info">Wpisz nazwę kanału, o który pyta klient. Zobaczysz, w których pakietach jest (S / M / L).</p>`
          + (TV ? `<ul class="w-pinfo-promos">${TV_PLANS.map((x) => `<li><b>Magenta TV ${x}</b>: ${TV.counts[x]} kanałów</li>`).join("")}</ul>` : "");
      return;
    }
    const only = !find && Number(dlg.dataset.only) || 0;
    const kinds = KINDS.filter(([k]) => plans.some((p) => kindOf(p) === k));
    dlg.querySelector("#pinfo-kinds").innerHTML = kinds.map(([k, l]) => `<button type="button" data-pk="${k}" aria-pressed="${!find && !only && dlg.dataset.kind === k}">${l}</button>`).join("");
    // szukanie przeszukuje wszystkie grupy taryf
    const text = (p) => p.name + "\n" + (p.features || "") + "\n" + (p.info || "") + (tvPlanOf(p) && !p.info && TV ? "\n" + TV.packages[tvPlanOf(p)].map((g) => g.channels.join(",")).join(",") : "");
    const shown = plans.filter((p) => only ? p.id === only : key ? normFind(text(p)).includes(key) : kindOf(p) === dlg.dataset.kind);
    const nameHit = (p) => !!key && normFind(p.name).includes(key);
    // pasuje nazwa (np. „jump”) → cała karta; inaczej tylko pasujące punkty
    const card = (p) => `<section class="w-pcard"><h3>${esc(p.name)}</h3>${productInfoHtml(p, nameHit(p) ? "" : find)}</section>`;
    if (!key) {
      list.innerHTML = (only ? `<p><button type="button" class="w-link" data-pall>← Pokaż wszystkie produkty z tej grupy</button></p>` : "")
        + (shown.length ? shown.map(card).join("") : `<p class="w-info">Brak taryf w tej grupie.</p>`);
      return;
    }
    // kolejność: pasuje nazwa → punkty oferty (np. „Prędkość: do 300 Mb/s”) → tylko ściąga dla agenta
    const rank = (p) => nameHit(p) ? 0 : normFind(p.features).includes(key) ? 1 : 2;
    const sorted = shown.map((p, i) => [rank(p), i, p]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
    const tvHits = channelMatches(find).length;
    list.innerHTML = (sorted.length ? sorted.map(card).join("") : `<p class="w-info">Nic nie znaleziono dla „${esc(find)}”.</p>`)
      + (tvHits ? `<p class="muted small">Szukasz kanału? Pasuje ${tvHits} – <button type="button" class="w-link" data-gotv>sprawdź w „Kanały TV”</button>.</p>` : "");
    const go = list.querySelector("[data-gotv]");
    if (go) go.addEventListener("click", () => { dlg.dataset.find_tv = find; setPinfoMode(dlg, "tv"); });
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
      return `<div class="w-step"><h3>Oferta dla klienta</h3>${planPicker()}
        <label class="w-field grow">E-mail klienta${config.mode === "direct" ? "" : " (żeby osoba wysyłająca wiedziała, dokąd)"}<input id="f-email" type="email" maxlength="254" value="${esc(d.email || "")}" placeholder="np. biuro@firma.pl"></label>
        ${whenField("offer", "Kiedy zadzwonić w sprawie oferty (follow-up)", nextWorkday(10), true)}
        <label class="w-field grow">Sprawa przy follow-upie<input id="f-topic" maxlength="255" value="Omówić wysłaną ofertę"></label>
        <p class="w-info">Oferta pójdzie ${target}. <button type="button" class="w-link" id="f-show-preview">Podgląd oferty</button></p>
        <pre class="w-preview" id="f-mail-preview" hidden></pre></div>`;
    }
    if (o === "sold") {
      return `<div class="w-step"><h3>Co klient kupił?</h3>${planPicker()}<p class="w-info">Klient zostanie zamknięty jako <b>sprzedany</b>. Prowizja doliczy się do Twoich wyników.</p>
        <p class="w-info">Zapytaj o polecenie: „Kogo Pan zna, kto jeszcze mógłby skorzystać?” → <button type="button" class="w-link" data-referral>+ dodaj polecony kontakt</button></p></div>`;
    }
    if (o === "refused") return `<div class="w-step"><h3>Dlaczego odmówił?${reqRefuse ? "" : " (opcjonalnie)"} <small class="muted">dopisze się do komentarza</small></h3><div class="w-chips">${REFUSE_CHIPS.map((c) => `<button type="button" class="w-chip" data-refuse="${esc(c)}">${esc(c)}</button>`).join("")}</div></div>`;
    if (o === "wrong_number") return `<div class="w-step"><h3>Zły numer</h3>
      <p class="w-info">Bez nowego numeru klient zostanie zamknięty (<b>nie dzwonić</b>).</p>
      <label class="w-field grow">Dostałeś inny numer do tej firmy? (np. żona podała numer męża, sekretariat – numer szefa)<input id="f-newphone" maxlength="30" inputmode="tel" placeholder="np. 600 100 200"></label>
      <p class="w-info" id="f-newphone-info">Jeśli wpiszesz nowy numer, klient <b>zostaje u Ciebie</b>: nowy numer staje się głównym, stary zapisze się jako dodatkowy, a w historii zostanie notatka.</p></div>`;
    if (o === "skipped") return `<div class="w-step"><h3>Dlaczego pomijasz tę firmę?${reqSkip ? "" : " (opcjonalnie)"}</h3><div class="w-chips" role="radiogroup">${SKIP_CHIPS.map((l) => `<button type="button" class="w-chip" role="radio" aria-checked="false" data-skip="${esc(l)}">${esc(l)}</button>`).join("")}</div><p class="w-info">Pominięcie nie liczy się jako telefon ani próba.</p></div>`;
    return "";
  }
  const SAVE_HINT = { no_answer: "Enter zapisuje i bierze następnego", callback: "Wpisz termin, Enter zapisuje", interested: "Sprawdź termin, Enter zapisuje", offer: "Wybierz taryfę i termin follow-upu", sold: "Wybierz taryfę i liczbę numerów", refused: "Enter zapisuje i bierze następnego", wrong_number: "Enter zapisuje i bierze następnego", skipped: "Enter zapisuje i bierze następnego" };
  function pick(o) {
    if (!current || CLOSED.includes(current.status) || !$("out-panel")) return;
    if (form && form.outcome === o) { resetForm(); return; }
    const basket = ["offer", "sold"].includes(o) && plans.length ? defaultBasket() : null;
    form = { outcome: o, basket, plan: basket ? basket[0] : null, skip: "" };
    document.querySelectorAll("[data-out]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.out === o)));
    $("step").innerHTML = stepHtml(o) + (TALKED.includes(o) ? `<div class="w-step w-step-net">${networkHtml(current)}${contractHtml(current)}</div>` : "");
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
      $("f-cal").addEventListener("click", () => {
        const d = parseWhen(inp.value.trim());
        if (d && d > new Date()) nat.value = fmtNative(d);   // kalendarz otwiera się na już wpisanym terminie
        try { nat.showPicker(); } catch { nat.focus(); nat.click(); }
      });
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
    wireContract();
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
    if (contractSaving) await contractSaving;
    const o = form.outcome, lead = current.id;
    if (o === "offer") {
      if (!form.plan || !basketLines().length) { fail("Wybierz taryfę."); return; }
      const w = readWhen(true); if (!w.ok) { fail(w.error); return; }
      const email = $("f-email").value.trim();
      if (config.mode === "direct" && !email) { fail("Podaj e-mail klienta."); return; }
      return submitOffer({ lead, lines: basketLines(),
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
        if (!basketLines().length) { fail("Wybierz taryfę."); return; }
        body.sale_lines = basketLines();   // zestaw: numery + światłowód + TV w jednej sprzedaży
      }
    }
    if (o === "skipped") { if (!form.skip && reqSkip) { fail("Wybierz powód pominięcia."); return; } if (form.skip) body.skip_reason = form.skip; }
    if (o === "refused" && reqRefuse && !note) { fail("Wybierz albo wpisz, dlaczego klient odmówił."); return; }
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
    // „Zapisz i dalej”: serwer od razu oddaje następnego klienta (pole `next`) – jedno zapytanie
    const r = await jsonReq(goNext ? "/api/activities/?next=1" : "/api/activities/", "POST", body);
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
    // „Zapisz i dalej”: od razu następny klient, a listy po lewej i panel „Dziś” odświeżają się w tle
    if (goNext) { await nextClient(true, saved && saved.next); refreshAll(); armAutoDial(); return; }
    await refreshAll();
    if (leadId) openLead(leadId);
  }
  async function refreshAll() { await Promise.all([loadLeads(), loadSummary(), loadHistory(), loadSideData()]); }

  /* ---------- „Następny klient” ---------- */
  const REASON_TOAST = { offer: "Follow-up po ofercie", callback: "Umówione oddzwonienie", retry: "Ponowna próba", new: "Nowa firma" };
  /* Bez mrugania: przyciski i karta przygasają dopiero, gdy serwer odpowiada dłużej niż 0,3 s
     (szybka odpowiedź nie zmienia wyglądu); drugie kliknięcie w trakcie nic nie robi */
  let nextBusy = false;
  async function nextClient(refreshLater, ready) {
    if (nextBusy) return;
    nextBusy = true;
    const pane = $("lead-pane");
    const slow = setTimeout(() => {
      document.querySelectorAll("#claim-btn, [data-next-btn]").forEach((b) => { b.disabled = true; });
      if (pane) pane.classList.add("w-busy");
    }, 300);
    try {
      // `ready` = następny klient przyszedł już w odpowiedzi na zapis („Zapisz i dalej”)
      const r = ready ? { ok: !!ready.lead, status: ready.lead ? 200 : 404 } : await api("/api/work/next/", { method: "POST" });
      const d = ready || await r.json().catch(() => ({}));
      if (r.status === 404) { welcome(d.detail || "Na teraz nie ma nic do zrobienia."); return; }
      if (!r.ok) { toast(d.detail || "Nie udało się pobrać klienta.", true); return; }
      await openLead(d.lead.id, d.reason, d.lead.activities ? d.lead : null);
      if (!refreshLater) { if (!leads.some((l) => l.id === d.lead.id)) loadLeads(); loadSummary(); }
      CRM.scrollTop();
    } catch { toast("Brak połączenia z serwerem.", true); } finally {
      clearTimeout(slow); nextBusy = false;
      document.querySelectorAll("#claim-btn, [data-next-btn]").forEach((b) => { b.disabled = false; });
      if (pane) pane.classList.remove("w-busy");
    }
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
    if (e.key === "Escape" && autoDial) { stopAutoDial("Wstrzymane. Zadzwoń, kiedy będziesz gotowy."); return; }
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
  $("pinfo-open").addEventListener("click", () => openProductInfo());
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
    // wszystko naraz (nie w dwóch turach po kolei) – ekran otwiera się szybciej
    const lists = Promise.all([loadLeads(), loadSummary(), loadDialer(), loadSideData(), loadHistory()]);
    const [pr, cf, rl] = await Promise.all([api("/api/products/?is_active=true&page_size=500").catch(() => null), api("/api/offer-config/").catch(() => null), api("/api/settings/").catch(() => null)]);
    try { if (rl && rl.ok) { const d = await rl.json(); if (d.work_days) workDays = new Set([...d.work_days].map(Number));
      reqSkip = !!d.require_skip_reason; reqRefuse = !!d.require_refuse_reason;
      if (d.quick_phrases) { const q = d.quick_phrases; PHRASES = { ...PHRASES, ...q, refused: [], skipped: [] }; if (Array.isArray(q.refused)) REFUSE_CHIPS = q.refused;
        if (Array.isArray(q.skip_never) && Array.isArray(q.skip_lost)) SKIP_CHIPS = [...q.skip_never, ...q.skip_lost]; }
      if (form) paintPhrases(form.outcome); } } catch { /* pon–pt */ }
    try { if (pr && pr.ok) { const d = await pr.json(); plans = (d.results || d).filter((p) => p.is_active); } } catch { /* bez taryf */ }
    try { if (cf && cf.ok) config = { ...config, ...(await cf.json()) }; } catch { /* domyślne ustawienia */ }
    await lists;
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

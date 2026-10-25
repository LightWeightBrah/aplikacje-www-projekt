/* Kolejka agenta: lista leadów (grupy wg pilności), karta rozmowy i szybki zapis wyniku. */
(() => {
  if (!CRM.esc || !CRM.$ || !CRM.STATUS || typeof WHEN === "undefined") {
    document.getElementById("lead-list").textContent = "Wczytała się stara wersja strony. Odśwież ją skrótem Ctrl+F5.";
    return;
  }
  const { api, esc, STATUS, $ } = CRM;
  const me = CRM.currentUser() || {};
  const SCHEDULING = ["callback", "interested", "no_answer"];
  const REQUIRED = ["callback", "interested"];   // te wyniki wymagają terminu (sprawa jest opcjonalna)
  const { parseWhen, fmtInput, fmtLong, fmtNative } = WHEN;
  const OUTCOMES = [
    ["no_answer", "Nie odebrał", "Zaliczy próbę. Po wyczerpaniu limitu prób lead wraca do puli. Możesz zaplanować ponowną próbę."],
    ["callback", "Oddzwonić", "Lead zostaje u Ciebie do wskazanego terminu."],
    ["interested", "Zainteresowany", "Status „Zainteresowany”. Wpisz, kiedy ponownie zadzwonić (sprawa jest opcjonalna)."],
    ["sold", "Sprzedane", "Lead zostaje zamknięty jako wygrany (sprzedaż). Znika z Twojej kolejki."],
    ["refused", "Odmowa", "Lead zostaje zamknięty jako przegrany."],
    ["wrong_number", "Zły numer", "Lead zostaje zamknięty jako „Nie dzwonić”."],
  ];
  const OUT_LABEL = { ...Object.fromEntries(OUTCOMES.map((o) => [o[0], o[1]])), note: "Notatka" };
  const CLOSED = ["won", "lost", "do_not_call", "unreachable"];
  const GROUPS = ["Zaległe – oddzwoń teraz", "Dziś", "Ponowne próby – jeszcze nie odebrali", "Zaplanowane", "Do pierwszego telefonu", "Bez terminu"];
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("pl-PL",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–");
  const chip = (code) => { const [l, c] = STATUS[code] || [code, "#64748b"]; return `<span class="chip" style="--c:${c}">${esc(l)}</span>`; };
  const cache = { companies: {}, products: {} };
  let leads = [], selectedId = null, outcome = null, resetForm = null, current = null, lastListHtml = "", savedNote = "", rules = { max_attempts_per_agent: 3 }, autoRetry = false;
  const OUT_COLOR = { no_answer: "#64748b", callback: "#f59e0b", interested: "#4f46e5", sold: "#22c55e",
    refused: "#ef4444", wrong_number: "#a855f7", note: "#94a3b8" };
  const MISSED_WINDOW_MS = 5 * 60000;
  /* Umówiony kontakt, którego termin minął albo właśnie nadszedł (nieodebrany telefon liczy się wtedy jako „nie w terminie”) */
  const isDue = (l) => !!l && !!l.next_contact_at && new Date(l.next_contact_at).getTime() <= Date.now() + MISSED_WINDOW_MS;
  const PROMISED = ["callback", "interested", "offer"];
  /* Umówiony kontakt (obiecany klientowi), którego termin minął lub właśnie nadszedł; zwykła ponowna próba po nieodebraniu się nie liczy */
  const isMissable = (l) => isDue(l) && PROMISED.includes(l.status);
  const isFresh = (l) => !!l && l.status === "new" && !!l.owner;   // nikt jeszcze z tym leadem nie rozmawiał
  const triesOf = (l) => (l && l.owner_no_answers) || 0;
  const maxTries = () => rules.max_attempts_per_agent || 3;
  const isLastTry = (l) => isFresh(l) && triesOf(l) + 1 >= maxTries();
  const raz = (n) => (n === 1 ? "raz" : "razy");
  /* Plakietka statusu: „Nowy” rozbijamy na „jeszcze nie dzwoniłeś” i „nie odbiera – próba N z M”, żeby było widać, na czym stoi lead */
  function statusChip(l) {
    if (!isFresh(l)) return chip(l.status);
    const n = triesOf(l);
    return n === 0
      ? `<span class="try-chip fresh" title="Nikt jeszcze nie dzwonił do tego leada">Nowy · nie dzwoniłeś</span>`
      : `<span class="try-chip" title="Jeszcze z nim nie rozmawiałeś. Po ${maxTries()} nieodebranych próbach lead wraca do puli.">Nie odbiera · ${n} z ${maxTries()}</span>`;
  }
  const longDate = (d) => d.toLocaleString("pl-PL", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  const missedChip = (l) => (l.missed_calls > 0 ? `<span class="missed-chip">Nie odebrał w terminie${l.missed_calls > 1 ? " ×" + l.missed_calls : ""}</span>` : "");
  const ICON_NOTE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  const refreshReminders = () => { if (CRM.refreshReminders) CRM.refreshReminders(); };
  const plOverdue = (n) => (n === 1 ? "kontakt do wykonania" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "kontakty do wykonania" : "kontaktów do wykonania");
  /* Inni zaległi (najstarszy pierwszy): „Zapisz i weź kolejnego” najpierw prowadzi do nich, dopiero potem do puli */
  /* Inne kontakty z minionym terminem: najpierw obiecane oddzwonienia, potem ponowne próby (najstarsze pierwsze) */
  const otherOverdue = (leadId) => leads
    .filter((l) => l.id !== leadId && l.next_contact_at && new Date(l.next_contact_at) < new Date())
    .sort((a, b) => (isFresh(a) - isFresh(b)) || (new Date(a.next_contact_at) - new Date(b.next_contact_at)));

  function rel(iso) {
    const m = Math.floor(new Date(iso) / 60000) - Math.floor(Date.now() / 60000), a = Math.abs(m);   // po granicach minut zegara: wszystkie wpisy zmieniają się razem
    if (a === 0) return "teraz";
    const t = a < 60 ? `${a} min` : a < 1440 ? `${Math.round(a / 60)} h` : Math.round(a / 1440) === 1 ? "1 dzień" : `${Math.round(a / 1440)} dni`;
    return m < 0 ? `${t} temu` : `za ${t}`;
  }
  function groupOf(l) {
    const retry = isFresh(l) && triesOf(l) > 0;   // do tego leada już dzwoniono, ale nikt nie odebrał
    if (!l.next_contact_at) return retry ? 2 : isFresh(l) ? 4 : 5;
    const t = new Date(l.next_contact_at), now = new Date();
    if (retry) return 2;   // ponowna próba to zadanie miękkie: nigdy „zaległe – oddzwoń teraz”
    if (t < now) return 0;
    return t.toDateString() === now.toDateString() ? 1 : 3;
  }
  /* Kolejność: grupa, w zaległych najpierw obiecane oddzwonienia, potem ponowne próby; w grupie po terminie */
  const timeKey = (l) => (l.next_contact_at ? new Date(l.next_contact_at).getTime() : Infinity);
  function byUrgency(a, b) {
    const ga = groupOf(a), gb = groupOf(b);
    if (ga !== gb) return ga - gb;
    if (ga === 0 && isFresh(a) !== isFresh(b)) return isFresh(a) ? 1 : -1;
    return timeKey(a) - timeKey(b);
  }
  async function ref(kind, id) {
    if (!id) return null;
    if (!(id in cache[kind])) { const r = await api(`/api/${kind}/${id}/`); cache[kind][id] = r.ok ? await r.json() : null; }
    return cache[kind][id];
  }
  let toastTimer;
  function toast(msg, isError) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
  }
  const errText = (d) => Object.values(d || {}).flat().map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ") || "Nie udało się zapisać.";

  /* Pasek dnia nad kolejką: ile już zrobiono dziś i ile jeszcze czeka */
  async function loadSummary() {
    const box = $("day-bar");
    if (!box) return;
    box.hidden = !CRM.dayBarEnabled();
    if (box.hidden) return;
    try {
      const r = await api("/api/my-leads/summary/");
      if (!r.ok) return;
      const d = await r.json();
      const todo = d.left_today + d.overdue + d.retries_due, total = d.done_today + todo;
      const pct = total ? Math.round((d.done_today / total) * 100) : 0;
      const parts = [
        `<span title="Rozmów: ${d.talked_today}, nieodebranych: ${d.done_today - d.talked_today}"><b>${d.done_today}</b> wykonane</span>`,
        `<span><b>${d.left_today}</b> jeszcze dziś</span>`,
        d.overdue ? `<span class="db-late"><b>${d.overdue}</b> zaległe</span>` : "",
        d.retries_due ? `<span class="db-retry" title="Ponowne próby leadów, z którymi jeszcze nie rozmawiałeś"><b>${d.retries_due}</b> do ponowienia</span>` : "",
      ].filter(Boolean).join('<span class="db-sep">·</span>');
      box.innerHTML = `<div class="db-text"><span class="db-label">Dziś</span>${parts}</div>
        <div class="db-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="Postęp dnia"><i style="width:${pct}%"></i></div>`;
    } catch { /* pasek jest dodatkiem: brak danych nie przeszkadza w pracy */ }
  }
  document.addEventListener("crm:view", loadSummary);   // agent włączył / wyłączył pasek w oknie „Powiadomienia i widok”

  async function loadLeads() {
    let url = "/api/my-leads/", all = [];
    for (let i = 0; i < 5 && url; i++) {
      const d = await (await api(url)).json();
      all = all.concat(d.results);
      url = d.next ? d.next.replace(location.origin, "") : null;
    }
    leads = all.filter((l) => !CLOSED.includes(l.status)).sort(byUrgency);
    renderList();
    loadSummary();
  }

  /* Jeden wiersz listy; extra = dodatkowa linia (np. „Pasuje numer telefonu”) pod numerem */
  function leadItem(l, g, extra = "") {
    const when = l.next_contact_at ? `<small class="when${g === 0 ? " late" : g === 2 && new Date(l.next_contact_at) < new Date() ? " soft" : ""}" title="${fmt(l.next_contact_at)}">${rel(l.next_contact_at)} · ${fmt(l.next_contact_at)}</small>` : "";
    const overdue = g === 0;   // obiecany kontakt, którego termin minął
    const retryDue = isFresh(l) && !!l.next_contact_at && new Date(l.next_contact_at) <= Date.now();   // ponowna próba, na którą już pora
    return `<button type="button" class="lead-item${l.id === selectedId ? " active" : ""}${retryDue ? " retry-due" : ""}${overdue ? " promise-late" : ""}" data-id="${l.id}">
        <div><strong>${esc(l.contact_name)}</strong>${statusChip(l)}</div><small>${esc(l.phone)}</small>${retryDue ? `<small class="retry-now">Czas na ponowną próbę</small>` : ""}${CRM.lastContact(l, me.id)}${extra}${l.transferred_from ? `<small class="transfer">↪ przepisany od ${esc(l.transferred_from)}</small>` : ""}${when}${l.next_contact_note ? `<small class="why" title="Sprawa">${ICON_NOTE}<span>${esc(l.next_contact_note)}</span></small>` : ""}${l.missed_calls > 0 ? `<small>${missedChip(l)}</small>` : ""}</button>`;
  }

  function renderList() {
    const heading = $("lead-count").closest("h2");
    if (heading) heading.hidden = found !== null;
    if (found !== null) { renderFound(); return; }
    $("lead-count").textContent = leads.length ? `(${leads.length})` : "";
    if (!leads.length) { lastListHtml = ""; $("lead-list").innerHTML = `<p class="muted">Brak aktywnych leadów. Kliknij „Daj kolejnego leada”.</p>`; return; }
    leads.sort(byUrgency);   // pilność zmienia się z czasem (termin mija), więc sortujemy przy każdym rysowaniu
    let html = "", last = -1;
    leads.forEach((l) => {
      const g = groupOf(l);
      if (g !== last) { html += `<h3 class="grp g${g}">${GROUPS[g]}</h3>`; last = g; }
      html += leadItem(l, g);
    });
    if (html !== lastListHtml) { lastListHtml = html; $("lead-list").innerHTML = html; }   // bez migotania, gdy nic się nie zmieniło
  }

  /* ---------- wyszukiwarka z góry strony: lista zawęża się do wyników, najlepszy otwiera się sam ---------- */
  let found = null, foundQ = "", searchSeq = 0;   // found = null: zwykła kolejka
  function renderFound() {
    let html = `<div class="found-bar"><span>Wyniki dla „${esc(foundQ)}”: <b>${found.length}</b></span><button type="button" class="found-clear" id="found-clear">Cała kolejka</button></div>`;
    if (!found.length) html += `<p class="muted">Brak wyników.</p>`;
    found.forEach((l) => { html += leadItem(l, groupOf(l), `<small class="found-line">${CRM.searchMatch(l, foundQ)}</small>`); });
    if (html !== lastListHtml) { lastListHtml = html; $("lead-list").innerHTML = html; }
  }
  function matchBanner(id) {
    const i = found === null ? -1 : found.findIndex((x) => x.id === id);
    if (i < 0) return "";
    return `<div class="match-banner">${CRM.searchMatch(found[i], foundQ)}
      ${found.length > 1 ? `<span class="match-n">Wynik ${i + 1} z ${found.length}</span>` : ""}
      <button type="button" class="found-clear" id="banner-clear">Zamknij wyszukiwanie</button></div>`;
  }
  const searchWrap = () => $("gsearch");
  function clearSearch() {
    found = null; foundQ = ""; searchSeq++;
    const input = $("gsearch-input");
    if (input) input.value = "";
    if (searchWrap()) searchWrap().classList.remove("loading");
    renderList();
  }
  async function doSearch(text) {
    const query = (text || "").trim();
    if (query.length < 2) { if (found !== null) { found = null; foundQ = ""; renderList(); } return; }
    if (/^[\d\s+\-().]+$/.test(query) && query.replace(/\D/g, "").length < 3) return;   // za mało cyfr, czekamy na kolejne
    const mine = ++searchSeq;
    if (searchWrap()) searchWrap().classList.add("loading");
    let data;
    try {
      const r = await api("/api/leads/lookup/?q=" + encodeURIComponent(query));
      if (!r.ok) throw new Error();
      data = await r.json();
    } catch {
      if (mine === searchSeq) { if (searchWrap()) searchWrap().classList.remove("loading"); toast("Nie udało się wyszukać. Spróbuj ponownie.", true); }
      return;
    }
    if (mine !== searchSeq) return;   // w międzyczasie wpisano coś nowego
    if (searchWrap()) searchWrap().classList.remove("loading");
    found = data; foundQ = query;
    if (data.length) { await select(data[0].id); return; }   // najlepsze dopasowanie (dokładne są pierwsze) otwiera się od razu
    selectedId = null; current = null; renderList();
    $("lead-pane").innerHTML = `<div class="card empty"><h2>Nie znaleziono leada</h2>
      <p class="muted">Nic nie pasuje do „${esc(query)}”. Szukam po numerze telefonu, NIP, KRS, nazwisku, firmie i e-mailu.</p></div>`;
  }
  document.addEventListener("crm:search", (e) => { e.preventDefault(); doSearch(e.detail && e.detail.q); });   // preventDefault = „obsłużone w kolejce”

  function updatePreview() {
    const v = $("cb-at").value.trim(), pv = $("cb-preview");
    if (!v) { pv.textContent = "np. jutro 9:30 · 25.10 10:23 · pon 8 · za 2h"; pv.className = "hint"; return; }
    const d = parseWhen(v);
    if (!d) { pv.textContent = "Nie rozumiem. Spróbuj np. „jutro 9:30”, „25.10 10:23”, „pon 8”, „za 2h”."; pv.className = "hint bad"; return; }
    const past = d < new Date();
    pv.textContent = (past ? "Ten termin już minął: " : "") + fmtLong(d);
    pv.className = "hint " + (past ? "bad" : "ok");
  }
  const atNine = (days) => { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(9, 0, 0, 0); return d; };
  const PRESETS = [["Za 30 min", () => new Date(Date.now() + 1800e3)], ["Za godzinę", () => new Date(Date.now() + 3600e3)], ["Jutro 9:00", () => atNine(1)],
    ["Za 3 dni", () => atNine(3)], ["Za tydzień", () => atNine(7)]];

  function emptyPane() {
    selectedId = null; renderList();
    $("lead-pane").innerHTML = `<div class="card empty"><h2>Wybierz leada z listy</h2>
      <p class="muted">albo pobierz następnego z puli przyciskiem „Daj kolejnego leada”.</p></div>`;
  }

  async function select(id) {
    selectedId = id; renderList();
    const r = await api(`/api/leads/${id}/`);
    if (!r.ok) { toast("Nie udało się wczytać leada.", true); return; }
    const d = await r.json();
    const [company, product] = await Promise.all([ref("companies", d.company), ref("products", d.product)]);
    outcome = null; current = d; autoRetry = false;
    renderCard(d, company, product);
  }

  /* Otwarcie leada z zewnątrz (karta przypomnienia, powiadomienie) bez przeładowania strony */
  CRM.openLead = async (id) => {
    clearSearch();
    if (!leads.some((l) => l.id === id)) await loadLeads();
    await select(id);
    window.scrollTo({ top: 0, behavior: "auto" });
    return !!current && current.id === id;
  };

  /* Historia: najpierw godzina, potem wynik i kto dzwonił, notatka pod spodem; wpisy pogrupowane dniami */
  const HIST_SHORT = 6;
  function dayLabel(iso) {
    const d = new Date(iso), now = new Date(), yest = new Date(now.getTime() - 86400000);
    if (d.toDateString() === now.toDateString()) return "Dziś";
    if (d.toDateString() === yest.toDateString()) return "Wczoraj";
    return d.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" });
  }
  const spanText = (m) => (m < 60 ? `${m} min` : m < 1440 ? `${Math.floor(m / 60)} h${m % 60 ? " " + (m % 60) + " min" : ""}` : `${Math.floor(m / 1440)} d`);
  /* Data z dniem tygodnia: „pt 31.10, 09:00” */
  const wd = (iso) => { const d = new Date(iso); return `${d.toLocaleDateString("pl-PL", { weekday: "short" }).replace(".", "")} ${d.toLocaleDateString("pl-PL", { day: "numeric", month: "numeric" })}, ${d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}`; };
  const sameMinute = (x, y) => Math.abs(new Date(x) - new Date(y)) < 60000;
  /* Co dalej po tym kontakcie: jedna wyraźna linia (nowy termin, ponowna próba albo zamknięcie leada) */
  const CLOSING = { sold: "Lead zamknięty: sprzedane", refused: "Lead zamknięty: odmowa", wrong_number: "Lead zamknięty: zły numer" };
  function nextStep(a) {
    if (CLOSING[a.outcome]) return `<div class="h-next closed">${CLOSING[a.outcome]}</div>`;
    if (!a.rescheduled_to) return "";
    const label = a.outcome === "no_answer" ? "Ponowna próba" : "Następny kontakt";
    let tag = "";
    if (a.outcome !== "no_answer" && a.planned_at) {
      tag = sameMinute(a.planned_at, a.rescheduled_to)
        ? `<span class="h-tag same">termin bez zmian</span>`
        : `<span class="h-tag">termin zmieniony (był ${esc(wd(a.planned_at))})</span>`;
    }
    return `<div class="h-next">${label}: <b>${esc(wd(a.rescheduled_to))}</b> ${tag}</div>`;
  }
  /* Czy kontakt odbył się w umówionym terminie: pokazujemy tylko, gdy różnica jest zauważalna (≥ 5 min) */
  function punctuality(a) {
    if (!a.planned_at || a.outcome === "note") return "";
    const diff = Math.round((new Date(a.created_at) - new Date(a.planned_at)) / 60000);
    if (Math.abs(diff) < 5) return "";
    return diff > 0 ? `<div class="h-punct late">Spóźniony kontakt: ${spanText(diff)} po umówionym terminie (${esc(wd(a.planned_at))})</div>` : "";   // wcześniejszy telefon nie jest problemem, więc go nie pokazujemy
  }
  /* Numer kontaktu z leadem (1 = pierwszy), liczony od najstarszego; notatki nie mają numeru */
  let histNums = new Map();
  function numberCalls(acts) {
    histNums = new Map();
    let n = 0;
    acts.slice().reverse().forEach((a) => { if (a.outcome !== "note") histNums.set(a.id, ++n); });
  }
  const HIST_FILTERS = { all: () => true, talked: (a) => a.outcome !== "note" && a.outcome !== "no_answer", noans: (a) => a.outcome === "no_answer", notes: (a) => a.outcome === "note" };
  function histStats(acts) {
    const talked = acts.filter(HIST_FILTERS.talked).length, noans = acts.filter(HIST_FILTERS.noans).length;
    const first = acts.length ? acts[acts.length - 1].created_at : null;
    return { talked, noans, calls: talked + noans, first, last: acts[0] || null };
  }
  /* Jeden wpis historii; ten sam wygląd w panelu „Ostatnie kontakty” i w pełnej historii */
  function contactRow(a, whenHtml) {
    const who = a.agent_name ? (a.agent === me.id ? "Ty" : esc(a.agent_name)) : "–";
    const num = histNums.get(a.id);
    return `<li class="h-row" style="--oc:${OUT_COLOR[a.outcome] || "#64748b"}"><div class="h-when">${whenHtml}</div>
      <div class="h-main"><div class="h-head"><span class="h-out">${esc(OUT_LABEL[a.outcome] || a.outcome)}</span><span class="who">${who}</span>${num ? `<span class="h-n" title="Który to kontakt z tym leadem">kontakt ${num}</span>` : ""}</div>
      ${a.note ? `<p class="h-note">${esc(a.note)}</p>` : ""}${nextStep(a)}${punctuality(a)}</div></li>`;
  }
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  function historyHtml(acts, all, filter = "all") {
    const list = acts.filter(HIST_FILTERS[filter] || HIST_FILTERS.all);
    if (!list.length) return `<li class="muted">${acts.length ? "Brak wpisów tego rodzaju." : "Brak historii kontaktów."}</li>`;
    const shown = all ? list : list.slice(0, HIST_SHORT);
    let html = "", lastDay = "";
    shown.forEach((a) => {
      const day = dayLabel(a.created_at);
      if (day !== lastDay) { html += `<li class="h-day" role="presentation">${esc(day)}</li>`; lastDay = day; }
      html += contactRow(a, `<b>${hhmm(a.created_at)}</b>`);
    });
    return html;
  }
  /* Krótki dzień + godzina: „dziś 22:01”, „wczoraj 14:10”, „23.10, 10:05” */
  function shortWhen(iso) {
    const d = new Date(iso), t = hhmm(iso), day = dayLabel(iso);
    return day === "Dziś" ? `dziś ${t}` : day === "Wczoraj" ? `wczoraj ${t}` : `${d.toLocaleDateString("pl-PL", { day: "numeric", month: "numeric" })}, ${t}`;
  }
  const whenCell = (iso) => { const d = dayLabel(iso), day = d === "Dziś" ? "dziś" : d === "Wczoraj" ? "wczoraj" : wd(iso).replace(/, .*/, ""); return `<b>${hhmm(iso)}</b><small>${esc(day)}</small>`; };
  const plKontakt = (n) => (n === 1 ? "kontakt" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "kontakty" : "kontaktów");
  /* Podsumowanie w trzech kafelkach: ile było kontaktów, jaki był ostatni, od kiedy trwa */
  function summaryTiles(acts) {
    const st = histStats(acts), last = st.last;
    const calls = st.calls ? `<b>${st.calls} ${plKontakt(st.calls)}</b><small>${st.talked} ${st.talked === 1 ? "rozmowa" : "rozmowy"} · ${st.noans} nieodebr.</small>` : `<b>0 kontaktów</b><small>jeszcze nie dzwoniono</small>`;
    return `<div class="h-tiles">
      <div><span>Dotychczas</span>${calls}</div>
      <div><span>Ostatni kontakt</span><b>${esc(shortWhen(last.created_at))}</b><small>${esc(OUT_LABEL[last.outcome] || last.outcome)}</small></div>
      <div><span>Pierwszy kontakt</span><b>${esc(wd(st.first).replace(/, .*/, ""))}</b><small>${esc(dayLabel(st.first) === "Dziś" ? "dziś" : spanText(Math.max(1, Math.round((Date.now() - new Date(st.first)) / 60000))) + " temu")}</small></div></div>`;
  }
  /* Panel pod terminem: podsumowanie + 3 ostatnie kontakty, bez zjeżdżania na dół strony */
  const RECENT_N = 3;
  function recentPanel(d) {
    const acts = d.activities;
    if (!acts.length) return `<section class="recent"><span class="nc-label">Ostatnie kontakty</span><p class="muted small">To pierwszy kontakt z tym leadem.</p></section>`;
    const items = acts.slice(0, RECENT_N).map((a) => contactRow(a, whenCell(a.created_at))).join("");
    return `<section class="recent"><div class="recent-head"><span class="nc-label">Ostatnie kontakty</span>${acts.length > RECENT_N ? `<button type="button" class="link-btn" id="recent-all">Pełna historia (${acts.length}) &darr;</button>` : ""}</div>
      ${summaryTiles(acts)}<ul class="history rc-list">${items}</ul></section>`;
  }

  /* Pas „jeszcze z nim nie rozmawiałeś”: widać, ile prób zostało i co się stanie po ostatniej */
  function freshNote(d) {
    if (!isFresh(d)) return "";
    const n = triesOf(d), max = maxTries();
    if (n === 0) return `<div class="fresh-note"><b>Nowy lead.</b> Nikt jeszcze z nim nie rozmawiał. Masz <b>${max}</b> ${max === 1 ? "próbę" : "próby"} dodzwonienia; jeśli nie odbierze, system sam ustawi ponowną próbę.</div>`;
    const left = max - n;
    return `<div class="fresh-note retry"><b>Jeszcze z nim nie rozmawiałeś.</b> Nie odebrał ${n} ${raz(n)} z ${max} dozwolonych prób. ${left <= 1 ? "<b>To ostatnia próba:</b> jeśli znów nie odbierze, lead wróci do wspólnej puli." : `Po ${max}. nieodebranym telefonie lead wróci do wspólnej puli.`}</div>`;
  }

  function renderCard(d, company, product) {
    const tel = esc(d.phone.replace(/[^\d+]/g, ""));
    numberCalls(d.activities);
    const hist = historyHtml(d.activities, false, "all");
    const st = histStats(d.activities);
    const next = d.next_contact_at ? new Date(d.next_contact_at) : null, due = next && next < new Date();
    const soft = isFresh(d), late = due && !soft;   // ponowna próba (nikt jeszcze nie rozmawiał) nie jest „zaległa”, tylko „do ponowienia”
    const stateText = (isDue_) => (soft ? (isDue_ ? "Czas na ponowną próbę" : "Ponowna próba") : isDue_ ? "Zaległy kontakt" : "Następny kontakt");
    const long = (iso) => new Date(iso).toLocaleString("pl-PL", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
    const lastNote = d.activities.find((a) => a.note);
    const who = lastNote ? (lastNote.agent === me.id ? "Ty" : esc(lastNote.agent_name || "–")) : "";
    const agreedLong = d.agreed_contact_at ? long(d.agreed_contact_at) : "";
    const nextBlock = `<div class="next-card${late ? " late" : ""}${soft ? " soft" : ""}"${d.next_contact_at ? ` data-iso="${esc(d.next_contact_at)}"` : ""}>
      <div class="nc-section"><span class="nc-label nc-state">${stateText(due)}</span>
        ${next ? `<div class="nc-time"><div class="nc-when">${esc(rel(d.next_contact_at))}</div><div class="nc-date">${esc(long(d.next_contact_at))}</div></div>
          ${d.missed_calls > 0 && agreedLong ? `<div class="nc-missed">Umówiony kontakt (${esc(agreedLong)}) nie doszedł do skutku: nie odebrał ${d.missed_calls}×. Ponowna próba jest ustawiona wyżej.</div>` : ""}`
          : `<div class="nc-date">Nie zaplanowano</div>`}</div>
      ${lastNote ? `<div class="nc-section nc-last"><span class="nc-label">Ostatni komentarz · ${who} · ${fmt(lastNote.created_at)}</span><div class="nc-text">${esc(lastNote.note)}</div></div>` : ""}
    </div>`;
    const caseBlock = d.next_contact_note ? `<div class="nc-why case-top"><span class="nc-why-ic">${ICON_NOTE}</span><div><span class="nc-why-l">Sprawa do omówienia</span><span class="nc-why-t">${esc(d.next_contact_note)}</span></div></div>` : "";
    const mv = (d.assignments || [])[0];
    const transfer = d.transferred_from && mv && mv.to_agent === d.owner
      ? `<div class="transfer-note">
        <strong>↪ Przepisany od ${esc(d.transferred_from)}</strong>
        <span>Kiedy: ${fmt(mv.assigned_at)}</span>
        ${mv.reason ? `<span>Powód: ${esc(mv.reason)}</span>` : ""}
        <small>Wcześniejsze rozmowy widzisz w historii kontaktów na dole.</small></div>` : "";
    const banner = savedNote ? `<div class="saved-banner" role="status"><span class="sb-ic">✓</span><div>${savedNote}</div></div>` : "";
    savedNote = "";
    $("lead-pane").innerHTML = `
      ${banner}
      ${matchBanner(d.id)}
      ${transfer}
      <div class="card">
        <div class="lead-head"><div><h2 class="lead-name">${esc(d.contact_name)}</h2>
          <p class="muted">${company ? esc(company.name) : "Brak firmy"}${product ? " · " + esc(product.name) : ""}</p></div>${statusChip(d)}</div>
        ${caseBlock}
        <div class="facts facts-top">
          <div class="f-phone"><span>Telefon</span><div class="phone-row"><a class="big-phone" href="tel:${tel}">${esc(d.phone)}</a>${CRM.copyButton(d.phone)}</div></div>
          <div class="f-mail"><span>E-mail</span>${d.email ? `<div class="phone-row"><a href="mailto:${esc(d.email)}">${esc(d.email)}</a>${CRM.copyButton(d.email, "e-mail")}</div>` : "–"}</div>
          <div><span>NIP / KRS</span>${company ? esc(company.nip || "–") + " / " + esc(company.krs || "–") : "–"}</div>
          <div class="f-tries" title="Liczba prób kontaktu z tym leadem"><span>Próby</span>${d.attempts_count}</div>
        </div>
        ${freshNote(d)}
        ${nextBlock}${recentPanel(d)}

      </div>
      <form class="card" id="act-form" novalidate>
        <h2>Jak poszła rozmowa?</h2>
        <div class="outcomes" role="radiogroup">${OUTCOMES.map(([v, l], i) =>
          `<button type="button" role="radio" data-outcome="${v}" aria-checked="false"><kbd>${i + 1}</kbd>${l}</button>`).join("")}</div>
        <p class="hint" id="hint">Wybierz wynik (klawisze 1–6). Pomyłka? Kliknij ten sam przycisk albo Esc, żeby zacząć od nowa.</p>
        <div id="cb-fields" hidden>
          <div class="field"><label for="cb-at" id="cb-label">Kiedy oddzwonić</label>
            <div class="when-row"><input id="cb-at" autocomplete="off" placeholder="np. jutro 9:30">
              <span class="cal-wrap"><button type="button" class="icon-btn" id="cb-pick" title="Wybierz z kalendarza" aria-label="Wybierz z kalendarza"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/></svg></button><input type="datetime-local" id="cb-native" tabindex="-1" aria-hidden="true"></span></div>
            <small class="hint" id="cb-preview"></small></div>
          <div class="presets">${PRESETS.map(([l], i) => `<button type="button" data-preset="${i}" aria-pressed="false">${l}</button>`).join("")}</div>
          <label><span id="cb-why-label">W jakiej sprawie</span><input id="cb-note" maxlength="255" placeholder="np. wróci z urlopu, chce wycenę"></label>
        </div>
        ${CRM.phraseButtons()}
        <label>Komentarz (opcjonalnie)<textarea id="act-note" rows="2" placeholder="Krótka notatka z rozmowy"></textarea></label>
        <p class="form-error" id="act-error" hidden></p>
        <div class="actions">
          <div class="act-main">
            <button class="btn btn-primary" type="submit" data-next="1" disabled>Zapisz i weź kolejnego</button>
            <button class="btn btn-secondary" type="submit" disabled>Tylko zapisz</button>
          </div>
          <div class="act-links">
            <button class="link-btn" type="button" id="note-only">Dodaj samą notatkę</button>
            <button class="link-btn" type="button" id="reset-btn">Zacznij od nowa (Esc)</button>
          </div>
          <span class="enter-hint">↵ Enter zapisuje i bierze kolejnego</span>
        </div>
      </form>
      <div class="card" id="hist-card"><h2>Historia kontaktów${d.activities.length ? ` <span class="muted small">(${d.activities.length})</span>` : ""}</h2>
        ${d.activities.length ? `<div class="h-filters" role="group" aria-label="Filtr historii">${[["all", "Wszystko", d.activities.length], ["talked", "Rozmowy", st.talked], ["noans", "Nieodebrane", st.noans], ["notes", "Notatki", d.activities.filter(HIST_FILTERS.notes).length]]
          .filter(([k, , n]) => k === "all" || n > 0).map(([k, l, n]) => `<button type="button" data-hf="${k}" aria-pressed="${k === "all"}">${l} <small>${n}</small></button>`).join("")}</div>` : ""}
        <ul class="history" id="hist">${hist}</ul>
        ${d.activities.length > HIST_SHORT ? `<button type="button" class="btn-mini h-more" id="hist-more">Pokaż całą historię (${d.activities.length})</button>` : ""}</div>`;
    window.scrollTo({ top: 0, behavior: "auto" });   // nowy lead zawsze od góry strony (pasek „Zapisano”, termin, telefon)
    let histFilter = "all", histAll = false;
    const paintHistory = () => {
      $("hist").innerHTML = historyHtml(d.activities, histAll, histFilter);
      const more = $("hist-more");
      if (more) more.hidden = histAll || d.activities.filter(HIST_FILTERS[histFilter]).length <= HIST_SHORT;
    };
    const more = $("hist-more");
    if (more) more.addEventListener("click", () => { histAll = true; paintHistory(); });
    document.querySelectorAll("#hist-card [data-hf]").forEach((b) => b.addEventListener("click", () => {
      histFilter = b.dataset.hf;
      document.querySelectorAll("#hist-card [data-hf]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      paintHistory();
    }));
    const jump = $("recent-all");
    if (jump) jump.addEventListener("click", () => { histAll = true; paintHistory(); $("hist-card").scrollIntoView({ behavior: "smooth", block: "start" }); });
    wireForm(d.id);
    CRM.wirePhrases($("act-form"), $("act-note"));
    const eh = document.querySelector("#act-form .enter-hint");
    if (eh) {
      const od = otherOverdue(d.id);
      eh.textContent = od.length ? `↵ Enter zapisuje i otwiera: ${od[0].contact_name} (${isFresh(od[0]) ? "ponowna próba" : "zaległy"})` : "↵ Enter zapisuje i pobiera nowego leada z puli";
    }
  }

  async function postActivity(body) {
    return api("/api/activities/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  /* Pasek „Cofnij” po zapisie: 10 s na pomyłkę (serwer przyjmuje cofnięcie do 30 s, tylko dla ostatniego wpisu leada) */
  const UNDO_SECONDS = 10;
  let undoTimer = null;
  function hideUndo() { clearInterval(undoTimer); const el = $("undo-bar"); if (el) el.remove(); }
  function showUndo(saved, leadId, html) {
    hideUndo();
    if (!saved || !saved.id) return;
    const bar = document.createElement("div");
    bar.id = "undo-bar"; bar.className = "undo-bar"; bar.setAttribute("role", "status");
    bar.innerHTML = `<span class="ub-text">${html}</span><button type="button" class="ub-btn" title="Cofnij zapis (Ctrl+Z)">Cofnij <span class="ub-sec">${UNDO_SECONDS}</span></button><span class="ub-time" aria-hidden="true"></span>`;
    document.body.appendChild(bar);
    bar.querySelector(".ub-time").style.animationDuration = UNDO_SECONDS + "s";
    let left = UNDO_SECONDS;
    undoTimer = setInterval(() => {
      left -= 1;
      const sec = bar.querySelector(".ub-sec");
      if (sec) sec.textContent = String(Math.max(left, 0));
      if (left <= 0) hideUndo();
    }, 1000);
    const btn = bar.querySelector(".ub-btn");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      let r;
      try { r = await api(`/api/activities/${saved.id}/undo/`, { method: "POST" }); }
      catch { toast("Brak połączenia z serwerem. Nie cofnięto.", true); btn.disabled = false; return; }
      if (!r.ok) {
        const msg = (await r.json().catch(() => ({}))).detail || "Nie można cofnąć tego zapisu.";
        toast(msg, true); hideUndo(); return;
      }
      hideUndo();
      toast("Zapis cofnięty.");
      clearSearch();
      await loadLeads();
      refreshReminders();
      await CRM.openLead(leadId);
    });
  }
  /* Ctrl+Z cofa ostatni zapis, dopóki pasek jest widoczny (nie w polach tekstowych, żeby nie psuć ich własnego cofania) */
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z" || e.shiftKey) return;
    const t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
    const btn = document.querySelector("#undo-bar .ub-btn");
    if (typing || !btn || btn.disabled) return;
    e.preventDefault(); btn.click();
  });

  async function afterSave(leadId, goNext, saved) {
    clearSearch();   // po zapisie wracasz do zwykłej kolejki (dane z wyszukiwania byłyby nieaktualne)
    const name = current && current.contact_name ? current.contact_name : "";
    const out = saved && saved.outcome ? OUT_LABEL[saved.outcome] || "" : "";
    const again = saved && saved.rescheduled_to ? ` Następny kontakt: <b>${esc(fmt(saved.rescheduled_to))}</b>.` : "";
    savedNote = `<b>Zapisano:</b> ${esc(out)}${name ? " · " + esc(name) : ""}.${again}`;
    showUndo(saved, leadId, `<b>Zapisano:</b> ${esc(out)}${name ? " · " + esc(name) : ""}`);
    await loadLeads();
    refreshReminders();
    if (saved && saved.outcome === "no_answer" && !leads.some((l) => l.id === leadId)) {
      savedNote += " Limit prób wyczerpany: lead wrócił do wspólnej puli.";
    }
    if (goNext) {
      const od = otherOverdue(leadId);
      if (od.length) {   // najpierw zaległe, bo przy zaległych backend i tak nie wyda nowego leada (409)
        savedNote += ` Czeka jeszcze ${od.length} ${plOverdue(od.length)}, otwieram najstarszy.`;
        select(od[0].id);
        return;
      }
      if (await claim()) return;
    }
    if (leads.some((l) => l.id === leadId)) select(leadId);
    else if (leads.length) select(leads[0].id);
    else { savedNote = ""; emptyPane(); }
  }

  function wireForm(leadId) {
    const form = $("act-form"), err = $("act-error");
    const submits = () => form.querySelectorAll("[type=submit]");
    const fail = (m) => { err.textContent = m; err.hidden = false; };
    const actions = form.querySelector(".actions"), primary = form.querySelector("[data-next]");
    const setArmed = (on) => actions.classList.toggle("armed", on);
    /* Zacznij od nowa: czyści wynik, terminy i komentarze (kliknięcie tego samego wyniku albo Esc). */
    function reset() {
      outcome = null; err.hidden = true;
      form.querySelectorAll("[data-outcome]").forEach((x) => x.setAttribute("aria-checked", "false"));
      form.querySelectorAll("[data-preset]").forEach((x) => x.setAttribute("aria-pressed", "false"));
      $("cb-fields").hidden = true;
      ["cb-at", "cb-note", "act-note"].forEach((id) => { $(id).value = ""; });
      updatePreview();
      $("hint").textContent = "Wybierz wynik (klawisze 1–6). Pomyłka? Kliknij ten sam przycisk albo Esc, żeby zacząć od nowa.";
      submits().forEach((s) => { s.disabled = true; });
      setArmed(false);
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    }
    resetForm = reset;
    $("reset-btn").addEventListener("click", reset);
    /* Podświetlenie: przycisk „Zapisz i weź kolejnego” + podpowiedź, gdy Enter właśnie go uruchomi. */
    const hintEl = form.querySelector(".enter-hint");
    [$("cb-note"), $("act-note"), primary].forEach((f) => {
      f.addEventListener("focus", () => {
        if (!outcome) return;
        hintEl.textContent = f === $("act-note") ? "Ctrl+Enter zapisuje i bierze kolejnego (Enter = nowa linia)" : "↵ Enter zapisuje i bierze kolejnego";
        setArmed(true);
      });
      f.addEventListener("blur", () => setArmed(false));
    });
    /* Wybór terminu z kalendarza (natywny picker przeglądarki) */
    const nat = $("cb-native");
    $("cb-pick").addEventListener("click", () => {
      nat.min = fmtNative(new Date());
      nat.value = fmtNative(parseWhen($("cb-at").value) || atNine(1));
      try { nat.showPicker(); } catch { nat.focus(); nat.click(); }
    });
    const applyPicked = () => {
      if (!nat.value) return;
      $("cb-at").value = fmtInput(new Date(nat.value));
      form.querySelectorAll("[data-preset]").forEach((x) => x.setAttribute("aria-pressed", "false"));
      updatePreview();
    };
    nat.addEventListener("input", applyPicked);                       // data wpisuje się od razu przy każdym kliknięciu w kalendarzu
    nat.addEventListener("change", () => { applyPicked(); $("cb-note").focus(); });
    form.querySelectorAll("[data-outcome]").forEach((b) => b.addEventListener("click", () => {
      if (outcome === b.dataset.outcome) { reset(); return; }
      outcome = b.dataset.outcome; err.hidden = true;
      form.querySelectorAll("[data-outcome]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      const fresh = isFresh(current), lastTry = outcome === "no_answer" && isLastTry(current);
      const sched = SCHEDULING.includes(outcome) && !lastTry, required = REQUIRED.includes(outcome);
      autoRetry = false;
      $("cb-fields").hidden = !sched;
      let hintText = OUTCOMES.find((o) => o[0] === outcome)[2];
      if (sched) {
        const missed = outcome === "no_answer" && isMissable(current);
        const autoTerm = outcome === "no_answer" && fresh && !missed && current.suggested_retry_at ? new Date(current.suggested_retry_at) : null;
        $("cb-label").textContent = { callback: "Kiedy oddzwonić", interested: "Kiedy ponownie zadzwonić", no_answer: missed ? "Ponowna próba (nie odebrał w terminie)" : autoTerm ? "Ponowna próba (ustawiona automatycznie, możesz zmienić)" : "Ponowna próba (opcjonalnie)" }[outcome];
        $("cb-why-label").textContent = "W jakiej sprawie (opcjonalnie)";
        $("cb-note").value = (current && current.next_contact_note) || "";   // podpowiedź: ostatnio wpisana sprawa
        $("cb-at").value = required ? fmtInput(atNine(1)) : missed ? fmtInput(new Date(Date.now() + 3600e3)) : autoTerm ? fmtInput(autoTerm) : "";
        autoRetry = !!autoTerm;
        updatePreview();
        form.querySelectorAll("[data-preset]").forEach((p) => p.setAttribute("aria-pressed", "false"));
        if (missed) hintText = "Umówiony kontakt nie doszedł do skutku. Lead zostaje u Ciebie, a ponowna próba trafi do planu dnia na wskazany termin (domyślnie za godzinę).";
        else if (autoTerm) hintText = `Próba ${triesOf(current) + 1} z ${maxTries()} bez odpowiedzi. Ponowną próbę ustawiłem na ${longDate(autoTerm)}. Zmień termin, jeśli wolisz inny, albo od razu zapisz.`;
      } else if (lastTry) {
        hintText = `To ostatnia próba (${triesOf(current) + 1} z ${maxTries()}). Po zapisie lead wróci do wspólnej puli i zniknie z Twojej listy.`;
      }
      $("hint").textContent = hintText;
      submits().forEach((s) => { s.disabled = false; });
      /* Kursor od razu ląduje tam, gdzie agent ma coś wpisać lub zatwierdzić (Enter). */
      const focusOn = { callback: "cb-at", interested: "cb-at", refused: "act-note" }[outcome];
      (focusOn ? $(focusOn) : form.querySelector("[data-next]")).focus();
      if (focusOn === "cb-at") $("cb-at").select();   // od razu można nadpisać domyślny termin
    }));
    const clearPresets = () => form.querySelectorAll("[data-preset]").forEach((p) => p.setAttribute("aria-pressed", "false"));
    $("cb-at").addEventListener("input", () => { autoRetry = false; clearPresets(); updatePreview(); });
    $("cb-at").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("cb-note").focus(); } });
    form.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
      const was = b.getAttribute("aria-pressed") === "true";
      autoRetry = false;
      clearPresets();
      if (was && !REQUIRED.includes(outcome)) { $("cb-at").value = ""; updatePreview(); return; }  /* drugie kliknięcie czyści termin */
      b.setAttribute("aria-pressed", "true");
      $("cb-at").value = fmtInput(PRESETS[Number(b.dataset.preset)][1]());
      updatePreview();
    }));
    $("note-only").addEventListener("click", async () => {
      const note = $("act-note").value.trim();
      if (!note) { fail("Wpisz treść notatki."); return; }
      let r;
      try { r = await postActivity({ lead: leadId, outcome: "note", note }); } catch { fail("Brak połączenia z serwerem. Spróbuj ponownie."); return; }
      if (!r.ok) { fail(errText(await r.json().catch(() => ({})))); return; }
      const saved = await r.json().catch(() => ({}));
      try { await afterSave(leadId, false, saved); } catch (ex) { console.error(ex); toast("Zapisano, ale nie udało się odświeżyć widoku. Odśwież stronę (F5).", true); }
    });
    let saving = false;
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); err.hidden = true;
      if (!outcome) { fail("Wybierz wynik rozmowy."); return; }
      const goNext = e.submitter && e.submitter.dataset.next === "1";
      const body = { lead: leadId, outcome, note: $("act-note").value.trim() };
      if (SCHEDULING.includes(outcome) && !$("cb-fields").hidden && !(autoRetry && outcome === "no_answer")) {
        // (przy nietkniętej automatycznej propozycji nie wysyłamy terminu: serwer policzy go od chwili zapisu)
        const raw = $("cb-at").value.trim(), why = $("cb-note").value.trim();
        const when = raw ? parseWhen(raw) : null;
        if (raw && !when) { fail("Nie rozumiem terminu. Spróbuj np. „jutro 9:30” albo „25.10 10:23”."); return; }
        if (REQUIRED.includes(outcome) && !when) { fail("Podaj termin ponownego kontaktu."); return; }
        if (when && when < new Date()) { fail("Ten termin już minął."); return; }
        if (when) { body.next_contact_at = when.toISOString(); body.next_contact_note = why; }
      }
      if (saving) return;   // podwójne kliknięcie / Enter nie zapisze drugi raz
      saving = true;
      submits().forEach((s) => { s.disabled = true; });
      const unlock = () => { saving = false; submits().forEach((s) => { s.disabled = false; }); };
      let r;
      try { r = await postActivity(body); } catch { fail("Brak połączenia z serwerem. Spróbuj ponownie."); unlock(); return; }
      if (!r.ok) { fail(errText(await r.json().catch(() => ({})))); unlock(); return; }
      const saved = await r.json().catch(() => ({}));
      try { await afterSave(leadId, goNext, saved); }
      catch (ex) { console.error(ex); toast("Zapisano, ale nie udało się odświeżyć widoku. Odśwież stronę (F5).", true); }
      finally { saving = false; }
    });
  }

  async function claim() {
    clearSearch();
    const btn = $("claim-btn"); btn.disabled = true;
    try {
      const r = await api("/api/leads/next/", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (r.status === 409) { toast(`Masz zaległe oddzwonienia (${d.overdue_count}). Zrób je najpierw – są na górze listy.`, true); return false; }
      if (r.status === 404) { toast("Pula jest pusta – brak leadów do pobrania.", true); return false; }
      if (!r.ok) { toast(d.detail || "Nie udało się pobrać leada.", true); return false; }
      await loadLeads(); select(d.id);
      refreshReminders();
      return true;
    } catch (ex) {
      console.error(ex); toast("Brak połączenia z serwerem. Spróbuj ponownie.", true); return false;
    } finally { btn.disabled = false; }
  }

  api("/api/settings/").then((r) => (r.ok ? r.json() : null)).then((d) => { if (d) { rules = d; renderList(); } }).catch(() => {});
  $("claim-btn").addEventListener("click", claim);
  $("lead-pane").addEventListener("click", (e) => { if (e.target.closest("#banner-clear")) clearSearch(); });
  $("lead-list").addEventListener("click", (e) => {
    if (e.target.closest("#found-clear")) { clearSearch(); return; }
    const item = e.target.closest("[data-id]");
    if (item) select(Number(item.dataset.id));
  });
  /* Skróty: 1–6 wybiera wynik, Ctrl+Enter zapisuje i bierze kolejnego. */
  document.addEventListener("keydown", (e) => {
    const form = $("act-form");
    if (!form) return;
    if (e.key === "Escape" && resetForm) { resetForm(); return; }
    if (e.key === "Enter" && e.ctrlKey) { const b = form.querySelector("[data-next]"); if (b && !b.disabled) b.click(); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && e.target === document.body) {
      const b = form.querySelector("[data-next]");
      if (b && !b.disabled) { e.preventDefault(); b.click(); }
      return;
    }
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.ctrlKey || e.metaKey || e.altKey) return;
    const o = OUTCOMES[Number(e.key) - 1];
    if (o) { e.preventDefault(); form.querySelector(`[data-outcome="${o[0]}"]`).click(); }   // preventDefault: cyfra nie wpisze się w pole, na które przeskoczy kursor
  });
  /* Żywy licznik: co sekundę (jak karty przypomnień, żeby oba miejsca zmieniały się w tej samej chwili) odświeża „za X min” na liście i w karcie, a przy zmianie pilności przesuwa leada do innej grupy */
  setInterval(() => {
    if (document.hidden) return;
    renderList();
    const nc = document.querySelector(".next-card[data-iso]");
    if (!nc) return;
    const iso = nc.dataset.iso, due = new Date(iso) < new Date(), soft = nc.classList.contains("soft");
    nc.classList.toggle("late", due && !soft);
    const state = nc.querySelector(".nc-state"), when = nc.querySelector(".nc-when");
    if (state) state.textContent = soft ? (due ? "Czas na ponowną próbę" : "Ponowna próba") : due ? "Zaległy kontakt" : "Następny kontakt";
    if (when) when.textContent = rel(iso);
  }, 1000);
  setInterval(() => { if (!document.hidden) loadSummary(); }, 30000);   // pasek dnia: liczby „zaległe / do ponowienia” zmieniają się z upływem czasu
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { renderList(); } });
  /* /crm/queue/?lead=ID (link z planu dnia) otwiera od razu wskazanego leada */
  const wanted = Number(new URLSearchParams(location.search).get("lead"));
  loadLeads().then(async () => {
    const find = new URLSearchParams(location.search).get("find");   // z wyszukiwarki na innej stronie
    if (find) {
      if ($("gsearch-input")) $("gsearch-input").value = find;
      await doSearch(find);
      if (found && found.length) return;
    }
    if (wanted && !leads.some((l) => l.id === wanted)) {   // np. z wyszukiwarki: lead zamknięty albo spoza kolejki
      await select(wanted);
      if (current) return;
    }
    const first = leads.find((l) => l.id === wanted) || leads[0];
    return first ? select(first.id) : emptyPane();
  }).catch(() => {
    $("lead-list").innerHTML = '<p class="form-error">Nie udało się wczytać leadów. Odśwież stronę (Ctrl+F5).</p>';
  });
})();

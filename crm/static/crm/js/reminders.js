/* Powiadomienia o oddzwonieniach.
   Co 30 s pyta GET /api/my-leads/due/ o leady z terminem w najbliższym oknie (zaległe + nadchodzące),
   a co sekundę sprawdza lokalnie, czy któryś termin wszedł w czas „powiadom mnie przed terminem”:
   - pokazuje wyraźną kartę przypomnienia (raz na dany termin leada) z licznikiem na żywo,
   - ustawia czerwony licznik przy „Plan dnia” i w tytule karty,
   - opcjonalnie gra krótki sygnał i wysyła powiadomienie systemowe (przez service worker, żeby kliknięcie
     zawsze otwierało właściwego leada).
   Ustawienia (włączone, czas przed terminem, dźwięk) są zapamiętane per użytkownik w tej przeglądarce.
   CRM.refreshReminders() odświeża wszystko od razu (np. po zapisie rozmowy w kolejce). */
CRM.initReminders = function initReminders() {
  const { api, esc, $, currentUser } = CRM;
  const user = currentUser();
  if (!user || user.role === "none" || !$("reminders")) return;

  const POLL_MS = 30000;
  const MAX_CARDS = 3;
  const SEEN_KEY = "crm_rem_seen_" + user.username;
  const BROWSER_KEY = "crm_rem_browser_" + user.username;
  const CFG_KEY = "crm_rem_cfg_" + user.username;
  const DEFAULTS = { enabled: true, lead: 300, sound: true, dayBar: true };   // lead = sekundy przed terminem
  const baseTitle = document.title;
  const box = $("reminders");
  box.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest('a[href^="/crm/queue/?lead="]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;   // Ctrl/Cmd+klik nadal otwiera nową kartę
    if (!(location.pathname.startsWith("/crm/queue") && CRM.openLead)) return;   // poza kolejką zwykłe przejście
    e.preventDefault();
    goTo(a.getAttribute("href"));
    const card = a.closest(".reminder");
    if (card) closeCard(card);
  });
  let memSeen = {}, memBrowser = null, memCfg = null;   // zapas, gdy localStorage jest niedostępny
  let cache = [];   // ostatnio pobrane leady z terminem (zaległe + w oknie)

  /* ---------- ustawienia ---------- */
  function cfg() {
    let raw = memCfg;
    try { raw = JSON.parse(localStorage.getItem(CFG_KEY) || "null") || memCfg; } catch { /* użyj zapasu */ }
    const c = { ...DEFAULTS, ...(raw || {}) };
    c.lead = Math.min(7200, Math.max(0, Math.round(Number(c.lead)) || 0));
    return c;
  }
  function saveCfg(patch) {
    memCfg = { ...cfg(), ...patch };
    try { localStorage.setItem(CFG_KEY, JSON.stringify(memCfg)); } catch { /* brak dostępu */ }
  }

  /* Pasek dnia nad kolejką: domyślnie widoczny, agent może go schować w oknie „Powiadomienia i widok” */
  CRM.dayBarEnabled = () => cfg().dayBar !== false;

  /* ---------- zapamiętywanie, o czym już przypomniano ---------- */
  function readSeen() {
    try { return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}"); } catch { return memSeen; }
  }
  function writeSeen(map) {
    const cutoff = Date.now() - 2 * 24 * 3600 * 1000;   // stare wpisy usuwamy
    Object.keys(map).forEach((k) => { if (map[k] < cutoff) delete map[k]; });
    memSeen = map;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(map)); } catch { /* brak dostępu */ }
  }
  const keyOf = (l) => l.id + "@" + l.next_contact_at;   // zmiana terminu = nowe przypomnienie
  const dueMs = (l) => new Date(l.next_contact_at).getTime();

  /* ---------- dźwięk ---------- */
  let audio = null;
  function beep() {
    if (!cfg().sound) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume();
      [[880, 0], [1175, 0.16]].forEach(([freq, at]) => {
        const o = audio.createOscillator(), g = audio.createGain(), t = audio.currentTime + at;
        o.type = "sine"; o.frequency.value = freq;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.28);
        o.connect(g); g.connect(audio.destination);
        o.start(t); o.stop(t + 0.3);
      });
    } catch { /* przeglądarka blokuje dźwięk do pierwszego kliknięcia na stronie */ }
  }

  /* ---------- powiadomienia przeglądarki (systemowe) ---------- */
  const supported = "Notification" in window;
  let swReg = null;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (e) => { if (e.data && e.data.type === "open-url") goTo(e.data.url); });
    navigator.serviceWorker.register("/crm/sw.js", { scope: "/crm/" })
      .then((reg) => { swReg = reg; }).catch(() => { /* bez SW działa zwykłe Notification */ });
  }
  function browserOn() {
    let v = memBrowser;
    try { v = localStorage.getItem(BROWSER_KEY); } catch { /* użyj zapasu */ }
    return v === "1" && supported && Notification.permission === "granted";
  }
  function setBrowser(on) {
    memBrowser = on ? "1" : "0";
    try { localStorage.setItem(BROWSER_KEY, memBrowser); } catch { /* brak dostępu */ }
  }
  function say(text, bad) {
    const m = $("notif-msg");
    if (!m) return;
    m.textContent = text; m.className = "avatar-msg" + (bad ? " bad" : ""); m.hidden = !text;
  }
  function paintBrowserButton() {
    const b = $("notif-btn");
    if (!b) return;
    let label = "wyłączone";
    if (!supported) label = "niedostępne";
    else if (Notification.permission === "denied") label = "zablokowane w przeglądarce";
    else if (browserOn()) label = "włączone";
    b.textContent = "Powiadomienia przeglądarki: " + label;
    b.disabled = !supported;
    const t = $("notif-test");
    if (t) t.hidden = !browserOn();
  }
  /* tag jest unikalny (wspólny tag powodował ciche podmienianie kolejnych powiadomień);
     requireInteraction zostawia powiadomienie na ekranie, dopóki go nie klikniesz lub nie zamkniesz */
  /* Przejście do leada: na stronie kolejki otwieramy go na miejscu (bez przeładowania strony), gdzie indziej zwykła nawigacja */
  function goTo(url) {
    const m = /[?&]lead=(\d+)/.exec(url || "");
    if (m && location.pathname.startsWith("/crm/queue") && CRM.openLead) {
      const id = Number(m[1]);
      CRM.openLead(id).then((ok) => { if (ok) history.replaceState(null, "", "/crm/queue/?lead=" + id); else location.href = url; });
      return;
    }
    if (url) location.href = url;
  }
  async function browserNotify(title, body, url, tag, sticky = true) {
    if (!browserOn()) return false;
    const opts = { body, tag: tag || "leadcrm-" + Date.now(), renotify: true, requireInteraction: sticky, data: { url } };
    try {
      if (swReg && swReg.showNotification) { await swReg.showNotification(title, opts); return true; }
    } catch { /* spróbuj zwykłego Notification */ }
    try {
      const n = new Notification(title, opts);
      n.onclick = () => { window.focus(); goTo(url); n.close(); };
      return true;
    } catch { return false; }
  }
  async function toggleBrowser() {
    if (!supported) { say("Ta przeglądarka nie obsługuje powiadomień.", true); return; }
    if (browserOn()) { setBrowser(false); paintBrowserButton(); say("Powiadomienia przeglądarki wyłączone."); return; }
    if (Notification.permission === "denied") {
      say("Zablokowane. Kliknij kłódkę obok adresu strony → Powiadomienia → Zezwalaj, potem odśwież stronę (F5).", true);
      paintBrowserButton();
      return;
    }
    if (Notification.permission === "default") await Notification.requestPermission();
    if (Notification.permission === "granted") {
      setBrowser(true);
      paintBrowserButton();
      sendTest();
    } else {
      say("Nie zezwolono na powiadomienia. Możesz to zmienić pod kłódką obok adresu strony.", true);
      paintBrowserButton();
    }
  }
  async function sendTest() {
    const ok = await browserNotify("LeadCRM – test", "Tak będą wyglądać przypomnienia o oddzwonieniach. Kliknij, żeby wrócić do aplikacji.", "/crm/plan/", "leadcrm-test", false);
    say(ok
      ? "Włączone. Wysłałem powiadomienie testowe. Jeśli go nie widzisz, sprawdź w Windows: Ustawienia → System → Powiadomienia (Chrome musi być włączony) oraz tryb „Nie przeszkadzać”."
      : "Nie udało się wysłać powiadomienia testowego.", !ok);
  }

  /* ---------- teksty ---------- */
  const hhmm = (v) => new Date(v).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  const dm = (v) => new Date(v).toLocaleDateString("pl-PL", { day: "numeric", month: "numeric" });
  function minutesText(min) {
    if (min < 60) return min + " min";
    const h = Math.floor(min / 60), r = min % 60;
    return h < 24 ? h + " h" + (r ? " " + r + " min" : "") : Math.floor(h / 24) + " d";
  }
  /* „Za 4 min 12 s” (do 10 min dokładnie co do sekundy) / „Za 35 min” / „Spóźnione o 14 min” */
  function relText(iso, now) {
    const diff = new Date(iso).getTime() - now;
    /* minuty liczymy po granicach minut zegara (nie od sekundy terminu), żeby wszystkie karty zmieniały się w tej samej chwili */
    const minOf = (t) => Math.floor(t / 60000);
    if (diff < 0) {
      const late = minOf(now) - minOf(new Date(iso).getTime());
      return late < 1 ? "Termin właśnie minął" : "Spóźnione o " + minutesText(late);
    }
    const sec = Math.ceil(diff / 1000);
    if (sec <= 1) return "Za chwilę";
    if (sec < 600) {
      const m = Math.floor(sec / 60), s = sec % 60;
      return "Za " + (m ? m + " min " : "") + s + " s";
    }
    return "Za " + minutesText(Math.max(1, minOf(new Date(iso).getTime()) - minOf(now)));
  }
  const who = (l) => l.contact_name + (l.company_name ? " · " + l.company_name : "");
  /* Kolor karty = kolor statusu leada (Zainteresowany zielony, Do oddzwonienia bursztynowy); zaległe zawsze czerwone */
  const statusInfo = (code) => (CRM.STATUS && CRM.STATUS[code]) || null;
  function inkFor(hex) {
    const n = parseInt(String(hex).replace("#", ""), 16);
    const lum = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
    return lum > 0.45 ? "#10151f" : "#ffffff";
  }

  /* ---------- karty na ekranie ---------- */
  const ICON_PHONE = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></svg>';
  const ICON_ALERT = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';

  function closeCard(el) {
    if (el.classList.contains("leaving")) return;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 180);
  }
  function paintCard(el, now) {
    const iso = el.dataset.iso;
    if (!iso) return;
    const soft = el.dataset.soft === "1";   // ponowna próba (lead bez rozmowy) nie jest „zaległa” (czerwona)
    const late = new Date(iso).getTime() < now && !soft;
    const rel = el.querySelector(".rem-rel");
    const text = relText(iso, now);
    if (rel && rel.textContent !== text) rel.textContent = text;
    const state = late ? "late" : "soon";
    if (el.dataset.state === state) return;
    el.dataset.state = state;
    el.classList.toggle("late", late);
    if (late || !el.dataset.color) { el.style.removeProperty("--rc"); el.style.removeProperty("--rci"); }
    else { el.style.setProperty("--rc", el.dataset.color); el.style.setProperty("--rci", inkFor(el.dataset.color)); }
    el.querySelector(".rem-kind").textContent = soft ? (new Date(iso).getTime() < now ? "Czas na ponowną próbę" : "Zaraz ponowna próba") : late ? "Zaległe oddzwonienie" : "Zaraz oddzwonienie";
    el.querySelector(".rem-icon").innerHTML = late ? ICON_ALERT : ICON_PHONE;
  }
  function leadCard(l, now) {
    const tel = String(l.phone || "").replace(/[^\d+]/g, "");
    const missed = l.missed_calls > 0 && l.agreed_contact_at
      ? `<div class="rem-missed">Nie odebrał w umówionym terminie (${dm(l.agreed_contact_at)}, ${hhmm(l.agreed_contact_at)})${l.missed_calls > 1 ? " · " + l.missed_calls + "×" : ""}</div>` : "";
    const el = document.createElement("div");
    el.className = "reminder";
    el.dataset.lead = l.id;
    el.dataset.iso = l.next_contact_at;
    if (l.status === "new") el.dataset.soft = "1";
    const st = statusInfo(l.status);
    if (st) el.dataset.color = st[1];
    el.setAttribute("role", "alert");
    el.innerHTML = `
      <div class="rem-bar"><span class="rem-icon"></span><span class="rem-kind"></span>
        <button type="button" class="rem-x" aria-label="Zamknij">&times;</button></div>
      <div class="rem-body">
        <div class="rem-name">${esc(l.contact_name)}${st ? ` <span class="chip" style="--c:${st[1]}">${esc(st[0])}</span>` : ""}</div>
        ${l.company_name ? `<div class="rem-company">${esc(l.company_name)}</div>` : ""}
        ${l.next_contact_note ? `<div class="rem-note"><div class="rem-label">Sprawa do omówienia</div><div class="rem-note-text">${esc(l.next_contact_note)}</div></div>` : ""}
        <div class="rem-when">
          <div class="rem-at"><div class="rem-label">Oddzwoń o</div><div class="rem-hour">${hhmm(l.next_contact_at)}</div></div>
          <div class="rem-left"><div class="rem-label">Zostało</div><div class="rem-rel"></div></div>
        </div>
        ${missed}
        <div class="rem-actions">
          <a class="btn btn-primary" href="/crm/queue/?lead=${l.id}">Otwórz leada</a>
          ${tel ? `<a class="btn btn-secondary" href="tel:${esc(tel)}">Zadzwoń</a>` : ""}
        </div>
      </div>`;
    el.querySelector(".rem-x").addEventListener("click", () => closeCard(el));
    paintCard(el, now);
    return el;
  }
  function summaryCard(count, late) {
    const el = document.createElement("div");
    el.className = "reminder" + (late ? " late" : "");
    el.dataset.lead = "";
    el.setAttribute("role", "alert");
    el.innerHTML = `
      <div class="rem-bar"><span class="rem-icon">${late ? ICON_ALERT : ICON_PHONE}</span><span class="rem-kind">Masz oddzwonienia do zrobienia</span>
        <button type="button" class="rem-x" aria-label="Zamknij">&times;</button></div>
      <div class="rem-body">
        <div class="rem-name">${count} ${count === 1 ? "kontakt" : "kontaktów"}</div>
        <div class="rem-company">${late ? `Zaległe: ${late}` : "Wszystkie w najbliższych minutach"}</div>
        <div class="rem-actions"><a class="btn btn-primary" href="/crm/plan/">Plan dnia</a></div>
      </div>`;
    el.querySelector(".rem-x").addEventListener("click", () => closeCard(el));
    return el;
  }

  function showFresh(fresh, now) {
    if (!fresh.length) return;
    beep();
    if (fresh.length > MAX_CARDS) {   // dużo naraz (np. po zalogowaniu): jedna zbiorcza karta
      const late = fresh.filter((l) => dueMs(l) < now && l.status !== "new").length;
      box.appendChild(summaryCard(fresh.length, late));
      browserNotify("Masz oddzwonienia do zrobienia",
        late ? `Zaległe: ${late}, razem: ${fresh.length}.` : `Do zrobienia: ${fresh.length}.`, "/crm/plan/");
      return;
    }
    fresh.forEach((l) => {
      box.appendChild(leadCard(l, now));
      const late = dueMs(l) < now;
      browserNotify(l.status === "new" ? (late ? "Czas na ponowną próbę" : "Zaraz ponowna próba") : late ? "Zaległe oddzwonienie" : "Zaraz oddzwonienie",
        who(l) + " – " + relText(l.next_contact_at, now), "/crm/queue/?lead=" + l.id, "leadcrm-" + keyOf(l));
    });
  }

  /* ---------- licznik ---------- */
  function paintCount(total) {
    const badge = $("plan-badge");
    if (badge) { badge.textContent = total; badge.hidden = !total; }
    document.title = total ? `(${total}) ${baseTitle}` : baseTitle;
  }

  /* ---------- ocena, co jest „do zrobienia” (co sekundę, bez zapytań do serwera) ---------- */
  function evaluate() {
    const c = cfg(), now = Date.now();
    const active = cache.filter((l) => dueMs(l) <= now + c.lead * 1000);   // zaległe + w oknie „przed terminem”
    paintCount(active.length);   // licznik zostaje także przy wyłączonych przypomnieniach

    if (!c.enabled) { box.querySelectorAll(".reminder").forEach(closeCard); return; }

    const byId = new Map(active.map((l) => [String(l.id), l]));   // załatwione leady znikają z kart
    box.querySelectorAll(".reminder[data-lead]").forEach((el) => {
      if (!el.dataset.lead) return;
      const l = byId.get(el.dataset.lead);
      if (!l || el.dataset.iso !== l.next_contact_at) closeCard(el);   // załatwiony albo termin zmieniony
    });

    const seen = readSeen();
    const fresh = active.filter((l) => !seen[keyOf(l)]);
    if (fresh.length) {
      fresh.forEach((l) => { seen[keyOf(l)] = now; });
      writeSeen(seen);
      showFresh(fresh, now);
    }
    box.querySelectorAll(".reminder[data-iso]").forEach((el) => paintCard(el, now));
  }

  /* ---------- odpytywanie serwera ---------- */
  let busy = false, again = false;
  async function poll() {
    if (busy) { again = true; return; }   // odświeżenie w trakcie odpytywania: powtórz zaraz po nim
    busy = true;
    try {
      const minutes = Math.min(1440, Math.ceil(cfg().lead / 60) + 1);
      const r = await api("/api/my-leads/due/?minutes=" + minutes);
      if (!r.ok) return;
      const d = await r.json();
      cache = [...(d.overdue || []), ...(d.upcoming || [])];
      evaluate();
    } catch { /* chwilowy brak sieci: spróbujemy za 30 s */ }
    finally {
      busy = false;
      if (again) { again = false; poll(); }
    }
  }
  CRM.refreshReminders = poll;

  /* ---------- okno ustawień ---------- */
  const dlg = $("notif-dialog");
  function paintSettings() {
    const c = cfg();
    $("notif-enabled").checked = c.enabled;
    $("notif-sound").checked = c.sound;
    $("notif-daybar").checked = c.dayBar !== false;
    $("notif-min").value = Math.floor(c.lead / 60);
    $("notif-sec").value = c.lead % 60;
    paintBrowserButton();
  }
  function readLead() {
    const m = Math.min(120, Math.max(0, parseInt($("notif-min").value, 10) || 0));
    const s = Math.min(59, Math.max(0, parseInt($("notif-sec").value, 10) || 0));
    return m * 60 + s;
  }
  function wireSettings() {
    const open = $("notif-open");
    if (!open || !dlg) return;
    open.addEventListener("click", () => {
      $("user-popover").hidden = true;
      $("user-btn").setAttribute("aria-expanded", "false");
      say("");
      paintSettings();
      dlg.showModal();
    });
    $("notif-enabled").addEventListener("change", () => { saveCfg({ enabled: $("notif-enabled").checked }); evaluate(); });
    $("notif-daybar").addEventListener("change", () => {
      saveCfg({ dayBar: $("notif-daybar").checked });
      document.dispatchEvent(new CustomEvent("crm:view"));   // kolejka od razu pokazuje / chowa pasek
    });
    $("notif-sound").addEventListener("change", () => {
      saveCfg({ sound: $("notif-sound").checked });
      if ($("notif-sound").checked) beep();   // od razu słychać, jak brzmi
    });
    ["notif-min", "notif-sec"].forEach((id) => $(id).addEventListener("change", () => {
      const lead = readLead();
      saveCfg({ lead });
      $("notif-min").value = Math.floor(lead / 60);
      $("notif-sec").value = lead % 60;
      poll();   // okno zapytania zależy od czasu „przed terminem”
    }));
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  }

  const btn = $("notif-btn");
  if (btn) btn.addEventListener("click", toggleBrowser);
  const test = $("notif-test");
  if (test) test.addEventListener("click", sendTest);
  wireSettings();
  paintBrowserButton();
  poll();
  setInterval(poll, POLL_MS);
  setInterval(evaluate, 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
};

/* Solid CRM – co dzieje się na telefonie agenta (Solid Dialer 1.3+), na każdej stronie CRM.
   GET /api/phone/live/ co 3 s (gdy karta jest na wierzchu i telefon jest połączony):
   - pasek „Dzwoni do Ciebie: Firma X” z „Otwórz kartę” (karta NIE przełącza się sama, gdy agent jest u innego
     klienta – tylko gdy w „Dzwonieniu” nie ma otwartego klienta),
   - karty „Numer klienta już dostępny – możesz dzwonić” (SMS od sieci) i „Klient dzwonił – nieodebrane”,
   - licznik nowych SMS-ów od klientów (zdarzenie „crm:phone” dla zakładki SMS w „Dzwonieniu”).
   Do tego: linki tel: (np. „Zadzwoń” w kalendarzu) przy połączonym telefonie dzwonią przez telefon z dialerem,
   a nie przez program do dzwonienia na komputerze, i okno „Na mój telefon” (tekst / plik SMS-em na prywatny numer). */
CRM.initPhone = function initPhone() {
  const { api, esc } = CRM;
  let paired = null, timer = null, last = null, ringShownFor = null;
  const DISMISSED = "crm_phone_dismissed";
  const p2 = (n) => String(n).padStart(2, "0");
  const hhmm = (v) => { const t = new Date(v); return `${t.getHours()}:${p2(t.getMinutes())}`; };
  const STATUS = (s) => (CRM.STATUS && CRM.STATUS[s] ? CRM.STATUS[s][0] : "");

  /* ---------- krótki komunikat (strony mają różne „toasty”, ten działa wszędzie) ---------- */
  let noteEl = null, noteTimer = null;
  function notice(text, bad) {
    if (!noteEl) { noteEl = document.createElement("div"); noteEl.className = "ph-notice"; noteEl.setAttribute("role", "status"); document.body.appendChild(noteEl); }
    noteEl.textContent = text; noteEl.classList.toggle("bad", !!bad); noteEl.hidden = false;
    clearTimeout(noteTimer); noteTimer = setTimeout(() => { noteEl.hidden = true; }, 5000);
  }
  CRM.phoneNotice = notice;

  async function post(url, body) {
    try {
      const r = await api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const d = await r.json().catch(() => ({}));
      return r.ok ? { data: d } : { error: d.detail || Object.values(d).flat().join(" ") || "Nie udało się." };
    } catch { return { error: "Brak połączenia z serwerem." }; }
  }

  /* ---------- dzwonienie przez telefon z dialerem ---------- */
  CRM.dialViaPhone = async (phone, lead) => {
    const r = await post("/api/dialer/call/", lead ? { lead } : { phone });
    if (r.error) { notice(r.error, true); return false; }
    notice(r.data.online === false ? "Telefon nie odpowiada – otwórz Solid Dialer. Numer wybierze się, gdy telefon się połączy." : `Telefon wybiera numer${phone ? " " + phone : ""}…`);
    return true;
  };
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="tel:"]');
    if (!a || !paired || e.defaultPrevented || a.closest(".lead-pane-skip")) return;
    e.preventDefault();
    const lead = Number(a.dataset.lead) || null;
    CRM.dialViaPhone(decodeURIComponent(a.getAttribute("href").slice(4)), lead);
  });

  /* ---------- karta „Dzwoni do Ciebie” (na samej górze stosu kart w prawym rogu, nic nie zasłania) ---------- */
  const bar = document.createElement("div");
  bar.className = "reminder ring-card"; bar.hidden = true; bar.setAttribute("role", "alert");
  const placeBar = () => { const box = document.getElementById("reminders"); if (box && box.firstChild !== bar) box.prepend(bar); else if (!box && !bar.isConnected) document.body.appendChild(bar); };
  function openLead(id) {
    if (CRM.openLead && location.pathname.startsWith("/crm/queue")) CRM.openLead(id);
    else location.href = `/crm/queue/?lead=${id}`;
  }
  function paintRing(r) {
    if (!r) { bar.hidden = true; bar.innerHTML = ""; ringShownFor = null; return; }
    const talking = Date.now() - new Date(r.since).getTime() > 45000;
    const head = talking ? "Rozmowa z klientem" : "Dzwoni do Ciebie";
    const open = CRM.queueCurrentId && CRM.queueCurrentId() === r.lead;
    const html = `<div class="rem-bar"><span class="rem-icon rb-ic" aria-hidden="true">📞</span><span class="rem-kind">${head}</span></div>
      <div class="rem-body">${r.lead
        ? `<div class="rem-name">${esc(r.name)}</div>
           <div class="rem-company">${esc([STATUS(r.status), r.phone].filter(Boolean).join(" · "))}</div>
           ${r.note ? `<div class="ph-sms">Sprawa: ${esc(r.note)}</div>` : ""}
           <div class="rem-actions">${open ? `<span class="rb-open">✓ Karta tego klienta jest otwarta</span>` : `<button type="button" class="btn btn-primary" data-ring-open="${r.lead}">Otwórz kartę</button>`}</div>`
        : `<div class="rem-name">${esc(r.phone)}</div><div class="rem-company">Tego numeru nie ma wśród Twoich klientów.</div>`}</div>`;
    if (bar.dataset.html !== html) { bar.innerHTML = html; bar.dataset.html = html; }
    placeBar();
    bar.hidden = false;
    // „Dzwonienie” bez otwartego klienta: od razu pokazujemy kartę dzwoniącego (nic nie przerywamy)
    if (r.lead && ringShownFor !== r.id) {
      ringShownFor = r.id;
      // (nie zaraz po wczytaniu strony – wtedy „Dzwonienie” jeszcze otwiera klienta, po którego agent przyszedł)
      if (CRM.queueCurrentId && CRM.queueCurrentId() === null && performance.now() > 6000) openLead(r.lead);
    }
  }
  bar.addEventListener("click", (e) => { const b = e.target.closest("[data-ring-open]"); if (b) { openLead(Number(b.dataset.ringOpen)); setTimeout(() => last && paintRing(last.ringing), 300); } });

  /* ---------- karty „już dostępny” i „klient dzwonił” ---------- */
  const dismissed = () => { try { return JSON.parse(localStorage.getItem(DISMISSED) || "[]"); } catch { return []; } };
  const dismiss = (key) => { try { localStorage.setItem(DISMISSED, JSON.stringify([key, ...dismissed()].slice(0, 60))); } catch { /* brak dostępu */ } };
  function cardsBox() { return document.getElementById("reminders"); }
  function paintCards(d) {
    const box = cardsBox(); if (!box) return;
    const gone = new Set(dismissed());
    const want = [
      ...d.available.map((s) => ({ key: "a" + s.id, kind: "available", s })),
      ...d.missed.filter((c) => !gone.has("m" + c.id)).map((c) => ({ key: "m" + c.id, kind: "missed", c })),
    ];
    const keys = new Set(want.map((w) => w.key));
    box.querySelectorAll(".ph-card").forEach((el) => { if (!keys.has(el.dataset.key)) el.remove(); });
    want.forEach((w) => {
      if (box.querySelector(`.ph-card[data-key="${w.key}"]`)) return;
      const el = document.createElement("div");
      el.className = `reminder ph-card ${w.kind}`; el.dataset.key = w.key;
      const lead = w.kind === "available" ? w.s.lead : w.c.lead;
      const name = w.kind === "available" ? w.s.name : w.c.name;
      const phone = w.kind === "available" ? w.s.phone : w.c.phone;
      const status = w.kind === "available" ? w.s.status : w.c.status;
      el.innerHTML = `<div class="rem-bar"><span class="rem-icon" aria-hidden="true">${w.kind === "available" ? "✓" : "☎"}</span>
          <span class="rem-kind">${w.kind === "available" ? "Klient już dostępny – możesz dzwonić" : "Klient dzwonił – nieodebrane"}</span>
          <button type="button" class="rem-x" aria-label="Zamknij">&times;</button></div>
        <div class="rem-body"><div class="rem-name">${esc(name)}</div>
          <div class="rem-company">${esc([STATUS(status), phone, "o " + hhmm(w.kind === "available" ? w.s.received_at : w.c.at)].filter(Boolean).join(" · "))}</div>
          ${w.kind === "available" ? `<div class="ph-sms">„${esc(w.s.text)}”</div>` : ""}
          <div class="rem-actions"><button type="button" class="btn btn-primary" data-ph-call>Zadzwoń</button><button type="button" class="btn btn-secondary" data-ph-open>Otwórz kartę</button></div></div>`;
      const close = () => {
        el.remove();
        if (w.kind === "available") post("/api/phone/sms/", { ids: [w.s.id] }); else dismiss(w.key);
      };
      el.querySelector(".rem-x").addEventListener("click", close);
      el.querySelector("[data-ph-open]").addEventListener("click", () => { openLead(lead); close(); });
      el.querySelector("[data-ph-call]").addEventListener("click", async () => {
        if (location.pathname.startsWith("/crm/queue") && CRM.openLead) await CRM.openLead(lead);
        if (paired) CRM.dialViaPhone(phone, lead); else location.href = "tel:" + String(phone).replace(/[^\d+]/g, "");
        close();
      });
      box.prepend(el);
      if (CRM.playChime) CRM.playChime();
    });
    if (!bar.hidden) placeBar();   // „Dzwoni do Ciebie” zawsze na samej górze
  }

  /* ---------- pytanie o stan ---------- */
  async function poll() {
    clearTimeout(timer);
    if (!document.hidden) {
      try {
        const r = await api("/api/phone/live/");
        if (r.ok) {
          const d = await r.json();
          paired = d.paired; last = d;
          paintRing(d.ringing); paintCards(d);
          CRM.phoneState = d;
          document.dispatchEvent(new CustomEvent("crm:phone", { detail: d }));
        }
      } catch { /* brak sieci – spróbujemy za chwilę */ }
    }
    timer = setTimeout(poll, paired ? 3000 : 60000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  CRM.refreshPhone = poll;
  poll();

  /* ---------- „Na mój telefon”: tekst albo plik SMS-em na prywatny numer ---------- */
  const dlg = document.getElementById("self-dialog");
  if (!dlg) return;
  const $ = (id) => document.getElementById(id);
  const err = (t) => { $("self-error").textContent = t || ""; $("self-error").hidden = !t; };
  let stateTimer = null;
  async function openSelf() {
    err(""); $("self-state").textContent = ""; $("self-text").value = ""; $("self-file").value = ""; $("self-file-name").textContent = "";
    dlg.showModal();
    const r = await api("/api/phone/self/").catch(() => null);
    const d = r && r.ok ? await r.json() : {};
    $("self-phone").value = d.private_phone || "";
    $("self-nophone").hidden = d.paired !== false;
    (d.private_phone ? $("self-text") : $("self-phone")).focus();
  }
  CRM.openSelfPhone = openSelf;
  document.querySelectorAll("[data-self-open]").forEach((b) => b.addEventListener("click", () => { const pop = document.getElementById("user-popover"); if (pop) pop.hidden = true; openSelf(); }));
  $("self-file").addEventListener("change", () => { const f = $("self-file").files[0]; $("self-file-name").textContent = f ? `${f.name} (${Math.max(1, Math.round(f.size / 1024))} KB)` : ""; });
  $("self-cancel").addEventListener("click", () => dlg.close());
  [$("self-text"), $("self-phone")].forEach((el) => el.addEventListener("keydown", (e) => e.stopPropagation()));
  $("self-form").addEventListener("submit", async (e) => {
    e.preventDefault(); err("");
    const phone = $("self-phone").value.trim(), text = $("self-text").value.trim(), file = $("self-file").files[0];
    if ((phone.replace(/\D/g, "")).length < 9) { err("Wpisz swój prywatny numer (9 cyfr)."); $("self-phone").focus(); return; }
    if (!text && !file) { err("Wpisz tekst albo wybierz plik."); return; }
    if (file && file.size > 10 * 1024 * 1024) { err("Plik jest za duży (najwyżej 10 MB)."); return; }
    $("self-ok").disabled = true;
    try {
      const saved = await api("/api/phone/self/", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ private_phone: phone }) });
      if (!saved.ok) { const d = await saved.json().catch(() => ({})); err((d.private_phone || [d.detail || "Zły numer."]).join(" ")); return; }
      const body = new FormData(); body.append("text", text); if (file) body.append("file", file);
      const r = await api("/api/phone/self/", { method: "POST", body });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err(d.detail || "Nie udało się wysłać."); return; }
      $("self-state").textContent = "Czekam, aż telefon z dialerem wyśle SMS…";
      clearInterval(stateTimer);
      let n = 0;
      stateTimer = setInterval(async () => {
        n++;
        const s = await api(`/api/phone/self/${d.id}/`).then((x) => (x.ok ? x.json() : null)).catch(() => null);
        if (!s) return;
        if (s.state === "sent") { clearInterval(stateTimer); $("self-state").textContent = `✓ Wysłane na ${d.phone}.`; $("self-text").value = ""; $("self-file").value = ""; $("self-file-name").textContent = ""; }
        else if (s.state === "failed" || s.state === "expired" || n > 150) { clearInterval(stateTimer); $("self-state").textContent = s.state === "failed" ? `Nie wyszło: ${s.error}` : "Telefon nie odebrał zlecenia – sprawdź, czy Solid Dialer działa."; }
      }, 2000);
    } finally { $("self-ok").disabled = false; }
  });
};

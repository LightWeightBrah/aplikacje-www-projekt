/* Samouczek po całym Solid CRM: krótkie kroki na kolejnych stronach (Dzwonienie → Kalendarz → Wyniki → Ranking),
   a managerowi dodatkowo Zespół, Import, Taryfy, Użytkownicy i Ustawienia.
   Pokazuje się raz każdemu agentowi i managerowi; ponownie: „Moje ustawienia” → „Pokaż samouczek jeszcze raz”.
   Krok w toku pamięta sessionStorage („crm_tour”), więc po przejściu na inną stronę samouczek idzie dalej.
   Strona „Dzwonienie” podpina CRM.tourQueue (przykładowy klient, podgląd wyników), żeby nic się nie zapisało. */
CRM.initTour = function initTour() {
  const me = CRM.currentUser() || {};
  const DONE_KEY = "crm_tour_done_" + (me.username || "");
  const STEP_KEY = "crm_tour";
  const Q = "/crm/queue/";
  const STEPS = [
    { page: Q, sel: "#claim-btn", title: "Następny klient", text: "Klikasz i CRM sam wybiera, do kogo dzwonić: najpierw oferty i zainteresowani, potem oddzwonienia, ponowne próby, na końcu nowe firmy." },
    { page: Q, sel: "#segs", title: "Twoje grupy klientów", text: "Oferty, Oddzwonienia, Nie odebrali, Nowi. Kliknij grupę, żeby zobaczyć tylko ją. Niżej widać, ile firm zostało w bazie i w puli." },
    { page: Q, sel: "#lead-pane .w-head", also: "#lead-pane .w-call", title: "Karta klienta", text: "Firma, osoba kontaktowa, numer i „Zadzwoń”. To <b>przykładowy klient</b> – w samouczku nic się nie zapisuje." },
    { page: Q, sel: "#out-panel .w-outs", title: "Wynik rozmowy", text: "Po rozmowie wybierz jeden z 8 wyników. Pod każdym pojawia się krótki formularz – zobacz dwa przykłady." },
    { page: Q, sel: "#step", demo: "callback", title: "Przykład: Oddzwonić", text: "Termin jednym kliknięciem (np. „Za 1 h”) albo wpisz „jutro 10”. Zaznacz, czy klient wie, że dzwonimy z T-Mobile." },
    { page: Q, sel: "#step", demo: "offer", title: "Przykład: Wyślij ofertę", text: "Taryfa, liczba numerów i okres umowy – zobowiązanie i <b>Twoja prowizja liczą się same</b>. Oferta idzie do wysyłki z terminem follow-upu." },
    { page: Q, sel: "#save-next", title: "Zapisz i dalej", text: "Zapisuje wynik i od razu otwiera następnego klienta. Pomyłka? Przez 10 s możesz kliknąć „Cofnij”." },
    { page: Q, sel: ".w-quick", title: "Polecenie i szybki telefon", text: "„+ Nowy kontakt” – np. znajomy klienta z polecenia. „Zadzwoń na numer” – dowolny numer bez szukania klienta." },
    { page: Q, sel: "#side", title: "Panel „Dziś”", text: "Twoje liczby dnia i minuty rozmów. Cel: 15 min na godzinę pracy – mocniejsza godzina nadrabia słabszą." },
    { page: "/crm/plan/", sel: "#plan-list", title: "Kalendarz", text: "Wszystkie umówione kontakty z godziną i sprawą. Klik w wiersz otwiera klienta. „Miesiąc” pokazuje cały miesiąc." },
    { page: "/crm/", sel: "#minutes-card", title: "Wyniki", text: "Twoje minuty rozmów godzina po godzinie albo dzień po dniu (wybierz okres u góry), rozmowy, oferty i prowizja." },
    { page: "/crm/ranking/", sel: "#tabs", title: "Ranking", text: "Najważniejsza jest aktywność: minuty i rozmowy. Obok sprzedaż i prowizja." },
  ];
  /* Manager dostaje ten sam samouczek + swoje strony (zespół, bazy, taryfy, użytkownicy, ustawienia). */
  const isManager = me.role === "manager";
  if (isManager) STEPS.push(
    { page: "/crm/ranking/", sel: "#access-card", title: "Kto widzi ranking", text: "Tylko manager: wyłączasz ranking wybranym agentom albo wszystkim naraz." },
    { page: "/crm/manager/", sel: "#att-card", title: "Obecność", text: "Kto kiedy przyszedł (zalogował się) i wyszedł (wylogował się), czas pracy, przerwy, telefony i minuty rozmów względem celu. Wybierz dzień u góry." },
    { page: "/crm/manager/", sel: "#tabs", title: "Wszyscy klienci", text: "Cała baza zespołu: „Zaległe” – przegapione terminy, „Pula” – nikt ich nie ma, „Nieosiągalni” – nie odbierają. Zaznacz klientów i przekaż ich innemu agentowi albo do puli." },
    { page: "/crm/import/", sel: "#import-form", title: "Import bazy", text: "Wgraj plik z CEIDG / KRS (Excel). CRM pominie duplikaty i naprawi przesunięte wiersze. Bazę możesz od razu dać jednemu agentowi." },
    { page: "/crm/products/", sel: "#products-table", title: "Taryfy", text: "Taryfy z cenami (z promocją i bez) i opisem do oferty. Z nich liczy się zobowiązanie i prowizja agenta." },
    { page: "/crm/users/", sel: "#users-table", title: "Użytkownicy", text: "Dodajesz agentów, resetujesz hasła, wyłączasz konto. Klientów odchodzącego agenta przekazujesz innemu – historia jego pracy zostaje." },
    { page: "/crm/settings/", sel: "#rules-form", title: "Ustawienia zespołu", text: "Ile razy i co ile dzwonić do tych, co nie odbierają, godziny pracy, losowanie firm." },
    { page: "/crm/settings/", sel: "#offer-card", title: "Oferty", text: "Na jaki adres idą oferty do wysłania (osoba z firmowym mailem T-Mobile), podpis, telefon i mnożniki prowizji (24 / 36 mies.)." },
    { page: "/crm/settings/", sel: "#ann-card", title: "Solid info", text: "Ogłoszenia dla zespołu (promocje, cel dnia) – agenci widzą je w panelu przy dzwonieniu." },
  );
  STEPS.push({ page: STEPS[STEPS.length - 1].page, sel: "#user-btn", title: "Moje ustawienia", text: "Motyw jasny / ciemny, telefon (Solid Dialer), skróty klawiszowe, pasek „Cofnij” i ten samouczek." });
  const store = {
    get: (k, s = sessionStorage) => { try { return s.getItem(k); } catch { return null; } },
    set: (k, v, s = sessionStorage) => { try { s.setItem(k, v); } catch { /* bez pamięci */ } },
    del: (k) => { try { sessionStorage.removeItem(k); } catch { /* bez pamięci */ } },
  };
  const path = () => location.pathname;
  let ov = null, i = 0, active = false;

  function visible(sel) { const el = document.querySelector(sel); return el && el.offsetParent !== null ? el : null; }
  const waitFor = (sel, ms = 2500) => new Promise((resolve) => {
    const t0 = Date.now();
    (function look() { const el = visible(sel); if (el || Date.now() - t0 > ms) resolve(el); else setTimeout(look, 120); })();
  });

  async function show(n) {
    i = n;
    const step = STEPS[i];
    if (!step) { finish(); return; }
    if (step.page !== path()) { store.set(STEP_KEY, String(i)); location.href = step.page; return; }
    const qt = CRM.tourQueue;
    if (step.page === Q && qt) { qt.prepare(); qt.demo(step.demo || null); }
    const el = await waitFor(step.sel);
    if (!el) { show(i + 1); return; }   // np. ranking wyłączony przez managera – pomijamy krok
    paint(step, el);
  }
  function paint(step, el) {
    if (!ov) build();
    // górne menu jest przyklejone: podświetlenie nie może na nie wchodzić
    const nav = document.querySelector(".topnav"), inNav = !!(nav && nav.contains(el));
    const navB = nav && !inNav ? nav.getBoundingClientRect().bottom : 0;   // element w samym menu (np. konto) – bez przycinania
    const roomH = window.innerHeight - navB - 24;
    if (inNav) { /* menu jest zawsze widoczne */ }
    else if (el.offsetHeight > roomH) {
      // duży element (np. lista w Kalendarzu): pokazujemy jego początek tuż pod menu
      const sc = CRM.scroller();   // przewija się środek strony (main.content), nie okno
      sc.scrollBy({ top: el.getBoundingClientRect().top - navB - 16, behavior: "instant" });
    } else el.scrollIntoView({ block: "center", behavior: "instant" });
    const r0 = el.getBoundingClientRect(), extra = step.also && visible(step.also);
    const r1 = extra ? extra.getBoundingClientRect() : r0;
    const r = { top: Math.min(r0.top, r1.top), left: Math.min(r0.left, r1.left), right: Math.max(r0.right, r1.right), bottom: Math.max(r0.bottom, r1.bottom) };
    // tylko widoczna część: od dołu menu do dołu okna
    r.top = Math.max(r.top, navB + 12); r.bottom = Math.min(r.bottom, window.innerHeight - 12);
    const hole = ov.querySelector(".tour-hole"), card = ov.querySelector(".tour-card");
    Object.assign(hole.style, { top: r.top - 6 + "px", left: r.left - 6 + "px", width: r.right - r.left + 12 + "px", height: r.bottom - r.top + 12 + "px" });
    ov.querySelector(".tour-n").textContent = `Samouczek · ${i + 1} z ${STEPS.length}`;
    ov.querySelector("#tour-t").textContent = step.title;
    ov.querySelector(".tour-p").innerHTML = step.text;
    ov.querySelector('[data-t="prev"]').hidden = i === 0;
    ov.querySelector('[data-t="next"]').textContent = i === STEPS.length - 1 ? (isManager ? "Gotowe" : "Zaczynam dzwonić") : "Dalej";
    const cw = Math.min(380, window.innerWidth - 24);
    card.style.width = cw + "px";
    const ch = card.offsetHeight || 190;
    let top = r.bottom + 14;
    if (top + ch > window.innerHeight - 8) top = r.top - ch - 14;
    if (top < 8) top = Math.max(8, window.innerHeight - ch - 16);   // duży element: karta na dole ekranu
    let left = Math.max(12, Math.min(window.innerWidth - cw - 12, r.left));
    if (top < r.bottom && top + ch > r.top && r.right + cw + 24 < window.innerWidth) left = r.right + 14;   // obok, gdy zasłaniałaby
    card.style.top = top + "px"; card.style.left = left + "px";
    ov.querySelector('[data-t="next"]').focus({ preventScroll: true });
  }
  function build() {
    ov = document.createElement("div");
    ov.className = "tour";
    ov.innerHTML = `<div class="tour-hole"></div><div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-t"><small class="tour-n"></small><h3 id="tour-t"></h3><p class="tour-p"></p>
      <div class="tour-actions"><button type="button" class="link-btn" data-t="skip">Zakończ</button><span></span><button type="button" class="btn btn-secondary" data-t="prev">Wstecz</button><button type="button" class="btn btn-primary" data-t="next">Dalej</button></div></div>`;
    document.body.appendChild(ov);
    ov.addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (!b) return; if (b.dataset.t === "skip") finish(); else show(i + (b.dataset.t === "next" ? 1 : -1)); });
    window.addEventListener("resize", () => { const s = STEPS[i], el = s && visible(s.sel); if (el) paint(s, el); });
    document.addEventListener("keydown", keys, true);
  }
  function keys(e) {
    if (!active) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(); }
    else if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); show(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); if (i > 0) show(i - 1); }
  }
  function finish() {
    active = false; CRM.tourActive = false;
    store.del(STEP_KEY); store.set(DONE_KEY, "1", localStorage);
    if (ov) { ov.remove(); ov = null; }
    document.removeEventListener("keydown", keys, true);
    if (CRM.tourQueue) CRM.tourQueue.cleanup();
    if (path() !== Q) location.href = Q;
  }
  function start(from = 0) {
    active = true; CRM.tourActive = true;
    show(from);
  }
  CRM.tour = {
    start: () => start(0),
    /* czy pokazać samouczek przy wejściu na stronę (pierwszy raz agenta albo kontynuacja z innej strony) */
    resume() {
      const n = store.get(STEP_KEY);
      if (n !== null && STEPS[Number(n)] && STEPS[Number(n)].page === path()) { store.del(STEP_KEY); start(Number(n)); return true; }
      return false;
    },
    shouldAutoStart: () => (me.role === "agent" || isManager) && !store.get(DONE_KEY, localStorage),
  };
  /* strony inne niż „Dzwonienie” same kontynuują samouczek; „Dzwonienie” robi to po wczytaniu danych */
  if (path() !== Q) setTimeout(() => CRM.tour.resume(), 400);
};

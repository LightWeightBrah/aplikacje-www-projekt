/* Kolejka agenta: lista leadów (grupy wg pilności), karta rozmowy i szybki zapis wyniku. */
(() => {
  if (!CRM.esc || !CRM.$ || !CRM.STATUS || typeof WHEN === "undefined") {
    document.getElementById("lead-list").textContent = "Wczytała się stara wersja strony. Odśwież ją skrótem Ctrl+F5.";
    return;
  }
  const { api, esc, STATUS, $ } = CRM;
  const me = CRM.currentUser() || {};
  const SCHEDULING = ["callback", "interested", "no_answer"];
  const REQUIRED = ["callback", "interested"];   // te wyniki wymagają terminu i sprawy
  const { parseWhen, fmtInput, fmtLong, fmtNative } = WHEN;
  const OUTCOMES = [
    ["no_answer", "Nie odebrał", "Zaliczy próbę (po 3 nieodebranych lead wraca do puli). Możesz zaplanować ponowną próbę."],
    ["callback", "Oddzwonić", "Lead zostaje u Ciebie do wskazanego terminu."],
    ["interested", "Zainteresowany", "Status „Zainteresowany”. Wpisz, kiedy i w jakiej sprawie ponownie zadzwonić."],
    ["refused", "Odmowa", "Lead zostaje zamknięty jako przegrany."],
    ["wrong_number", "Zły numer", "Lead zostaje zamknięty jako „Nie dzwonić”."],
  ];
  const OUT_LABEL = { ...Object.fromEntries(OUTCOMES.map((o) => [o[0], o[1]])), note: "Notatka" };
  const CLOSED = ["won", "lost", "do_not_call", "unreachable"];
  const GROUPS = ["Zaległe – oddzwoń teraz", "Dziś", "Zaplanowane", "Bez terminu"];
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("pl-PL",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–");
  const chip = (code) => { const [l, c] = STATUS[code] || [code, "#64748b"]; return `<span class="chip" style="--c:${c}">${esc(l)}</span>`; };
  const cache = { companies: {}, products: {} };
  let leads = [], selectedId = null, outcome = null, resetForm = null;

  function rel(iso) {
    const m = Math.round((new Date(iso) - Date.now()) / 60000), a = Math.abs(m);
    const t = a < 60 ? `${a} min` : a < 1440 ? `${Math.round(a / 60)} h` : Math.round(a / 1440) === 1 ? "1 dzień" : `${Math.round(a / 1440)} dni`;
    return m < 0 ? `${t} temu` : `za ${t}`;
  }
  function groupOf(l) {
    if (!l.next_contact_at) return 3;
    const t = new Date(l.next_contact_at), now = new Date();
    return t < now ? 0 : t.toDateString() === now.toDateString() ? 1 : 2;
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

  async function loadLeads() {
    let url = "/api/my-leads/", all = [];
    for (let i = 0; i < 5 && url; i++) {
      const d = await (await api(url)).json();
      all = all.concat(d.results);
      url = d.next ? d.next.replace(location.origin, "") : null;
    }
    const key = (l) => (l.next_contact_at ? new Date(l.next_contact_at).getTime() : Infinity);
    leads = all.filter((l) => !CLOSED.includes(l.status)).sort((a, b) => key(a) - key(b));
    renderList();
  }

  function renderList() {
    $("lead-count").textContent = leads.length ? `(${leads.length})` : "";
    if (!leads.length) { $("lead-list").innerHTML = `<p class="muted">Brak aktywnych leadów. Kliknij „Daj kolejnego leada”.</p>`; return; }
    let html = "", last = -1;
    leads.forEach((l) => {
      const g = groupOf(l);
      if (g !== last) { html += `<h3 class="grp g${g}">${GROUPS[g]}</h3>`; last = g; }
      const when = l.next_contact_at ? `<small class="when${g === 0 ? " late" : ""}" title="${fmt(l.next_contact_at)}">${rel(l.next_contact_at)} · ${fmt(l.next_contact_at)}</small>` : "";
      html += `<button type="button" class="lead-item${l.id === selectedId ? " active" : ""}" data-id="${l.id}">
        <div><strong>${esc(l.contact_name)}</strong>${chip(l.status)}</div><small>${esc(l.phone)}</small>${when}${l.next_contact_note ? `<small class="why">${esc(l.next_contact_note)}</small>` : ""}</button>`;
    });
    $("lead-list").innerHTML = html;
  }

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
    outcome = null;
    renderCard(d, company, product);
  }

  function renderCard(d, company, product) {
    const tel = esc(d.phone.replace(/[^\d+]/g, ""));
    const hist = d.activities.length ? d.activities.map((a) => `<li><b>${esc(OUT_LABEL[a.outcome] || a.outcome)}</b> <span class="who">· ${a.agent_name ? (a.agent === me.id ? "Ty" : esc(a.agent_name)) : "–"}</span>
      <time>${fmt(a.created_at)}</time>${a.note ? `<p>${esc(a.note)}</p>` : ""}</li>`).join("")
      : `<li class="muted">Brak historii kontaktów.</li>`;
    const next = d.next_contact_at ? new Date(d.next_contact_at) : null, late = next && next < new Date();
    const long = (iso) => new Date(iso).toLocaleString("pl-PL", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
    const lastNote = d.activities.find((a) => a.note);
    const who = lastNote ? (lastNote.agent === me.id ? "Ty" : esc(lastNote.agent_name || "–")) : "";
    const nextBlock = `<div class="next-card${late ? " late" : ""}">
      <div class="nc-section"><span class="nc-label">${late ? "Zaległy kontakt" : "Następny kontakt"}</span>
        ${next ? `<div class="nc-when">${esc(rel(d.next_contact_at))}</div><div class="nc-date">${esc(long(d.next_contact_at))}</div>
          ${d.next_contact_note ? `<div class="nc-why"><span>Sprawa:</span> ${esc(d.next_contact_note)}</div>` : ""}`
          : `<div class="nc-date">Nie zaplanowano</div>`}</div>
      ${lastNote ? `<div class="nc-section nc-last"><span class="nc-label">Ostatni komentarz · ${who} · ${fmt(lastNote.created_at)}</span><div class="nc-text">${esc(lastNote.note)}</div></div>` : ""}
    </div>`;
    $("lead-pane").innerHTML = `
      <div class="card">
        <div class="lead-head"><div><h2 class="lead-name">${esc(d.contact_name)}</h2>
          <p class="muted">${company ? esc(company.name) : "Brak firmy"}${product ? " · " + esc(product.name) : ""}</p></div>${chip(d.status)}</div>
        ${nextBlock}
        <div class="facts">
          <div><span>Telefon</span><a class="big-phone" href="tel:${tel}">${esc(d.phone)}</a></div>
          <div><span>E-mail</span>${d.email ? `<a href="mailto:${esc(d.email)}">${esc(d.email)}</a>` : "–"}</div>
          <div><span>NIP / KRS</span>${company ? esc(company.nip || "–") + " / " + esc(company.krs || "–") : "–"}</div>
          <div><span>Próby</span>${d.attempts_count}</div>
        </div>
      </div>
      <form class="card" id="act-form" novalidate>
        <h2>Jak poszła rozmowa?</h2>
        <div class="outcomes" role="radiogroup">${OUTCOMES.map(([v, l], i) =>
          `<button type="button" role="radio" data-outcome="${v}" aria-checked="false"><kbd>${i + 1}</kbd>${l}</button>`).join("")}</div>
        <p class="hint" id="hint">Wybierz wynik (klawisze 1–5). Pomyłka? Kliknij ten sam przycisk albo Esc, żeby zacząć od nowa.</p>
        <div id="cb-fields" hidden>
          <div class="field"><label for="cb-at" id="cb-label">Kiedy oddzwonić</label>
            <div class="when-row"><input id="cb-at" autocomplete="off" placeholder="np. jutro 9:30">
              <span class="cal-wrap"><button type="button" class="icon-btn" id="cb-pick" title="Wybierz z kalendarza" aria-label="Wybierz z kalendarza"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/></svg></button><input type="datetime-local" id="cb-native" tabindex="-1" aria-hidden="true"></span></div>
            <small class="hint" id="cb-preview"></small></div>
          <div class="presets">${PRESETS.map(([l], i) => `<button type="button" data-preset="${i}" aria-pressed="false">${l}</button>`).join("")}</div>
          <label><span id="cb-why-label">W jakiej sprawie</span><input id="cb-note" maxlength="255" placeholder="np. wróci z urlopu, chce wycenę"></label>
        </div>
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
      <div class="card"><h2>Historia kontaktów</h2><ul class="history">${hist}</ul></div>`;
    wireForm(d.id);
  }

  async function postActivity(body) {
    return api("/api/activities/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  async function afterSave(leadId, goNext) {
    toast("Zapisano.");
    await loadLeads();
    if (goNext && (await claim())) return;
    if (leads.some((l) => l.id === leadId)) select(leadId);
    else if (leads.length) select(leads[0].id);
    else emptyPane();
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
      $("hint").textContent = "Wybierz wynik (klawisze 1–5). Pomyłka? Kliknij ten sam przycisk albo Esc, żeby zacząć od nowa.";
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
      const sched = SCHEDULING.includes(outcome), required = REQUIRED.includes(outcome);
      $("cb-fields").hidden = !sched;
      if (sched) {
        $("cb-label").textContent = { callback: "Kiedy oddzwonić", interested: "Kiedy ponownie zadzwonić", no_answer: "Ponowna próba (opcjonalnie)" }[outcome];
        $("cb-why-label").textContent = required ? "W jakiej sprawie (wymagane)" : "W jakiej sprawie (opcjonalnie)";
        $("cb-at").value = required ? fmtInput(atNine(1)) : "";
        updatePreview();
        form.querySelectorAll("[data-preset]").forEach((p) => p.setAttribute("aria-pressed", "false"));
      }
      $("hint").textContent = OUTCOMES.find((o) => o[0] === outcome)[2];
      submits().forEach((s) => { s.disabled = false; });
      /* Kursor od razu ląduje tam, gdzie agent ma coś wpisać lub zatwierdzić (Enter). */
      const focusOn = { callback: "cb-at", interested: "cb-at", refused: "act-note" }[outcome];
      (focusOn ? $(focusOn) : form.querySelector("[data-next]")).focus();
      if (focusOn === "cb-at") $("cb-at").select();   // od razu można nadpisać domyślny termin
    }));
    const clearPresets = () => form.querySelectorAll("[data-preset]").forEach((p) => p.setAttribute("aria-pressed", "false"));
    $("cb-at").addEventListener("input", () => { clearPresets(); updatePreview(); });
    $("cb-at").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("cb-note").focus(); } });
    form.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
      const was = b.getAttribute("aria-pressed") === "true";
      clearPresets();
      if (was && !REQUIRED.includes(outcome)) { $("cb-at").value = ""; updatePreview(); return; }  /* drugie kliknięcie czyści termin */
      b.setAttribute("aria-pressed", "true");
      $("cb-at").value = fmtInput(PRESETS[Number(b.dataset.preset)][1]());
      updatePreview();
    }));
    $("note-only").addEventListener("click", async () => {
      const note = $("act-note").value.trim();
      if (!note) { fail("Wpisz treść notatki."); return; }
      const r = await postActivity({ lead: leadId, outcome: "note", note });
      if (!r.ok) { fail(errText(await r.json().catch(() => ({})))); return; }
      afterSave(leadId, false);
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); err.hidden = true;
      if (!outcome) { fail("Wybierz wynik rozmowy."); return; }
      const goNext = e.submitter && e.submitter.dataset.next === "1";
      const body = { lead: leadId, outcome, note: $("act-note").value.trim() };
      if (SCHEDULING.includes(outcome)) {
        const raw = $("cb-at").value.trim(), why = $("cb-note").value.trim();
        const when = raw ? parseWhen(raw) : null;
        if (raw && !when) { fail("Nie rozumiem terminu. Spróbuj np. „jutro 9:30” albo „25.10 10:23”."); return; }
        if (REQUIRED.includes(outcome) && (!when || !why)) { fail("Podaj termin i w jakiej sprawie ponownie zadzwonić."); return; }
        if (when && when < new Date()) { fail("Ten termin już minął."); return; }
        if (when) { body.next_contact_at = when.toISOString(); body.next_contact_note = why; }
      }
      submits().forEach((s) => { s.disabled = true; });
      const r = await postActivity(body);
      submits().forEach((s) => { s.disabled = false; });
      if (!r.ok) { fail(errText(await r.json().catch(() => ({})))); return; }
      afterSave(leadId, goNext);
    });
  }

  async function claim() {
    const btn = $("claim-btn"); btn.disabled = true;
    const r = await api("/api/leads/next/", { method: "POST" });
    btn.disabled = false;
    const d = await r.json().catch(() => ({}));
    if (r.status === 409) { toast(`Masz zaległe oddzwonienia (${d.overdue_count}). Zrób je najpierw – są na górze listy.`, true); return false; }
    if (r.status === 404) { toast("Pula jest pusta – brak leadów do pobrania.", true); return false; }
    if (!r.ok) { toast(d.detail || "Nie udało się pobrać leada.", true); return false; }
    await loadLeads(); select(d.id);
    return true;
  }

  $("claim-btn").addEventListener("click", claim);
  $("lead-list").addEventListener("click", (e) => {
    const item = e.target.closest("[data-id]");
    if (item) select(Number(item.dataset.id));
  });
  /* Skróty: 1–5 wybiera wynik, Ctrl+Enter zapisuje i bierze kolejnego. */
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
  loadLeads().then(() => (leads.length ? select(leads[0].id) : emptyPane())).catch(() => {
    $("lead-list").innerHTML = '<p class="form-error">Nie udało się wczytać leadów. Odśwież stronę (Ctrl+F5).</p>';
  });
})();

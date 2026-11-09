/* Solid CRM – kalendarz miesięczny: terminy kontaktów z nazwą firmy, kolor = rodzaj kontaktu.
   Kliknięcie dnia otwiera panel z całym dniem (godziny, sprawy, telefony). „+ Dodaj callback”
   umawia termin z własnym klientem: POST /api/my-leads/{id}/add-callback/ (bez zapisywania rozmowy). */
(() => {
  const { api, esc, $ } = CRM;
  const SEG_COLOR = { offer: "var(--seg-offer)", callback: "var(--seg-callback)", retry: "var(--seg-retry)", new: "var(--seg-new)" };
  const SEG_LABEL = { offer: "Oferta / zainteresowany", callback: "Umówione oddzwonienie", retry: "Ponowna próba", new: "Nowy" };
  const segOf = (l) => (["offer", "interested"].includes(l.status) ? "offer" : l.status === "callback" ? "callback" : l.attempts_count ? "retry" : "new");
  const DOW = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Niedz"];
  const MAX_PER_DAY = 4;
  const p2 = (n) => String(n).padStart(2, "0");
  const key = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
  const hhmm = (v) => { const t = new Date(v); return `${p2(t.getHours())}:${p2(t.getMinutes())}`; };
  const longDay = (k) => new Date(k + "T12:00:00").toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" });
  let month = new Date(); month.setDate(1);
  let byDay = {}, openDay = null, toastTimer;
  /* „Tylko umówione” (domyślnie) chowa automatyczne ponowne próby po „Nie odebrał”; wybór pamięta przeglądarka */
  const CAL_KEY = "crm_cal_show_all";
  let showAll = false;
  try { showAll = localStorage.getItem(CAL_KEY) === "1"; } catch { /* brak dostępu */ }
  function paintFilter() { document.querySelectorAll("[data-calf]").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.calf === "all") === showAll))); }
  function wireFilter(reload) {
    paintFilter();
    document.querySelectorAll("[data-calf]").forEach((b) => b.addEventListener("click", () => {
      showAll = b.dataset.calf === "all";
      try { localStorage.setItem(CAL_KEY, showAll ? "1" : "0"); } catch { /* brak dostępu */ }
      paintFilter(); reload();
    }));
  }

  function toast(msg, isError) {
    const t = $("toast"); if (!t) return;
    t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
  }

  async function load() {
    $("m-title").textContent = month.toLocaleDateString("pl-PL", { month: "long", year: "numeric" });
    const r = await api(`/api/my-leads/month/?month=${month.getFullYear()}-${p2(month.getMonth() + 1)}`);
    const all = r.ok ? await r.json() : [];
    const leads = showAll ? all : all.filter((l) => segOf(l) !== "retry" && segOf(l) !== "new");
    const hidden = all.length - leads.length;
    $("cal-hidden").innerHTML = hidden ? `Ukryte ponowne próby: <b>${hidden}</b> (pokaż: „Wszystko”)` : "";
    byDay = {};
    leads.forEach((l) => { const k = key(new Date(l.next_contact_at)); (byDay[k] = byDay[k] || []).push(l); });
    const start = new Date(month); start.setDate(1 - ((month.getDay() + 6) % 7));
    const today = key(new Date());
    let html = DOW.map((d) => `<div class="dow">${d}</div>`).join("");
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      if (i >= 35 && d.getMonth() !== month.getMonth()) break;
      const k = key(d), items = byDay[k] || [];
      const shown = items.slice(0, MAX_PER_DAY);
      html += `<div class="cal-day${d.getMonth() !== month.getMonth() ? " other" : ""}${k === today ? " today" : ""}${k === openDay ? " picked" : ""}" data-day="${k}" role="button" tabindex="0" aria-label="${esc(longDay(k))}: ${items.length} kontaktów">
        <div class="cal-num"><span>${d.getDate()}</span>${items.length ? `<small>${items.length}</small>` : ""}</div>
        ${shown.map((l) => { const late = new Date(l.next_contact_at) < new Date() && segOf(l) !== "retry";
          return `<div class="cal-ev${late ? " late" : ""}" style="--c:${SEG_COLOR[segOf(l)]}" title="${esc(CRM.leadTitle(l))}${l.next_contact_note ? " – " + esc(l.next_contact_note) : ""}"><b>${hhmm(l.next_contact_at)}</b><span>${esc(CRM.leadTitle(l))}</span></div>`; }).join("")}
        ${items.length > shown.length ? `<div class="cal-more">+ ${items.length - shown.length} więcej</div>` : ""}</div>`;
    }
    $("cal-grid").innerHTML = html;
    if (openDay) paintDay();
  }

  /* ---------- panel dnia ---------- */
  function paintDay() {
    const items = (byDay[openDay] || []).slice().sort((a, b) => new Date(a.next_contact_at) - new Date(b.next_contact_at));
    const now = new Date();
    const title = longDay(openDay);
    $("day-title").textContent = title.charAt(0).toUpperCase() + title.slice(1);   // „Piątek, 6 listopada”
    $("day-sub").textContent = items.length ? `${items.length} ${items.length === 1 ? "kontakt" : items.length < 5 ? "kontakty" : "kontaktów"}` : "Brak umówionych kontaktów";
    $("day-list").innerHTML = items.length ? items.map((l) => {
      const seg = segOf(l), late = new Date(l.next_contact_at) < now && seg !== "retry";
      const tel = String(l.phone || "").replace(/[^\d+]/g, "");
      return `<article class="cal-item${late ? " late" : ""}" style="--c:${late ? "var(--late)" : SEG_COLOR[seg]}">
        <div class="cal-item-time">${hhmm(l.next_contact_at)}${late ? "<small>spóźnione</small>" : ""}</div>
        <div class="cal-item-body">
          <div class="cal-item-title"><strong>${esc(CRM.leadTitle(l))}</strong>${l.contact_name && l.contact_name !== CRM.leadTitle(l) ? `<span class="muted"> · ${esc(l.contact_name)}</span>` : ""}</div>
          <div class="cal-item-kind">${esc(SEG_LABEL[seg])}${l.phone ? ` · ${esc(l.phone)}` : ""}${l.transferred_from ? ` · ↪ od ${esc(l.transferred_from)}` : ""}</div>
          ${l.next_contact_note ? `<div class="cal-item-note">Sprawa: ${esc(l.next_contact_note)}</div>` : ""}
        </div>
        <div class="cal-item-actions"><a class="btn btn-primary" href="/crm/queue/?lead=${l.id}">Otwórz</a>${tel ? `<a class="btn btn-secondary" href="tel:${esc(tel)}" data-lead="${l.id}">Zadzwoń</a>` : ""}</div>
      </article>`;
    }).join("") : `<p class="muted">Tego dnia nic nie jest umówione. Kliknij „+ Dodaj callback”, żeby umówić kontakt.</p>`;
  }
  function showDay(k) {
    openDay = k;
    document.querySelectorAll(".cal-day.picked").forEach((el) => el.classList.remove("picked"));
    const cell = document.querySelector(`.cal-day[data-day="${k}"]`); if (cell) cell.classList.add("picked");
    paintDay();
    if (!$("day-dialog").open) $("day-dialog").showModal();
  }

  /* ---------- „+ Dodaj callback” ---------- */
  let picked = null, findTimer;
  function openAdd(dayKey) {
    picked = null;
    $("cb-find").value = ""; $("cb-results").innerHTML = ""; $("cb-picked").hidden = true; $("cb-find").hidden = false;
    $("cb-date").value = dayKey && dayKey >= key(new Date()) ? dayKey : key(new Date());
    $("cb-time").value = "10:00"; $("cb-note").value = ""; $("cb-error").hidden = true;
    if ($("day-dialog").open) $("day-dialog").close();
    $("cb-dialog").showModal();
    $("cb-find").focus();
  }
  async function findClients() {
    const q = $("cb-find").value.trim();
    if (q.length < 2) { $("cb-results").innerHTML = q ? `<p class="muted small">Wpisz co najmniej 2 znaki.</p>` : ""; return; }
    const r = await api(`/api/my-leads/?search=${encodeURIComponent(q)}&page_size=8`);
    const d = r.ok ? await r.json() : { results: [] };
    const rows = (d.results || []).filter((l) => !["won", "lost", "do_not_call"].includes(l.status));
    $("cb-results").innerHTML = rows.length ? rows.map((l) => `<button type="button" class="cb-row" data-pick="${l.id}"><strong>${esc(CRM.leadTitle(l))}</strong><span class="muted"> · ${esc(l.phone || "")}${l.next_contact_at ? ` · teraz: ${new Date(l.next_contact_at).toLocaleString("pl-PL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}</span></button>`).join("")
      : `<p class="muted small">Nie znaleziono Twojego klienta „${esc(q)}”. Szukaj po nazwie firmy, telefonie albo NIP.</p>`;
    $("cb-results").querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      picked = rows.find((l) => l.id === Number(b.dataset.pick));
      $("cb-picked").innerHTML = `<span><strong>${esc(CRM.leadTitle(picked))}</strong> <span class="muted">· ${esc(picked.phone || "")}</span></span><button type="button" class="btn-mini" id="cb-change">Zmień</button>`;
      $("cb-picked").hidden = false; $("cb-find").hidden = true; $("cb-results").innerHTML = "";
      if (!$("cb-note").value && picked.next_contact_note) $("cb-note").value = picked.next_contact_note;
      $("cb-change").addEventListener("click", () => { picked = null; $("cb-picked").hidden = true; $("cb-find").hidden = false; $("cb-find").focus(); });
      $("cb-time").focus();
    }));
  }
  async function saveCallback(e) {
    e.preventDefault();
    const err = (m) => { $("cb-error").textContent = m; $("cb-error").hidden = !m; };
    if (!picked) { err("Wybierz klienta z listy."); return; }
    const at = new Date(`${$("cb-date").value}T${$("cb-time").value || "10:00"}`);
    if (isNaN(at)) { err("Podaj dzień i godzinę."); return; }
    if (at < new Date()) { err("Ten termin już minął. Wybierz późniejszą godzinę."); return; }
    $("cb-ok").disabled = true;
    try {
      const r = await api(`/api/my-leads/${picked.id}/add-callback/`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ next_contact_at: at.toISOString(), next_contact_note: $("cb-note").value.trim() }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err(d.detail || Object.values(d).flat().join(" ") || "Nie udało się zapisać."); return; }
      $("cb-dialog").close();
      toast(`Umówiono: ${CRM.leadTitle(picked)} · ${at.toLocaleString("pl-PL", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`);
      if (at.getMonth() !== month.getMonth() || at.getFullYear() !== month.getFullYear()) { month = new Date(at); month.setDate(1); }
      openDay = key(at);
      await load();
      showDay(openDay);
      if (CRM.refreshReminders) CRM.refreshReminders();
    } catch { err("Brak połączenia z serwerem. Spróbuj ponownie."); }
    finally { $("cb-ok").disabled = false; }
  }

  wireFilter(load);
  $("cal-grid").addEventListener("click", (e) => { const day = e.target.closest("[data-day]"); if (day) showDay(day.dataset.day); });
  $("cal-grid").addEventListener("keydown", (e) => { const day = e.target.closest("[data-day]"); if (day && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); showDay(day.dataset.day); } });
  $("day-close").addEventListener("click", () => $("day-dialog").close());
  $("day-dialog").addEventListener("close", () => { openDay = null; document.querySelectorAll(".cal-day.picked").forEach((el) => el.classList.remove("picked")); });
  $("day-add").addEventListener("click", () => openAdd(openDay));
  $("cb-open").addEventListener("click", () => openAdd(null));
  $("cb-cancel").addEventListener("click", () => $("cb-dialog").close());
  $("cb-find").addEventListener("input", () => { clearTimeout(findTimer); findTimer = setTimeout(findClients, 250); });
  $("cb-form").addEventListener("submit", saveCallback);
  $("m-prev").addEventListener("click", () => { month.setMonth(month.getMonth() - 1); load(); });
  $("m-next").addEventListener("click", () => { month.setMonth(month.getMonth() + 1); load(); });
  $("m-today").addEventListener("click", () => { month = new Date(); month.setDate(1); load(); });
  load();
})();

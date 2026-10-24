/* Plan dnia: GET /api/my-leads/schedule/?date= (zaległe + kontakty z wybranego dnia)
   oraz pobranie kalendarza .ics z GET /api/my-leads/calendar/. */
CRM.initPlan = function initPlan() {
  const { api, esc, STATUS, $ } = CRM;
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const fromIso = (s) => new Date(s + "T12:00:00");
  const today = () => iso(new Date());
  let day = today();
  let toastTimer;

  function toast(msg, isError) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
  }
  const hhmm = (v) => new Date(v).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  const longDay = (s) => fromIso(s).toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const shortDay = (v) => new Date(v).toLocaleDateString("pl-PL", { day: "numeric", month: "short" });
  const chip = (code) => { const [l, c] = STATUS[code] || [code, "#64748b"]; return `<span class="chip" style="--c:${c}">${esc(l)}</span>`; };

  function lateText(iso) {
    const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    if (m < 1) return "Termin właśnie minął";
    if (m < 60) return "Spóźnione o " + m + " min";
    const h = Math.floor(m / 60);
    if (h < 24) return "Spóźnione o " + h + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
    return "Spóźnione o " + Math.floor(h / 24) + " d";
  }

  const ICON_NOTE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  function inText(iso) {
    const m = Math.ceil((new Date(iso).getTime() - Date.now()) / 60000);
    if (m <= 0) return "teraz";
    if (m < 60) return "za " + m + " min";
    const h = Math.floor(m / 60);
    return h < 24 ? "za " + h + " h" + (m % 60 ? " " + (m % 60) + " min" : "") : "";
  }

  function item(l, opts = {}) {
    const company = l.company_name ? `<span class="muted"> · ${esc(l.company_name)}</span>` : "";
    const date = opts.withDate ? `<span class="plan-date">${shortDay(l.next_contact_at)}</span>` : "";
    let sub = "";
    if (opts.passed) sub = `<span class="plan-sub late">${lateText(l.next_contact_at)}</span>`;
    else if (opts.today) sub = `<span class="plan-sub${opts.next ? " next" : ""}">${opts.next ? "Następny · " : ""}${inText(l.next_contact_at)}</span>`;
    return `<article class="plan-item${opts.next ? " is-next" : ""}${opts.passed ? " is-passed" : ""}" data-href="/crm/queue/?lead=${l.id}">
      <div class="plan-time">${date}<span class="plan-hour">${hhmm(l.next_contact_at)}</span>${sub}</div>
      <div class="plan-body">
        <div class="plan-title"><strong>${esc(l.contact_name)}</strong>${company}</div>
        ${l.next_contact_note ? `<div class="plan-note">${ICON_NOTE}<span>${esc(l.next_contact_note)}</span></div>` : ""}
        <div class="plan-meta"><a class="plan-phone" href="tel:${esc(l.phone.replace(/[^\d+]/g, ""))}">${esc(l.phone)}</a>
          ${chip(l.status)}${l.missed_calls > 0 ? `<span class="missed-chip">Nie odebrał w terminie${l.missed_calls > 1 ? " ×" + l.missed_calls : ""}</span>` : ""}${l.product_name ? `<span class="muted small">${esc(l.product_name)}</span>` : ""}</div>
      </div>
      <div class="plan-go"><a class="btn btn-primary plan-open" href="/crm/queue/?lead=${l.id}">Otwórz leada</a></div>
    </article>`;
  }

  function render(d) {
    const isToday = d.date === today();
    const now = Date.now();
    const overdue = d.overdue || [], sched = d.scheduled || [];
    $("overdue-card").hidden = !overdue.length;
    $("overdue-count").textContent = overdue.length ? `(${overdue.length})` : "";
    $("overdue-list").innerHTML = overdue.map((l) => item(l, { withDate: true, passed: true })).join("");

    const nextIdx = isToday ? sched.findIndex((l) => new Date(l.next_contact_at).getTime() >= now) : -1;
    $("plan-list").innerHTML = sched.length
      ? sched.map((l, i) => item(l, {
        today: isToday,
        next: i === nextIdx,
        passed: isToday && new Date(l.next_contact_at).getTime() < now,
      })).join("")
      : `<p class="muted">Na ten dzień nie masz zaplanowanych kontaktów. Nowych klientów weźmiesz w <a href="/crm/queue/">kolejce</a>.</p>`;

    $("day-title").textContent = (isToday ? "Dziś" : longDay(d.date)) + ` · zaplanowane: ${sched.length}`;
    $("subtitle").textContent = longDay(d.date) +
      (isToday && overdue.length ? ` · zaległych: ${overdue.length}` : "");
  }

  async function load() {
    $("page-error").hidden = true;
    $("day-input").value = day;
    try {
      const r = await api("/api/my-leads/schedule/?date=" + day);
      if (!r.ok) throw new Error("Nie udało się pobrać planu dnia (" + r.status + ").");
      render(await r.json());
    } catch (ex) {
      $("page-error").textContent = ex.message;
      $("page-error").hidden = false;
    }
  }

  /* Cały wiersz otwiera leada (poza numerem telefonu i przyciskami, które mają własne linki) */
  ["overdue-list", "plan-list"].forEach((id) => $(id).addEventListener("click", (e) => {
    const row = e.target.closest(".plan-item[data-href]");
    if (row && !e.target.closest("a, button")) location.href = row.dataset.href;
  }));

  const shift = (n) => { const d = fromIso(day); d.setDate(d.getDate() + n); day = iso(d); load(); };
  $("day-prev").addEventListener("click", () => shift(-1));
  $("day-next").addEventListener("click", () => shift(1));
  $("day-today").addEventListener("click", () => { day = today(); load(); });
  $("day-input").addEventListener("change", () => { if ($("day-input").value) { day = $("day-input").value; load(); } });

  $("ics-btn").addEventListener("click", async () => {
    const btn = $("ics-btn");
    btn.disabled = true;
    try {
      const r = await api("/api/my-leads/calendar/");
      if (!r.ok) throw new Error("Nie udało się pobrać kalendarza (" + r.status + ").");
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "leadcrm.ics" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast("Pobrano leadcrm.ics: otwórz plik, żeby dodać terminy do kalendarza.");
    } catch (ex) { toast(ex.message, true); }
    btn.disabled = false;
  });

  load();
  setInterval(() => { if (day === today() && !document.hidden) load(); }, 60000);  // „Następny” i „Termin minął” na bieżąco
};

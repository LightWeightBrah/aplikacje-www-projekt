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

  function item(l, opts = {}) {
    const company = l.company_name ? `<span class="muted"> · ${esc(l.company_name)}</span>` : "";
    const when = opts.withDate ? `<span class="plan-date">${shortDay(l.next_contact_at)}</span>` : "";
    const flag = opts.next ? `<span class="chip" style="--c:#4f46e5">Następny</span>`
      : opts.passed ? `<span class="chip" style="--c:#ef4444">Termin minął</span>` : "";
    return `<article class="plan-item${opts.next ? " is-next" : ""}${opts.passed ? " is-passed" : ""}">
      <div class="plan-time">${when}${hhmm(l.next_contact_at)}</div>
      <div class="plan-body">
        <div class="plan-title"><strong>${esc(l.contact_name)}</strong>${company}</div>
        ${l.next_contact_note ? `<div class="plan-note">Sprawa: ${esc(l.next_contact_note)}</div>` : ""}
        <div class="plan-meta"><a class="plan-phone" href="tel:${esc(l.phone.replace(/[^\d+]/g, ""))}">${esc(l.phone)}</a>
          ${chip(l.status)}${flag}${l.product_name ? `<span class="muted small">${esc(l.product_name)}</span>` : ""}</div>
      </div>
      <a class="btn btn-secondary plan-open" href="/crm/queue/?lead=${l.id}">Otwórz</a>
    </article>`;
  }

  function render(d) {
    const isToday = d.date === today();
    const now = Date.now();
    const overdue = d.overdue || [], sched = d.scheduled || [];
    $("overdue-card").hidden = !overdue.length;
    $("overdue-count").textContent = overdue.length ? `(${overdue.length})` : "";
    $("overdue-list").innerHTML = overdue.map((l) => item(l, { withDate: true })).join("");

    const nextIdx = isToday ? sched.findIndex((l) => new Date(l.next_contact_at).getTime() >= now) : -1;
    $("plan-list").innerHTML = sched.length
      ? sched.map((l, i) => item(l, {
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

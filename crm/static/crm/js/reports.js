/* Raporty managera: lejek osób, godziny odbierania, powody odmów, oferty bez odpowiedzi,
   końce umów. Dane: GET /api/reports/?days=N (crm/reports.py). */
CRM.initReports = function initReports() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager") {
    $("page-error").textContent = "Ta strona jest dostępna tylko dla managera.";
    $("page-error").hidden = false;
    return;
  }
  const money = (v) => CRM.money(v);
  const pct = (r) => (r == null ? "–" : `${Math.round(r * 100)}%`);
  const day = (iso) => new Date(iso.length === 10 ? iso + "T12:00:00" : iso).toLocaleDateString("pl-PL", { day: "numeric", month: "numeric", year: "numeric" });
  const dayTime = (iso) => new Date(iso).toLocaleString("pl-PL", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
  const monthName = (ym) => { const [y, m] = ym.split("-").map(Number); return new Date(y, m - 1, 15).toLocaleDateString("pl-PL", { month: "short" }) + (m === 1 ? ` ${y}` : ""); };
  const monthYear = (iso) => { const [y, m] = iso.split("-"); return `${m}.${y}`; };
  const WEEKDAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "niedz"];
  const link = (id, name) => `<a href="/crm/manager/?lead=${id}" class="rp-link">${esc(name)}</a>`;
  const plural = (n, one, few, many) => (n === 1 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many);

  function kpis(d) {
    const t = d.funnel.team, o = d.offers, c = d.contracts, best = d.hours.best_hours;
    const items = [
      ["Rozmów na 1 sprzedaż", t.sold ? String(Math.round(t.calls / t.sold)) : "–", t.sold ? `${t.sold} ${plural(t.sold, "sprzedaż", "sprzedaże", "sprzedaży")} z ${t.calls} telefonów` : "w tym okresie bez sprzedaży"],
      ["Oferty bez odpowiedzi", String(o.count), o.count ? `${money(o.monthly)} / mies. czeka na decyzję` : "wszystkie oferty mają odpowiedź"],
      ["Można przenosić teraz", String(c.ready_idle_count), c.ready_idle_count ? "klienci po końcu umowy, do których nikt nie dzwoni" : "nikt nie czeka"],
      ["Najlepsze godziny", best.length ? best.map((h) => `${h}:00`).join(", ") : "–", best.length ? "najwięcej odebranych telefonów" : "za mało telefonów, żeby to ocenić"],
    ];
    $("rp-kpis").innerHTML = items.map(([label, value, sub]) =>
      `<div class="kpi"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`).join("");
  }

  function funnel(f) {
    const cell = (r, key) => `<td class="num${r.weak_step === key ? " rp-weak" : ""}">${pct(r[key])}</td>`;
    const row = (r, foot) => `<tr${foot ? "" : ` class="${r.weak_step ? "has-tip" : ""}"`}><td><strong>${esc(r.name)}</strong></td>
      <td class="num">${r.calls}</td>${cell(r, "answer_rate")}${cell(r, "offer_rate")}${cell(r, "close_rate")}
      <td class="num"><b>${r.sold}</b></td><td class="num nowrap">${esc(money(r.commission))}</td></tr>`
      + (!foot && r.weak_step ? `<tr class="rp-tip-row"><td colspan="7"><span class="rp-tip"><b>Do poprawy: ${esc(r.weak_label)}.</b> ${esc(r.tip)}</span></td></tr>` : "")
      + (!foot && !r.weak_step && r.calls < f.min_calls ? "" : "");
    $("rp-funnel").innerHTML = `<thead><tr><th>Osoba</th><th class="num">Telefony</th><th class="num">Dodzwonienia</th><th class="num">Rozmowa → oferta</th><th class="num">Oferta → sprzedaż</th><th class="num">Sprzedane</th><th class="num">Prowizja</th></tr></thead>`
      + `<tbody>${f.rows.length ? f.rows.map((r) => row(r)).join("") : `<tr><td colspan="7" class="muted">W tym okresie nikt nie dzwonił.</td></tr>`}</tbody>`
      + (f.rows.length ? `<tfoot>${row(f.team, true)}</tfoot>` : "");
  }

  function hours(h) {
    document.querySelectorAll(".rp-few-note").forEach((n) => n.remove());
    const best = new Set(h.best_hours);
    const max = Math.max(0.01, ...h.hours.map((x) => x.rate || 0));
    $("rp-hours").innerHTML = h.hours.map((x) => `<div class="rp-col${best.has(x.hour) ? " best" : ""}${x.few ? " few" : ""}" title="${x.hour}:00–${x.hour + 1}:00 · odebrano ${x.answered} z ${x.calls}${x.few ? " (za mało telefonów, żeby to ocenić)" : ""}">
        <span class="rp-val">${x.calls ? pct(x.rate) : ""}</span><i style="height:${x.calls ? Math.max(4, ((x.rate || 0) / max) * 100) : 0}%"></i><small>${x.hour}</small></div>`).join("");
    if (h.hours.some((x) => x.few)) $("rp-hours").insertAdjacentHTML("afterend", `<p class="muted small rp-few-note">Wyblakłe słupki: za mało telefonów o tej godzinie, żeby wynik coś znaczył.</p>`);
    const days = h.weekdays.filter((x) => x.calls || x.weekday <= 5);
    $("rp-weekdays").innerHTML = days.map((x) => `<span class="rp-day"><b>${WEEKDAYS[x.weekday - 1]}</b> ${x.calls ? pct(x.rate) : "–"}</span>`).join("");
  }

  function refusals(r) {
    if (!r.total) { $("rp-refusals").innerHTML = `<p class="muted">W tym okresie nie było odmów.</p>`; return; }
    const max = Math.max(...r.rows.map((x) => x.count));
    $("rp-refusals").innerHTML = r.rows.map((x) => `<div class="orow"><span class="olabel">${esc(x.reason)}</span>
        <div class="track"><i style="width:${(x.count / max) * 100}%;background:#dc2626"></i></div><b>${x.count}</b></div>`).join("")
      + `<p class="muted small rp-note">Razem odmów: <b>${r.total}</b>. Jedna odmowa może mieć kilka powodów.</p>`;
  }

  function offers(o) {
    $("rp-offers-desc").innerHTML = o.count
      ? `Klienci, którym ofertę wysłano co najmniej ${o.wait_days} dni temu, a jeszcze nie kupili ani nie odmówili: <b>${o.count}</b>, razem <b>${esc(money(o.monthly))} / mies.</b> To najcieplejsi klienci. Na czerwono termin follow-upu, który już minął. Kliknij firmę, żeby zobaczyć szczegóły.`
      : `Brak ofert, które czekają dłużej niż ${o.wait_days} dni. Tak trzymać!`;
    $("rp-offers").innerHTML = o.rows.length ? `<thead><tr><th>Firma</th><th>Agent</th><th>Oferta</th><th class="num">Mies.</th><th>Wysłana</th><th>Follow-up</th></tr></thead><tbody>`
      + o.rows.map((x) => `<tr><td>${link(x.lead, x.name)}<small class="muted rp-sub">${esc(x.phone)}</small></td><td>${esc(x.agent)}</td><td>${esc(x.product || "–")}</td>
          <td class="num nowrap">${esc(money(x.monthly))}</td><td class="nowrap">${esc(day(x.offer_at))} <small class="muted">(${x.days} ${plural(x.days, "dzień", "dni", "dni")} temu)</small></td>
          <td class="nowrap${x.overdue ? " rp-late" : ""}">${x.next_contact_at ? esc(dayTime(x.next_contact_at)) + (x.overdue ? " – zaległy" : "") : "brak terminu"}</td></tr>`).join("") + `</tbody>` : "";
  }

  function contracts(c) {
    const max = Math.max(1, ...c.months.map((m) => m.count));
    $("rp-months").innerHTML = c.months.map((m) => `<div class="rp-col" title="${monthName(m.month)}: ${m.count}">
        <span class="rp-val">${m.count || ""}</span><i style="height:${m.count ? Math.max(4, (m.count / max) * 100) : 0}%"></i><small>${esc(monthName(m.month))}</small></div>`).join("");
    const STATUS = CRM.STATUS || {};
    $("rp-ready").innerHTML = c.ready_idle.length
      ? `<ul class="rp-list">${c.ready_idle.map((x) => `<li>${link(x.lead, x.name)}<span class="muted small">umowa do ${esc(monthYear(x.contract_end))} · ${esc((STATUS[x.status] || [x.status])[0])}</span></li>`).join("")}</ul>`
        + (c.ready_idle_count > c.ready_idle.length ? `<p class="muted small">…i ${c.ready_idle_count - c.ready_idle.length} więcej.</p>` : "")
        + `<p class="muted small">Przypisz ich agentowi w „Zespół” albo otwórz i umów telefon.</p>`
      : `<p class="muted">Nikt nie czeka.</p>`;
    $("rp-upcoming").innerHTML = c.upcoming.length
      ? `<ul class="rp-list">${c.upcoming.map((x) => `<li>${link(x.lead, x.name)}<span class="muted small">przenosić od ${esc(day(x.porting_from))} · ${esc(x.agent)}</span></li>`).join("")}</ul>`
      : `<p class="muted">Jeszcze nikt nie ma zapisanego końca umowy. Agent zapisuje go przy wyniku rozmowy („Do kiedy klient ma umowę?”).</p>`;
  }

  async function load() {
    const days = $("rp-days").value;
    try {
      const r = await api(`/api/reports/?days=${days}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      $("rp-period").textContent = `${day(d.date_from)} – ${day(d.date_to)}`;
      kpis(d); funnel(d.funnel); hours(d.hours); refusals(d.refusals); offers(d.offers); contracts(d.contracts);
      $("rp-area").hidden = false; $("page-error").hidden = true;
    } catch {
      $("page-error").textContent = "Nie udało się wczytać raportów. Odśwież stronę.";
      $("page-error").hidden = false;
    }
  }
  $("rp-days").addEventListener("change", load);
  load();
};

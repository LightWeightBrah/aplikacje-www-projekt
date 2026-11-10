/* Wyniki: „Moje minuty rozmów” dla okresu wybranego u góry strony (Dziś / 7 / 30 / 90 dni / własny).
   Dziś: słupki godzina po godzinie. Dłuższy okres: dzień po dniu.
   Cel jest dzienny: 15 min × godziny pracy (od pierwszego do ostatniego zapisu), więc mocniejsza
   godzina nadrabia słabszą. Dane: GET /api/work/minutes/?days=… albo ?date_from=…&date_to=… */
CRM.initMinutes = function initMinutes() {
  const { api, esc, $ } = CRM;
  const box = $("minutes-card");
  if (!box) return;
  const fmt = (m) => CRM.talk(m);   // czas rozmów: „14 s”, „3 min 5 s”
  const few = (n) => n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20);
  /* czas pracy „3 h 20 min” */
  const dur = (m) => { m = Math.max(0, Math.round(m)); const h = Math.floor(m / 60), r = m % 60; return h ? `${h} h${r ? ` ${r} min` : ""}` : `${r} min`; };
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  void few;
  const dayLabel = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("pl-PL", { weekday: "short", day: "numeric", month: "numeric" });

  /* Manager → „Cały zespół”: suma minut i celu wszystkich osób (?scope=team) */
  const teamTab = () => document.querySelector('#scope-tabs:not([hidden]) button[data-scope="team"]');
  const isTeam = () => { const b = teamTab(); return !!b && b.getAttribute("aria-selected") === "true"; };
  function query() {
    const sel = $("days");
    if (sel && sel.value === "custom") {
      const a = $("date-from").value, b = $("date-to").value;
      return a && b ? `date_from=${a}&date_to=${b}` : null;
    }
    return "days=" + (sel ? sel.value : "1");
  }
  const kpi = (big, small, ok) => `<div class="mn-kpi${ok === true ? " ok" : ok === false ? " low" : ""}"><b>${big}</b><span>${small}</span></div>`;

  /* Słupki. Dzień po dniu (c.goal): obrys = ile powinno być tego dnia, wypełnienie = ile jest.
     Napis nad słupkiem: „12 / 40” – zawsze nad wyższym z nich. */
  function bars(cols, max, goalLine) {
    const pct = (v) => Math.min(100, (v / max) * 100);
    return (goalLine ? `<div class="mn-goal" style="bottom:${pct(goalLine)}%"><span>cel ${fmt(goalLine)}</span></div>` : "") +
      cols.map((c) => {
        const top = Math.max(pct(c.value), c.goal ? pct(c.goal) : 0);
        const label = c.goal ? `${fmt(c.value)} / ${fmt(c.goal)}` : c.value ? fmt(c.value) : "";
        return `<div class="mn-col${c.now ? " is-now" : ""}${c.goal ? " has-goal" : ""}" title="${esc(c.title)}">
        ${c.goal ? `<em class="mn-target" style="height:${pct(c.goal)}%" aria-hidden="true"></em>` : ""}
        <span class="mn-val" style="bottom:calc(${top}% + 4px)">${label}</span>
        <i class="${c.cls}" style="height:${c.value ? Math.max(3, pct(c.value)) : c.goal ? 0 : 1}%"></i>
        <small>${esc(c.label)}</small></div>`;
      }).join("");
  }

  function render(d) {
    const g = d.goal_minutes_per_hour, single = d.days.length === 1, today = d.date_from === new Date().toLocaleDateString("sv-SE");
    const dayOk = d.goal ? d.minutes >= d.goal : null;
    const team = d.scope === "team";
    box.querySelector("h2").innerHTML = `${team ? "Minuty rozmów zespołu" : "Moje minuty rozmów"} <small class="muted">${single ? (today ? "dziś, godzina po godzinie" : dayLabel(d.date_from)) : "dzień po dniu"}</small>`;
    if (team) {
      const people = d.people || 0;
      $("minutes-sum").innerHTML =
        kpi(`${fmt(d.minutes)} / ${fmt(d.goal)}`, "rozmowy zespołu / tyle powinno być", dayOk) +
        kpi(d.worked_minutes ? dur(d.worked_minutes) : "–", `łączny czas pracy (osób w zespole: ${people})`) +
        kpi(d.worked_minutes ? `${fmt((d.minutes / d.worked_minutes) * 60)} / ${g} min` : "–", "rozmów na godzinę pracy (średnio)", d.worked_minutes ? (d.minutes / d.worked_minutes) * 60 >= g : null);
      const cols = single
        ? d.by_hour.filter((h) => (h.hour >= 8 && h.hour <= 18) || h.minutes > 0).map((h) => ({
          value: h.minutes, label: `${h.hour}:00`, now: today && h.hour === new Date().getHours(),
          title: `${h.hour}:00–${h.hour + 1}:00 · ${fmt(h.minutes)} całego zespołu`, cls: h.minutes ? "mid" : "zero" }))
        : d.days.map((x) => ({
          value: x.minutes, goal: x.goal, label: d.days.length > 14 ? String(new Date(x.date + "T12:00:00").getDate()) : dayLabel(x.date),
          title: `${dayLabel(x.date)} · zespół: ${fmt(x.minutes)}, powinno być ${fmt(x.goal)} (praca łącznie ${dur(x.worked_minutes)})`,
          cls: !x.goal ? "zero" : x.minutes >= x.goal ? "ok" : "part" }));
      const max = Math.max(1, ...cols.map((c) => Math.max(c.value, c.goal || 0)));
      $("minutes-chart").innerHTML = bars(cols, max * 1.15, 0);
      $("minutes-note").innerHTML = `Suma wszystkich osób w zespole. Każdy ma cel <b>${g} min rozmów na godzinę pracy</b>, więc „powinno być” to suma celów wszystkich (np. 3 osoby po 4 h → 180 min). Szczegóły każdej osoby są w tabeli „Wyniki agentów” poniżej.`;
      return;
    }
    if (single) {
      const now = new Date().getHours();
      const hours = d.by_hour.filter((h) => (h.hour >= 8 && h.hour <= 18) || h.minutes > 0);
      const thisHour = (d.by_hour.find((h) => h.hour === now) || { minutes: 0 }).minutes;
      $("minutes-sum").innerHTML =
        kpi(`${fmt(d.minutes)} / ${fmt(d.goal)}`, "rozmowy / tyle powinno być", dayOk) +
        kpi(d.worked_minutes ? dur(d.worked_minutes) : "–", d.started_at ? `czas pracy, od ${hhmm(d.started_at)}${d.ended_at ? ` do ${hhmm(d.ended_at)}` : ""}` : "czas pracy (jeszcze nie zaczęty)") +
        (today ? kpi(`${fmt(thisHour)} / ${g} min`, "w tej godzinie", thisHour >= g ? true : null) : "");
      const max = Math.max(g * 1.6, ...hours.map((h) => h.minutes));
      $("minutes-chart").innerHTML = bars(hours.map((h) => ({
        value: h.minutes, label: `${h.hour}:00`, now: today && h.hour === now, title: `${h.hour}:00–${h.hour + 1}:00 · ${fmt(h.minutes)}`,
        cls: h.minutes >= g ? "ok" : today && h.hour === now ? "now" : today && h.hour > now ? "future" : h.minutes ? "mid" : "zero",
      })), max, g);
    } else {
      const max = Math.max(...d.days.map((x) => Math.max(x.minutes, x.goal)), g);
      $("minutes-sum").innerHTML =
        kpi(`${fmt(d.minutes)} / ${fmt(d.goal)}`, "rozmowy / tyle powinno być", dayOk) +
        kpi(d.worked_minutes ? dur(d.worked_minutes) : "–", "czas pracy w okresie") +
        kpi(`${d.days.filter((x) => x.goal && x.minutes >= x.goal).length} / ${d.days.filter((x) => x.goal).length}`, "dni z celem dziennym");
      $("minutes-chart").innerHTML = bars(d.days.map((x) => ({
        value: x.minutes, goal: x.goal, label: d.days.length > 14 ? String(new Date(x.date + "T12:00:00").getDate()) : dayLabel(x.date),
        title: `${dayLabel(x.date)} · ${fmt(x.minutes)}, powinno być ${fmt(x.goal)} (praca ${dur(x.worked_minutes)})`,
        cls: !x.goal ? "zero" : x.minutes >= x.goal ? "ok" : "part",
      })), max * 1.15, 0);
    }
    $("minutes-note").innerHTML = d.minutes || d.hours
      ? `Cel: <b>${g} min rozmów na każdą godzinę pracy</b>. Pracujesz 4 h → powinno być 60 min. Czas pracy liczy się od zalogowania (albo pierwszego ruchu w CRM) do wylogowania – przerwy go nie zmniejszają. Lepsza godzina nadrabia słabszą. ${single ? "" : "Przerywany obrys = ile powinno być tego dnia, wypełnienie = ile jest (zielone, gdy cel osiągnięty)."} Liczą się rozmowy z telefonu z aplikacją Solid Dialer.`
      : `Minuty policzą się same, gdy połączysz telefon z aplikacją <b>Solid Dialer</b> (menu konta → „Telefon (dialer)”).`;
  }
  async function load() {
    const q = query(); if (!q) return;
    try {
      const r = await api("/api/work/minutes/?" + q + (isTeam() ? "&scope=team" : ""));
      if (!r.ok) { box.hidden = true; return; }
      box.hidden = false;
      render(await r.json());
    } catch { /* wykres jest dodatkiem */ }
  }
  ["days", "date-from", "date-to"].forEach((id) => { const el = $(id); if (el) el.addEventListener("change", load); });
  document.querySelectorAll("#scope-tabs button").forEach((b) => b.addEventListener("click", () => setTimeout(load, 0)));
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
};

/* Wyniki: „Moje minuty rozmów” dla okresu wybranego u góry strony (Dziś / 7 / 30 / 90 dni / własny).
   Dziś: słupki godzina po godzinie. Dłuższy okres: dzień po dniu.
   Cel jest dzienny: 15 min × godziny pracy (od pierwszego do ostatniego zapisu), więc mocniejsza
   godzina nadrabia słabszą. Dane: GET /api/work/minutes/?days=… albo ?date_from=…&date_to=… */
CRM.initMinutes = function initMinutes() {
  const { api, esc, $ } = CRM;
  const box = $("minutes-card");
  if (!box) return;
  const fmt = (m) => String(Math.round(m * 10) / 10).replace(".", ",");
  const few = (n) => n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20);
  const hoursWord = (n) => `${n} ${n === 1 ? "godzina" : few(n) ? "godziny" : "godzin"}`;
  const dayLabel = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("pl-PL", { weekday: "short", day: "numeric", month: "numeric" });

  function query() {
    const sel = $("days");
    if (sel && sel.value === "custom") {
      const a = $("date-from").value, b = $("date-to").value;
      return a && b ? `date_from=${a}&date_to=${b}` : null;
    }
    return "days=" + (sel ? sel.value : "1");
  }
  const kpi = (big, small, ok) => `<div class="mn-kpi${ok === true ? " ok" : ok === false ? " low" : ""}"><b>${big}</b><span>${small}</span></div>`;

  function bars(cols, max, goalLine) {
    return (goalLine ? `<div class="mn-goal" style="bottom:${(goalLine / max) * 100}%"><span>cel ${goalLine} min</span></div>` : "") +
      cols.map((c) => `<div class="mn-col${c.now ? " is-now" : ""}" title="${esc(c.title)}">
        <span class="mn-val">${c.value ? fmt(c.value) : ""}</span>
        <i class="${c.cls}" style="height:${Math.max(c.value ? 3 : 1, (c.value / max) * 100)}%"></i>
        ${c.goal ? `<b class="mn-tick" style="bottom:${(c.goal / max) * 100}%" aria-hidden="true"></b>` : ""}
        <small>${esc(c.label)}</small></div>`).join("");
  }

  function render(d) {
    const g = d.goal_minutes_per_hour, single = d.days.length === 1, today = d.date_from === new Date().toLocaleDateString("sv-SE");
    const dayOk = d.goal ? d.minutes >= d.goal : null;
    box.querySelector("h2").innerHTML = `Moje minuty rozmów <small class="muted">${single ? (today ? "dziś, godzina po godzinie" : dayLabel(d.date_from)) : "dzień po dniu"}</small>`;
    if (single) {
      const now = new Date().getHours();
      const hours = d.by_hour.filter((h) => (h.hour >= 8 && h.hour <= 18) || h.minutes > 0);
      const thisHour = (d.by_hour.find((h) => h.hour === now) || { minutes: 0 }).minutes;
      $("minutes-sum").innerHTML =
        kpi(`${fmt(d.minutes)} / ${d.goal}`, d.hours ? `min dziś · cel na ${hoursWord(d.hours)} pracy` : "min dziś · cel liczy się od pierwszego telefonu", dayOk) +
        (today ? kpi(`${fmt(thisHour)} / ${g}`, "min w tej godzinie", thisHour >= g ? true : null) : "") +
        kpi(d.hours ? fmt(d.minutes / d.hours) : "0", "średnio min na godzinę", d.hours ? d.minutes / d.hours >= g : null);
      const max = Math.max(g * 1.6, ...hours.map((h) => h.minutes));
      $("minutes-chart").innerHTML = bars(hours.map((h) => ({
        value: h.minutes, label: `${h.hour}:00`, now: today && h.hour === now, title: `${h.hour}:00–${h.hour + 1}:00 · ${fmt(h.minutes)} min`,
        cls: h.minutes >= g ? "ok" : today && h.hour === now ? "now" : today && h.hour > now ? "future" : h.minutes ? "mid" : "zero",
      })), max, g);
    } else {
      const max = Math.max(...d.days.map((x) => Math.max(x.minutes, x.goal)), g);
      $("minutes-sum").innerHTML =
        kpi(`${fmt(d.minutes)} / ${d.goal}`, `min w okresie · cel na ${hoursWord(d.hours)} pracy`, dayOk) +
        kpi(d.hours ? fmt(d.minutes / d.hours) : "0", "średnio min na godzinę pracy", d.hours ? d.minutes / d.hours >= g : null) +
        kpi(`${d.days.filter((x) => x.goal && x.minutes >= x.goal).length} / ${d.days.filter((x) => x.goal).length}`, "dni z celem dziennym");
      $("minutes-chart").innerHTML = bars(d.days.map((x) => ({
        value: x.minutes, goal: x.goal, label: d.days.length > 14 ? String(new Date(x.date + "T12:00:00").getDate()) : dayLabel(x.date),
        title: `${dayLabel(x.date)} · ${fmt(x.minutes)} min z celu ${x.goal} (${hoursWord(x.hours)} pracy)`,
        cls: !x.goal ? "zero" : x.minutes >= x.goal ? "ok" : "mid",
      })), max * 1.1, 0);
    }
    $("minutes-note").innerHTML = d.minutes || d.hours
      ? `Cel: ${g} min rozmów na każdą godzinę pracy (liczoną od pierwszego do ostatniego zapisu w danym dniu). Lepsza godzina nadrabia słabszą – liczy się wynik dnia. ${single ? "" : "Kreska na słupku = cel tego dnia."} Liczą się rozmowy z telefonu połączonego z aplikacją Solid Dialer.`
      : `Minuty policzą się same, gdy połączysz telefon z aplikacją <b>Solid Dialer</b> (menu konta → „Telefon (dialer)”).`;
  }
  async function load() {
    const q = query(); if (!q) return;
    try {
      const r = await api("/api/work/minutes/?" + q);
      if (!r.ok) { box.hidden = true; return; }
      box.hidden = false;
      render(await r.json());
    } catch { /* wykres jest dodatkiem */ }
  }
  ["days", "date-from", "date-to"].forEach((id) => { const el = $(id); if (el) el.addEventListener("change", load); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
};

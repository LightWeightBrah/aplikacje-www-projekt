/* Panel managera: obecność agentów – kto kiedy przyszedł i wyszedł, czas pracy, minuty rozmów
   i ile minut powinno być (15 min na każdą godzinę pracy). API: GET /api/work/attendance/?date= */
CRM.initAttendance = function initAttendance() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  const card = $("att-card");
  if (!card || me.role !== "manager") return;
  card.hidden = false;
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const hhmm = (v) => (v ? new Date(v).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }) : "–");
  const dur = (m) => { const h = Math.floor(m / 60), r = m % 60; return m ? (h ? `${h} h${r ? ` ${r} min` : ""}` : `${r} min`) : "–"; };
  $("att-date").value = iso(new Date());

  async function load() {
    const day = $("att-date").value || iso(new Date());
    const today = day === iso(new Date());
    $("att-list").innerHTML = `<tr><td colspan="7" class="muted">Wczytuję…</td></tr>`;
    const now = Date.now();
    try {
      const r = await api("/api/work/attendance/?date=" + day);
      if (!r.ok) throw new Error();
      const rows = (await r.json()).results;
      const working = rows.filter((x) => x.working_now).length, came = rows.filter((x) => x.started_at).length;
      $("att-sum").textContent = today ? `Dziś w pracy: ${came} z ${rows.length} · teraz pracuje: ${working}` : `Pracowało: ${came} z ${rows.length}`;
      $("att-list").innerHTML = rows.length ? rows.map((x) => {
        const ok = x.goal ? x.minutes >= x.goal : null;
        // przerwa trwa (dziś, bez wylogowania): do 45 min to „przerwa od…”, dłużej – pewnie wyłączył laptopa
        const breakNow = x.on_break_since && now - new Date(x.on_break_since).getTime() < 45 * 60000;
        const end = x.working_now ? `<span class="att-live">pracuje</span>`
          : breakNow ? `<span class="att-break">przerwa od ${hhmm(x.on_break_since)}</span>`
          : x.ended_at ? hhmm(x.ended_at)
          : x.started_at ? `<span class="muted" title="Nie wylogował się – to jego ostatni ruch w CRM">${hhmm(x.last_seen)}?</span>` : "–";
        const brList = (x.breaks || []).map(([a, b]) => `${hhmm(a)}–${hhmm(b)}`).join(", ");
        const brs = x.break_minutes ? `<span title="${esc(brList)}">${dur(x.break_minutes)} <small class="muted">(${x.breaks.length})</small></span>` : "–";
        return `<tr class="${x.started_at ? "" : "att-off"}"><td><strong>${esc(x.full_name)}</strong></td>
          <td>${x.started_at ? hhmm(x.started_at) : `<span class="muted">nie pracował</span>`}</td><td>${end}</td>
          <td class="num">${dur(x.worked_minutes)}</td><td class="num">${brs}</td><td class="num">${x.calls}</td>
          <td class="num"><b class="${ok === true ? "att-ok" : ok === false ? "att-low" : ""}">${CRM.talk(x.minutes)}</b> / ${x.goal} min</td></tr>`;
      }).join("") : `<tr><td colspan="7" class="muted">Brak agentów.</td></tr>`;
    } catch {
      $("att-list").innerHTML = `<tr><td colspan="7" class="form-error">Nie udało się pobrać obecności.</td></tr>`;
    }
  }
  $("att-date").addEventListener("change", load);
  $("att-today").addEventListener("click", () => { $("att-date").value = iso(new Date()); load(); });
  load();
  setInterval(() => { if (!document.hidden && $("att-date").value === iso(new Date())) load(); }, 60000);
};

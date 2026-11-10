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
    // początek pracy = pierwszy telefon / „Następny klient”; gdy zalogował się dużo wcześniej, widać to pod spodem
    const loginNote = (x) => x.login_at && x.started_at && new Date(x.started_at) - new Date(x.login_at) >= 10 * 60000
      ? `<br><small class="muted" title="Samo zalogowanie nie liczy się jako praca">zalogowany ${hhmm(x.login_at)}</small>` : "";
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
        // „Przerwy” = krótkie (15–45 min, liczą się do pracy); dłuższe wyjścia są w wierszu „Pracował” pod spodem
        const shortBr = (x.breaks || []).filter(([a, b]) => new Date(b) - new Date(a) < 45 * 60000);
        const shortMin = (x.break_minutes || 0) - (x.away_minutes || 0);
        const brs = shortMin > 0 ? `<span title="${esc(shortBr.map(([a, b]) => `${hhmm(a)}–${hhmm(b)}`).join(", "))}">${dur(shortMin)} <small class="muted">(${shortBr.length})</small></span>` : "–";
        // kilka bloków pracy w ciągu dnia (np. 3 h pracy, 3 h poza pracą, 3 h pracy): każdy z godzinami
        const blocks = x.blocks || [];
        const blockRow = blocks.length > 1 ? `<tr class="att-blocks"><td></td><td colspan="6">Pracował: ${blocks.map(([a, b]) => `<b>${hhmm(a)}–${b ? hhmm(b) : "teraz"}</b>`).join(" · ")}
          <span class="muted">· poza pracą ${dur(x.away_minutes)} – nie liczy się do czasu pracy ani celu</span></td></tr>` : "";
        return `<tr class="${x.started_at ? "" : "att-off"}"><td><strong>${esc(x.full_name)}</strong></td>
          <td>${x.started_at ? hhmm(x.started_at) : `<span class="muted">${x.login_at ? "zalogował się, nie pracował" : "nie pracował"}</span>`}${loginNote(x)}</td><td>${end}</td>
          <td class="num">${dur(x.worked_minutes)}</td><td class="num">${brs}</td><td class="num">${x.calls}</td>
          <td class="num"><b class="${ok === true ? "att-ok" : ok === false ? "att-low" : ""}">${CRM.talk(x.minutes)}</b> / ${CRM.talk(x.goal)}</td></tr>${blockRow}`;
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

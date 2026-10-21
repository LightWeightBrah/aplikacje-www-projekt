/* Ranking zespołu: GET /api/leaderboard/ (403, gdy manager wyłączył agentowi podgląd)
   oraz przełączniki dostępu dla managera: PATCH /api/agents/{id}/. */
CRM.initRanking = function initRanking() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  const manager = me.role === "manager";
  const UNIT = { sold: "sprzedanych", calls: "rozmów", interested: "zainteresowanych" };
  let sort = "sold";

  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  $("date-from").value = iso(new Date());
  $("date-to").value = iso(new Date());
  const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");
  const day = (v) => new Date(v + "T12:00:00").toLocaleDateString("pl-PL");

  function periodQuery() {
    if ($("days").value !== "custom") return "days=" + $("days").value;
    return "date_from=" + $("date-from").value + "&date_to=" + $("date-to").value;
  }

  function showLocked(message) {
    $("locked-msg").textContent = message || "Podgląd wyników innych agentów jest wyłączony przez managera.";
    $("locked").hidden = false;
    $("ranking-area").hidden = true;
    $("subtitle").textContent = "Podgląd wyłączony";
    document.querySelectorAll("[data-team-only]").forEach((el) => { el.hidden = true; });
  }

  function renderChampion(rows, d) {
    const today = d.date_from === d.date_to && d.date_from === iso(new Date());
    const when = today ? "Dziś" : "W tym okresie";
    const top = rows.filter((r) => r.rank === 1 && r[d.sort] > 0);
    $("champion").textContent = top.length
      ? `${when} prowadzi: ${top.map((r) => r.full_name).join(", ")} – ${top[0][d.sort]} ${UNIT[d.sort]}.`
      : `${when} nikt jeszcze nie ma wyniku w tej kategorii. Pierwszy wpis zajmie pierwsze miejsce.`;
  }

  function renderTable(rows, d) {
    const col = (key, label) => `<th class="num${d.sort === key ? " sorted" : ""}">${label}</th>`;
    const cell = (r, key) => `<td class="num${d.sort === key ? " sorted" : ""}">${key === "answer_rate" ? pct(r[key]) : r[key]}</td>`;
    $("rank-table").innerHTML =
      `<thead><tr><th>Miejsce</th><th>Agent</th>${col("sold", "Sprzedane")}${col("calls", "Rozmowy")}` +
      `${col("interested", "Zainteresowani")}<th class="num">Dodzwonienia</th></tr></thead><tbody>` +
      (rows.length ? rows.map((r) => {
        const medal = r.rank <= 3 && r[d.sort] > 0 ? ` medal m${r.rank}` : "";
        return `<tr class="${r.is_me ? "me" : ""}"><td><span class="place${medal}">${r.rank}</span></td>
          <td><strong>${esc(r.full_name)}</strong>${r.is_me ? ' <small class="you">to Ty</small>' : ""}</td>
          ${cell(r, "sold")}${cell(r, "calls")}${cell(r, "interested")}${cell(r, "answer_rate")}</tr>`;
      }).join("") : `<tr><td colspan="6" class="muted">Brak agentów do pokazania.</td></tr>`) + "</tbody>";
  }

  async function load() {
    $("page-error").hidden = true;
    $("range").hidden = $("days").value !== "custom";
    if ($("days").value === "custom" && !($("date-from").value && $("date-to").value)) return;
    try {
      const r = await api(`/api/leaderboard/?${periodQuery()}&sort=${sort}`);
      if (r.status === 403) {
        const body = await r.json().catch(() => ({}));
        showLocked(body.detail);
        return;
      }
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || "Nie udało się pobrać rankingu (" + r.status + ").");
      }
      const d = await r.json();
      $("locked").hidden = true;
      $("ranking-area").hidden = false;
      $("subtitle").textContent = "Wyniki agentów · " +
        (d.date_from === d.date_to ? (d.date_from === iso(new Date()) ? "dziś" : day(d.date_from))
          : day(d.date_from) + " – " + day(d.date_to));
      renderChampion(d.results, d);
      renderTable(d.results, d);
    } catch (ex) {
      $("page-error").textContent = ex.message;
      $("page-error").hidden = false;
    }
  }

  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
    sort = b.dataset.sort;
    document.querySelectorAll("#tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    load();
  }));
  ["days", "date-from", "date-to"].forEach((id) => $(id).addEventListener("change", load));

  /* ---- Manager: kto może widzieć wyniki innych ---- */
  async function initAccess() {
    $("access-card").hidden = false;
    const list = $("access-list");
    try {
      const r = await api("/api/agents/");
      if (!r.ok) throw new Error();
      const agents = (await r.json()).filter((a) => a.role === "agent");
      if (!agents.length) { list.innerHTML = `<p class="muted">Brak agentów.</p>`; return; }
      list.innerHTML = agents.map((a) => `<div class="access-row" data-id="${a.id}">
        <span class="who"><strong>${esc(a.full_name)}</strong><small class="muted">${esc(a.username)}</small></span>
        <span class="saved muted" aria-live="polite"></span>
        <label class="switch"><input type="checkbox" ${a.can_view_team_stats ? "checked" : ""}
          aria-label="Podgląd wyników innych: ${esc(a.full_name)}"><i></i></label></div>`).join("");
    } catch {
      list.innerHTML = `<p class="form-error">Nie udało się pobrać listy agentów.</p>`;
      return;
    }
    list.addEventListener("change", async (e) => {
      const box = e.target;
      const row = box.closest(".access-row");
      const note = row.querySelector(".saved");
      const wanted = box.checked;
      box.disabled = true;
      try {
        const r = await api(`/api/agents/${row.dataset.id}/`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ can_view_team_stats: wanted }),
        });
        if (!r.ok) throw new Error();
        note.textContent = wanted ? "Zapisano: widzi ranking" : "Zapisano: ranking wyłączony";
      } catch {
        box.checked = !wanted;
        note.textContent = "Nie udało się zapisać";
      }
      box.disabled = false;
      setTimeout(() => { note.textContent = ""; }, 2500);
    });
  }

  if (manager) initAccess();
  load();
};

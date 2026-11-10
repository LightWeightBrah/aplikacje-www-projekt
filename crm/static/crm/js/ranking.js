/* Ranking zespołu: GET /api/leaderboard/ (403, gdy manager wyłączył agentowi podgląd)
   oraz przełączniki dostępu dla managera: PATCH /api/agents/{id}/. */
CRM.initRanking = function initRanking() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  const manager = me.role === "manager";
  const FORMS = { sold: ["sprzedaż", "sprzedaże", "sprzedaży"], calls: ["rozmowa", "rozmowy", "rozmów"], interested: ["zainteresowany", "zainteresowanych", "zainteresowanych"] };
  const few = (n) => n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20);
  const unitFor = (key, n) => (key === "minutes" ? "min rozmów" : FORMS[key] ? FORMS[key][n === 1 ? 0 : Number.isInteger(n) && few(n) ? 1 : 2] : "");
  const MONEY = ["revenue", "commission"];
  let sort = "minutes";

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
    const best = top.length ? score(top[0], d.sort) : "";
    $("champion").textContent = top.length
      ? `${when} prowadzi: ${top.map((r) => r.full_name).join(", ")} – ${best}.`
      : `${when} nikt jeszcze nie ma wyniku w tej kategorii. Pierwszy wpis zajmie pierwsze miejsce.`;
  }

  function avatarHtml(r, cls = "") {
    const style = r.avatar ? ` style="background-image:url('${esc(r.avatar)}')"` : "";
    return `<button type="button" class="avatar av-rank${cls}${r.avatar ? " has-img" : ""}"${style} data-photo="${esc(r.username)}" title="Pokaż zdjęcie: ${esc(r.full_name)}">${r.avatar ? "" : esc(r.full_name.charAt(0).toUpperCase())}</button>`;
  }
  let lastRows = [];
  const score = (r, key) => (MONEY.includes(key) ? CRM.money(r[key]) : key === "minutes" ? `${CRM.talk(r[key])} rozmów` : `${r[key]} ${unitFor(key, r[key])}`);
  function renderPodium(rows, d) {
    const top = rows.filter((r) => r.rank <= 3 && r[d.sort] > 0).slice(0, 3);
    $("podium").hidden = !top.length;
    // zawsze trzy miejsca: 2 po lewej, 1 w środku, 3 po prawej (puste miejsce zostaje niewidoczne)
    const order = [top[1], top[0], top[2]];
    $("podium").innerHTML = order.map((r, i) => (r ? `<div class="pod pod${i === 1 ? 1 : i === 0 ? 2 : 3}${r.is_me ? " me" : ""}">${avatarHtml(r, " av-pod")}
      <span class="pod-place">${r.rank}</span><strong>${esc(r.full_name)}</strong><span class="pod-score">${esc(score(r, d.sort))}</span></div>`
      : `<div class="pod pod-empty" aria-hidden="true"></div>`)).join("");
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-photo]"); if (!b) return;
    const r = lastRows.find((x) => x.username === b.dataset.photo); if (!r) return;
    const big = $("photo-big");
    big.style.backgroundImage = r.avatar ? `url('${r.avatar}')` : "";
    big.textContent = r.avatar ? "" : r.full_name.charAt(0).toUpperCase();
    big.classList.toggle("has-img", !!r.avatar);
    $("photo-name").textContent = r.full_name;
    $("photo-dialog").showModal();
  });

  function renderTable(rows, d) {
    const col = (key, label) => `<th class="num${d.sort === key ? " sorted" : ""}">${label}</th>`;
    const show = (r, key) => (key === "answer_rate" ? pct(r[key]) : MONEY.includes(key) ? CRM.money(r[key]) : key === "minutes" ? CRM.talk(r[key]) : r[key]);
    const cell = (r, key) => `<td class="num${d.sort === key ? " sorted" : ""}">${esc(show(r, key))}</td>`;
    $("rank-table").innerHTML =
      `<thead><tr><th>Miejsce</th><th>Agent</th>${col("minutes", "Minuty")}${col("calls", "Rozmowy")}${col("interested", "Zainteresowani")}` +
      `<th class="num">Dodzwonienia</th>${col("sold", "Sprzedane").replace('class="num', 'class="num grp')}${col("commission", "Prowizja")}</tr></thead><tbody>` +
      (rows.length ? rows.map((r) => {
        const medal = r.rank <= 3 && r[d.sort] > 0 ? ` medal m${r.rank}` : "";
        return `<tr class="${r.is_me ? "me" : ""}"><td><span class="place${medal}">${r.rank}</span></td>
          <td><span class="who-cell">${avatarHtml(r)}<strong>${esc(r.full_name)}</strong>${r.is_me ? ' <small class="you">to Ty</small>' : ""}</span></td>
          ${cell(r, "minutes")}${cell(r, "calls")}${cell(r, "interested")}${cell(r, "answer_rate")}${cell(r, "sold").replace('class="num', 'class="num grp')}${cell(r, "commission")}</tr>`;
      }).join("") : `<tr><td colspan="7" class="muted">Brak agentów do pokazania.</td></tr>`) + "</tbody>";
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
      lastRows = d.results;
      renderChampion(d.results, d);
      renderPodium(d.results, d);
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
      const all = await r.json();
      const agents = all.filter((a) => a.role === "agent");
      const managers = all.filter((a) => a.role === "manager");
      const row = (a, field, label) => `<div class="access-row" data-id="${a.id}" data-field="${field}">
        <span class="who"><strong>${esc(a.full_name)}${a.id === me.id ? ' <small class="you">to Ty</small>' : ""}</strong><small class="muted">${esc(a.username)}</small></span>
        <span class="saved muted" aria-live="polite"></span>
        <label class="switch"><input type="checkbox" ${a[field] ? "checked" : ""}
          aria-label="${label}: ${esc(a.full_name)}"><i></i></label></div>`;
      list.innerHTML = (agents.length ? agents.map((a) => row(a, "can_view_team_stats", "Podgląd wyników innych")).join("") : `<p class="muted">Brak agentów.</p>`);
      $("managers-list").innerHTML = managers.map((a) => row(a, "show_in_ranking", "Pokazuj w rankingu")).join("");
      $("managers-block").hidden = !managers.length;
    } catch {
      list.innerHTML = `<p class="form-error">Nie udało się pobrać listy agentów.</p>`;
      return;
    }
    /* Jeden przycisk dla wszystkich agentów: POST /api/agents/team-access/. */
    async function setAll(value) {
      const msg = $("access-msg");
      if (!value && !confirm("Wyłączyć podgląd rankingu i wyników innych wszystkim agentom?")) return;
      ["bulk-off", "bulk-on"].forEach((id) => { $(id).disabled = true; });
      try {
        const r = await api("/api/agents/team-access/", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ can_view_team_stats: value }),
        });
        if (!r.ok) throw new Error();
        list.querySelectorAll("input[type=checkbox]").forEach((b) => { b.checked = value; });
        msg.textContent = (value ? "Ranking włączony" : "Ranking wyłączony") + " dla wszystkich agentów.";
      } catch {
        msg.textContent = "Nie udało się zapisać zmiany.";
      }
      ["bulk-off", "bulk-on"].forEach((id) => { $(id).disabled = false; });
    }
    $("bulk-off").addEventListener("click", () => setAll(false));
    $("bulk-on").addEventListener("click", () => setAll(true));

    async function onToggle(e) {
      const box = e.target;
      const row = box.closest(".access-row");
      const note = row.querySelector(".saved");
      const field = row.dataset.field;
      const wanted = box.checked;
      box.disabled = true;
      try {
        const r = await api(`/api/agents/${row.dataset.id}/`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [field]: wanted }),
        });
        if (!r.ok) throw new Error();
        note.textContent = field === "show_in_ranking"
          ? (wanted ? "Zapisano: jest w rankingu" : "Zapisano: ukryty w rankingu")
          : (wanted ? "Zapisano: widzi ranking" : "Zapisano: ranking wyłączony");
        if (field === "show_in_ranking") load();
      } catch {
        box.checked = !wanted;
        note.textContent = "Nie udało się zapisać";
      }
      box.disabled = false;
      setTimeout(() => { note.textContent = ""; }, 2500);
    }
    list.addEventListener("change", onToggle);
    $("managers-list").addEventListener("change", onToggle);
  }

  if (manager) initAccess();
  load();
};

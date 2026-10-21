/* Panel managera: tabela leadów z filtrami, przypisywanie, przepisywanie, pula nieosiągalnych, eksport. */
CRM.initManager = function initManager() {
  const { api, esc, STATUS, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager") {
    $("page-error").textContent = "Ten panel jest dostępny tylko dla managera.";
    $("page-error").hidden = false;
    document.querySelectorAll(".page-head .head-actions, #tabs, .filters, .table-card").forEach((el) => { el.hidden = true; });
    return;
  }

  const OUTCOME = {
    no_answer: "Nie odebrał", callback: "Prosił o oddzwonienie", interested: "Zainteresowany",
    refused: "Odmowa", wrong_number: "Zły numer", note: "Notatka",
  };
  const state = { total: null, tab: "all", page: 1, ordering: "", search: "", count: 0, pageSize: 20 };
  let agents = [], leads = [], selected = new Set(), openId = null, searchTimer;

  const chip = (code) => {
    const [label, color] = STATUS[code] || [code, "#64748b"];
    return `<span class="chip" style="--c:${color}">${esc(label)}</span>`;
  };
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("pl-PL",
    { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–");
  function rel(iso) {
    const m = Math.round((new Date(iso) - Date.now()) / 60000), a = Math.abs(m);
    const t = a < 60 ? `${a} min` : a < 1440 ? `${Math.round(a / 60)} h`
      : Math.round(a / 1440) === 1 ? "1 dzień" : `${Math.round(a / 1440)} dni`;
    return m < 0 ? `${t} temu` : `za ${t}`;
  }
  let toastTimer;
  function toast(msg, isError) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
  }
  function showError(msg) {
    $("page-error").textContent = msg || "";
    $("page-error").hidden = !msg;
  }
  async function readJson(r) { try { return await r.json(); } catch { return {}; } }
  async function fetchAll(path) {
    const out = [];
    let url = path;
    while (url) {
      const r = await api(url);
      if (!r.ok) throw new Error("Nie udało się wczytać danych (" + r.status + ").");
      const d = await r.json();
      if (Array.isArray(d)) return d;
      out.push(...d.results);
      url = d.next ? d.next.replace(location.origin, "") : null;
    }
    return out;
  }
  const agentName = (id) => (agents.find((a) => a.id === id) || {}).username || (id ? "#" + id : "pula");

  /* ---------- filtry i zapytanie ---------- */
  function query(withPage = true) {
    const p = new URLSearchParams();
    if (withPage) p.set("page", state.page);
    if (state.search) p.set("search", state.search);
    if (state.ordering) p.set("ordering", state.ordering);
    if (state.tab === "pool") p.set("pool", "true");
    if (state.tab === "overdue") { p.set("overdue", "true"); if (!state.ordering) p.set("ordering", "next_contact_at"); }
    if (state.tab === "unreachable") p.set("status", "unreachable");
    else if ($("f-status").value) p.set("status", $("f-status").value);
    if (state.tab !== "pool" && $("f-owner").value) {
      if ($("f-owner").value === "pool") p.set("pool", "true");
      else p.set("owner", $("f-owner").value);
    }
    if ($("f-product").value) p.set("product", $("f-product").value);
    if ($("f-batch").value) p.set("batch", $("f-batch").value);
    return p.toString();
  }

  function hasFilters() {
    return state.tab !== "all" || !!state.search || ["f-status", "f-owner", "f-product", "f-batch"].some((id) => $(id).value);
  }
  async function loadTotal() {
    const r = await api("/api/leads/");
    if (r.ok) { state.total = (await r.json()).count; renderTotals(); }
  }
  function renderTotals() {
    const n = (v) => (v == null ? "…" : v);
    const filtered = hasFilters();
    $("totals").innerHTML = `<span>W bazie: <b>${n(state.total)}</b></span>` +
      `<span class="${filtered ? "filtered" : ""}">${filtered ? "Pasuje do filtrów" : "Wyświetlane"}: <b>${state.count}</b></span>`;
  }

  async function load() {
    showError("");
    try {
      const r = await api("/api/leads/?" + query());
      if (r.status === 404 && state.page > 1) { state.page = 1; return load(); }
      if (!r.ok) throw new Error("Nie udało się pobrać leadów (" + r.status + ").");
      const d = await r.json();
      leads = d.results;
      state.count = d.count;
      if (!hasFilters()) state.total = d.count;
      if (d.results.length) state.pageSize = Math.max(state.pageSize, d.results.length);
      selected = new Set([...selected].filter((id) => leads.some((l) => l.id === id)));
      render();
    } catch (ex) { showError(ex.message); }
  }

  function render() {
    const body = $("leads-body");
    if (!leads.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty muted">Brak leadów dla wybranych filtrów.</td></tr>`;
    } else {
      body.innerHTML = leads.map((l) => {
        const late = l.next_contact_at && new Date(l.next_contact_at) < new Date()
          && ["callback", "interested", "offer"].includes(l.status);
        return `<tr data-id="${l.id}" class="${l.id === openId ? "open" : ""}">
          <td class="chk"><input type="checkbox" data-check="${l.id}" ${selected.has(l.id) ? "checked" : ""} aria-label="Zaznacz leada"></td>
          <td><strong>${esc(l.contact_name)}</strong>${l.company_name
            ? `<small class="sub">${esc(l.company_name)}${l.company_nip ? " · NIP " + esc(l.company_nip) : ""}</small>` : ""}</td>
          <td class="nowrap">${esc(l.phone)}</td>
          <td>${chip(l.status)}</td>
          <td>${l.owner_name ? esc(l.owner_name) : '<span class="muted">pula</span>'}${l.transferred_from ? `<small class="sub">od ${esc(l.transferred_from)}</small>` : ""}</td>
          <td>${l.product_name ? esc(l.product_name) : '<span class="muted">–</span>'}</td>
          <td class="num">${l.attempts_count}</td>
          <td class="nowrap ${late ? "late" : ""}">${l.next_contact_at
            ? `${esc(rel(l.next_contact_at))}<small class="sub">${esc(fmt(l.next_contact_at))}${l.next_contact_note ? " · " + esc(l.next_contact_note) : ""}</small>`
            : '<span class="muted">–</span>'}</td>
        </tr>`;
      }).join("");
    }
    renderTotals();
    const pages = Math.max(1, Math.ceil(state.count / state.pageSize));
    $("pager-info").textContent = state.count
      ? `Strona ${state.page} z ${pages} · ${state.count} ${state.count === 1 ? "lead" : "leadów"}` : "";
    $("prev-page").disabled = state.page <= 1;
    $("next-page").disabled = state.page >= pages;
    $("check-all").checked = leads.length > 0 && leads.every((l) => selected.has(l.id));
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      const f = th.dataset.sort;
      th.classList.toggle("sorted", state.ordering === f || state.ordering === "-" + f);
      th.dataset.dir = state.ordering === f ? "asc" : state.ordering === "-" + f ? "desc" : "";
    });
    renderBulk();
  }

  function renderBulk() {
    $("bulk-bar").hidden = selected.size === 0;
    $("bulk-count").textContent = `Zaznaczono: ${selected.size}`;
  }

  /* ---------- zakładki, pula, nieosiągalni ---------- */
  function setTab(tab) {
    state.tab = tab; state.page = 1; selected.clear();
    document.querySelectorAll("#tabs [data-tab]").forEach((b) =>
      b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    $("f-status").disabled = tab === "unreachable";
    $("f-owner").disabled = tab === "pool";
    $("unr-bar").hidden = tab !== "unreachable";
    $("late-bar").hidden = tab !== "overdue";
    load();
  }

  async function loadCounts() {
    try {
      const [pool, unr, ready, late] = await Promise.all([
        api("/api/leads/?pool=true&status=new").then((r) => r.json()),
        api("/api/leads/unreachable/").then((r) => r.json()),
        api("/api/leads/unreachable/?ready=true").then((r) => r.json()),
        api("/api/leads/?overdue=true").then((r) => r.json()),
      ]);
      $("late-count").textContent = late.count != null ? late.count : "";
      $("tabs").querySelector("[data-tab=overdue]").classList.toggle("alert", late.count > 0);
      $("pool-count").textContent = pool.count != null ? pool.count : "";
      $("unr-count").textContent = unr.count != null ? unr.count : "";
      const n = ready.count || 0;
      $("unr-text").textContent = n
        ? `${n} nieosiągalnych leadów czeka już wystarczająco długo i może wrócić do puli.`
        : "Nieosiągalne leady wracają do puli dopiero po ustalonym czasie (domyślnie 30 dni od ostatniej próby). Na razie żaden nie czeka wystarczająco długo.";
      $("recycle-btn").disabled = !n;
    } catch { /* liczniki są dodatkiem */ }
  }

  async function recycle() {
    const btn = $("recycle-btn");
    btn.disabled = true;
    const r = await api("/api/leads/recycle-unreachable/", { method: "POST" });
    const d = await readJson(r);
    if (!r.ok) { toast(d.detail || "Nie udało się zwrócić leadów.", true); btn.disabled = false; return; }
    toast(d.recycled ? `Zwrócono do puli: ${d.recycled}.` : "Brak leadów do zwrotu.");
    await Promise.all([load(), loadCounts()]);
  }

  /* ---------- przypisywanie ---------- */
  async function assign(id, agent, reason) {
    const r = await api(`/api/leads/${id}/assign/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent, reason: reason || "" }),
    });
    const d = await readJson(r);
    if (!r.ok) throw new Error(d.detail || (d.agent && d.agent[0]) || "Nie udało się przypisać leada.");
    return d;
  }

  async function bulkAssign(agent) {
    const ids = [...selected];
    if (!ids.length) return;
    $("bulk-assign").disabled = $("bulk-pool").disabled = true;
    let ok = 0; const problems = [];
    for (const id of ids) {
      try { await assign(id, agent, "Przypisanie z panelu managera"); ok++; }
      catch (ex) {
        const l = leads.find((x) => x.id === id);
        problems.push(`${l ? l.contact_name : "#" + id}: ${ex.message}`);
      }
    }
    $("bulk-assign").disabled = $("bulk-pool").disabled = false;
    selected.clear();
    const target = agent ? agentName(agent) : "puli";
    if (problems.length) {
      toast(`Przypisano ${ok} z ${ids.length}. Pominięto: ${problems.slice(0, 2).join("; ")}${problems.length > 2 ? "…" : ""}`, ok === 0);
    } else toast(`Gotowe: ${ok} ${ok === 1 ? "lead" : "leadów"} → ${target}.`);
    await Promise.all([load(), loadCounts(), loadAgents()]);
  }

  /* ---------- szczegóły leada ---------- */
  async function openLead(id) {
    openId = id;
    $("drawer").hidden = $("drawer-back").hidden = false;
    $("drawer-body").innerHTML = `<p class="muted">Ładowanie…</p>`;
    document.querySelectorAll("#leads-body tr").forEach((tr) => tr.classList.toggle("open", Number(tr.dataset.id) === id));
    const r = await api(`/api/leads/${id}/`);
    if (!r.ok) { $("drawer-body").innerHTML = `<p class="form-error">Nie udało się wczytać leada.</p>`; return; }
    drawLead(await r.json());
  }

  function drawLead(l) {
    const closed = ["won", "lost", "do_not_call"].includes(l.status);
    const options = agents.map((a) => `<option value="${a.id}" ${a.id === l.owner ? "selected" : ""}>${esc(a.username)}${a.role === "manager" ? " (manager)" : ""}</option>`).join("");
    const acts = (l.activities || []).map((a) => `<li><strong>${esc(OUTCOME[a.outcome] || a.outcome)}</strong>
      <span class="who">· ${esc(a.agent_name || "–")}</span><time>${esc(fmt(a.created_at))}</time>
      ${a.note ? `<p>${esc(a.note)}</p>` : ""}</li>`).join("") || `<li class="muted">Brak rozmów.</li>`;
    const moves = (l.assignments || []).map((a) => `<li>${esc(agentName(a.from_agent))} → <strong>${esc(agentName(a.to_agent))}</strong>
      <time>${esc(fmt(a.assigned_at))}</time>${a.reason ? `<p>${esc(a.reason)}</p>` : ""}</li>`).join("") || `<li class="muted">Brak zmian opiekuna.</li>`;
    $("drawer-body").innerHTML = `
      <h2 class="drawer-title">${esc(l.contact_name)}</h2>
      <p class="muted">${l.company_name ? esc(l.company_name) : "Bez firmy"}${l.company_nip ? " · NIP " + esc(l.company_nip) : ""}</p>
      <div class="drawer-chips">${chip(l.status)}</div>
      <dl class="kv">
        <div><dt>Telefon</dt><dd><a href="tel:${esc(l.phone)}">${esc(l.phone)}</a></dd></div>
        <div><dt>E-mail</dt><dd>${l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` : "–"}</dd></div>
        <div><dt>Produkt</dt><dd>${esc(l.product_name || "–")}</dd></div>
        <div><dt>Baza</dt><dd>${esc(l.batch_name || "–")}</dd></div>
        <div><dt>Próby</dt><dd>${l.attempts_count}</dd></div>
        <div><dt>Ostatnia aktywność</dt><dd>${esc(fmt(l.last_activity_at))}</dd></div>
        <div class="wide"><dt>Następny kontakt</dt><dd>${l.next_contact_at ? esc(fmt(l.next_contact_at)) + " (" + esc(rel(l.next_contact_at)) + ")" : "–"}${l.next_contact_note ? `<br><span class="muted">${esc(l.next_contact_note)}</span>` : ""}</dd></div>
      </dl>
      <div class="assign-box">
        <label class="field">Opiekun leada
          <select id="d-agent" class="select" ${closed ? "disabled" : ""}><option value="">Pula (bez agenta)</option>${options}</select>
        </label>
        <button type="button" class="btn btn-primary" id="d-assign" ${closed ? "disabled" : ""}>Zapisz</button>
      </div>
      ${closed ? `<p class="hint">Zamkniętego leada nie można przepisać.</p>` : ""}
      <h3>Historia kontaktów</h3><ul class="history">${acts}</ul>
      <h3>Historia opiekunów</h3><ul class="history">${moves}</ul>`;
    const btn = $("d-assign");
    if (btn) btn.addEventListener("click", async () => {
      const v = $("d-agent").value;
      btn.disabled = true;
      try {
        await assign(l.id, v ? Number(v) : null, "Przypisanie z panelu managera");
        toast("Zapisano opiekuna.");
        await Promise.all([load(), loadCounts(), loadAgents()]);
        openLead(l.id);
      } catch (ex) { toast(ex.message, true); btn.disabled = false; }
    });
  }

  function closeDrawer() {
    openId = null;
    $("drawer").hidden = $("drawer-back").hidden = true;
    document.querySelectorAll("#leads-body tr.open").forEach((tr) => tr.classList.remove("open"));
  }

  /* ---------- przepisywanie wszystkich leadów agenta ---------- */
  function openReassign() {
    const opts = agents.map((a) => `<option value="${a.id}">${esc(a.username)} (aktywne: ${a.active_leads})</option>`).join("");
    $("re-from").innerHTML = `<option value="" selected>Wybierz agenta…</option>` + opts;
    $("re-to").innerHTML = `<option value="" selected>Wybierz agenta…</option>` + opts;
    $("re-reason").value = ""; $("re-error").hidden = true;
    $("reassign-dialog").showModal();
  }
  async function submitReassign(e) {
    e.preventDefault();
    const from = Number($("re-from").value), to = Number($("re-to").value);
    $("re-error").hidden = true;
    if (!from || !to) { $("re-error").textContent = "Wybierz obu agentów."; $("re-error").hidden = false; return; }
    if (from === to) { $("re-error").textContent = "Agenci muszą być różni."; $("re-error").hidden = false; return; }
    $("re-ok").disabled = true;
    const r = await api("/api/leads/reassign/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_agent: from, to_agent: to, reason: $("re-reason").value.trim() }),
    });
    const d = await readJson(r);
    $("re-ok").disabled = false;
    if (!r.ok) {
      $("re-error").textContent = d.detail || (d.non_field_errors && d.non_field_errors[0]) || "Nie udało się przepisać leadów.";
      $("re-error").hidden = false; return;
    }
    $("reassign-dialog").close();
    toast(`Przepisano leadów: ${d.reassigned} (${agentName(from)} → ${agentName(to)}).`);
    await Promise.all([load(), loadCounts(), loadAgents()]);
  }

  /* ---------- eksport ---------- */
  async function exportXlsx() {
    const btn = $("export-btn");
    btn.disabled = true;
    try {
      const r = await api("/api/leads/export/?" + query(false));
      if (!r.ok) throw new Error("Eksport nie powiódł się (" + r.status + ").");
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "leady.xlsx" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast("Pobrano leady.xlsx (z bieżącymi filtrami).");
    } catch (ex) { toast(ex.message, true); }
    btn.disabled = false;
  }

  /* ---------- słowniki (agenci, produkty, bazy) ---------- */
  function fillAgentSelects() {
    const cur = $("f-owner").value;
    $("f-owner").innerHTML = `<option value="">Każdy agent</option><option value="pool">Pula (bez agenta)</option>` +
      agents.map((a) => `<option value="${a.id}">${esc(a.username)}${a.overdue_leads ? ` · zaległe: ${a.overdue_leads}` : ""}</option>`).join("");
    $("f-owner").value = cur;
    $("bulk-agent").innerHTML = agents.map((a) => `<option value="${a.id}">${esc(a.username)}${a.role === "manager" ? " (manager)" : ""}</option>`).join("");
  }
  async function loadAgents() {
    const r = await api("/api/agents/");
    if (r.ok) { agents = await r.json(); fillAgentSelects(); }
  }
  async function loadDictionaries() {
    await loadAgents();
    $("f-status").innerHTML = `<option value="">Każdy status</option>` +
      Object.entries(STATUS).map(([c, [l]]) => `<option value="${c}">${esc(l)}</option>`).join("");
    const [products, batches] = await Promise.all([fetchAll("/api/products/"), fetchAll("/api/batches/")]);
    $("f-product").innerHTML = `<option value="">Każdy produkt</option>` + products.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    $("f-batch").innerHTML = `<option value="">Każda baza</option>` + batches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
  }

  /* ---------- zdarzenia ---------- */
  function refilter() { state.page = 1; selected.clear(); load(); }
  document.querySelectorAll("#tabs [data-tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  ["f-status", "f-owner", "f-product", "f-batch"].forEach((id) => $(id).addEventListener("change", refilter));
  $("f-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.search = $("f-search").value.trim(); refilter(); }, 300);
  });
  $("f-clear").addEventListener("click", () => {
    ["f-status", "f-owner", "f-product", "f-batch"].forEach((id) => { $(id).value = ""; });
    $("f-search").value = ""; state.search = ""; state.ordering = ""; refilter();
  });
  $("prev-page").addEventListener("click", () => { state.page--; load(); });
  $("next-page").addEventListener("click", () => { state.page++; load(); });
  document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const f = th.dataset.sort;
    state.ordering = state.ordering === f ? "-" + f : f;
    state.page = 1; load();
  }));
  $("leads-body").addEventListener("click", (e) => {
    const box = e.target.closest("[data-check]");
    if (box) {
      const id = Number(box.dataset.check);
      box.checked ? selected.add(id) : selected.delete(id);
      $("check-all").checked = leads.every((l) => selected.has(l.id));
      renderBulk(); return;
    }
    const tr = e.target.closest("tr[data-id]");
    if (tr && !e.target.closest("a")) openLead(Number(tr.dataset.id));
  });
  $("check-all").addEventListener("change", (e) => {
    leads.forEach((l) => (e.target.checked ? selected.add(l.id) : selected.delete(l.id)));
    render();
  });
  $("bulk-assign").addEventListener("click", () => bulkAssign(Number($("bulk-agent").value)));
  $("bulk-pool").addEventListener("click", () => bulkAssign(null));
  $("bulk-clear").addEventListener("click", () => { selected.clear(); render(); });
  $("recycle-btn").addEventListener("click", recycle);
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-back").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden && !$("reassign-dialog").open) closeDrawer(); });
  $("reassign-open").addEventListener("click", openReassign);
  $("re-cancel").addEventListener("click", () => $("reassign-dialog").close());
  $("reassign-form").addEventListener("submit", submitReassign);
  $("export-btn").addEventListener("click", exportXlsx);

  loadDictionaries().then(() => { load(); loadCounts(); loadTotal(); }).catch((ex) => showError(ex.message));
};

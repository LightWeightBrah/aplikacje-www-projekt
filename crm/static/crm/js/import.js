/* Import / eksport Excela (tylko manager): wysyłka pliku do /api/batches/import/ i raport. */
CRM.initImport = function initImport() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager") {
    $("page-error").textContent = "Ta strona jest dostępna tylko dla managera.";
    $("page-error").hidden = false;
    $("import-area").hidden = true;
    return;
  }

  const input = $("file-input"), zone = $("dropzone");
  let file = null;

  function setFile(f) {
    file = f;
    $("drop-title").textContent = f ? f.name : "Wybierz plik .xlsx albo upuść go tutaj";
    $("drop-hint").textContent = f
      ? (f.size / 1024).toFixed(0) + " KB · kliknij, żeby wybrać inny"
      : "Pierwszy arkusz. Max 5 MB i 5000 wierszy.";
    zone.classList.toggle("has-file", !!f);
  }
  function showImportError(msg) {
    $("import-error").textContent = msg || "";
    $("import-error").hidden = !msg;
  }

  input.addEventListener("change", () => { showImportError(""); setFile(input.files[0] || null); });
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.remove("drag");
  }));
  zone.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) { showImportError(""); setFile(f); }
  });

  /* Produkty do listy wyboru (nie blokuje importu, gdy się nie uda). */
  (async () => {
    try {
      let url = "/api/products/?page_size=500", items = [];
      while (url) {
        const r = await api(url);
        if (!r.ok) return;
        const d = await r.json();
        if (Array.isArray(d)) { items = d; break; }
        items.push(...d.results);
        url = d.next ? d.next.replace(location.origin, "") : null;
      }
      $("product").innerHTML = `<option value="">Bez produktu</option>` +
        items.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    } catch { /* lista produktów jest opcjonalna */ }
  })();

  /* Agenci do wyboru „Dla kogo jest ta baza” */
  (async () => {
    try {
      const r = await api("/api/agents/");
      if (!r.ok) return;
      const agents = (await r.json()).filter((a) => a.role === "agent");
      $("assigned-to").insertAdjacentHTML("beforeend",
        agents.map((a) => `<option value="${a.id}">${esc(a.full_name || a.username)}</option>`).join(""));
    } catch { /* bez listy baza trafi do wspólnej puli */ }
  })();

  /* Błędy walidacji DRF ({file: [...]}) albo {detail: "..."} zamieniamy na tekst. */
  function errorText(data, status) {
    if (data && data.detail) return data.detail;
    if (data && typeof data === "object") {
      const parts = Object.values(data).flat().filter((v) => typeof v === "string");
      if (parts.length) return parts.join(" ");
    }
    return "Import nie powiódł się (" + status + ").";
  }

  const issuesTable = (rows, cols) =>
    `<thead><tr>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c[1]])}</td>`).join("")}</tr>`).join("") + "</tbody>";

  function showResult(d) {
    $("result-card").hidden = false;
    $("result-detail").textContent = d.detail || "";
    $("result-detail").hidden = !d.detail;
    $("result-totals").innerHTML = [
      ["Wiersze w pliku", d.total_rows ?? 0],
      ["Zaimportowane", d.created ?? 0],
      ["Duplikaty", (d.duplicates || []).length],
      ["Błędy", (d.errors || []).length],
      ["Poprawione", (d.warnings || []).length],
    ].map(([l, v]) => `<span>${esc(l)} <b>${v}</b></span>`).join("");
    const sel = $("assigned-to"), who = sel.value ? sel.options[sel.selectedIndex].textContent : "";
    $("result-batch").innerHTML = d.batch
      ? `Utworzono bazę <b>${esc(d.batch.name)}</b>${d.layout === "ceidg" ? " (rozpoznano układ bazy z CEIDG)" : ""}. ` +
        (who ? `Baza jest dla agenta <b>${esc(who)}</b>: dostanie te firmy przyciskiem „Daj kolejnego leada”.` : `Leady są we wspólnej puli dla wszystkich agentów.`)
      : "";
    const warns = d.warnings || [];
    $("result-warnings").hidden = !warns.length;
    $("warnings-table").innerHTML = warns.length ? issuesTable(warns, [["Wiersz", "row"], ["Co poprawiono", "message"]]) : "";
    const dups = d.duplicates || [], errs = d.errors || [];
    $("result-dups").hidden = !dups.length;
    $("dups-table").innerHTML = dups.length
      ? issuesTable(dups, [["Wiersz", "row"], ["Telefon", "phone"], ["Powód", "reason"]]) : "";
    $("result-errors").hidden = !errs.length;
    $("errors-table").innerHTML = errs.length
      ? issuesTable(errs, [["Wiersz", "row"], ["Problem", "message"]]) : "";
    $("result-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("import-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    showImportError("");
    if (!file) { showImportError("Najpierw wybierz plik .xlsx."); return; }
    const body = new FormData();
    body.append("file", file);
    if ($("batch-name").value.trim()) body.append("name", $("batch-name").value.trim());
    if ($("product").value) body.append("product", $("product").value);
    if ($("assigned-to").value) body.append("assigned_to", $("assigned-to").value);
    const btn = $("import-btn");
    btn.disabled = true; btn.textContent = "Importuję…";
    try {
      const r = await api("/api/batches/import/", { method: "POST", body });
      let data = {};
      try { data = await r.json(); } catch { /* odpowiedź bez JSON */ }
      if (r.status === 201) {
        showResult(data); loadBatches();
        input.value = ""; setFile(null); $("batch-name").value = "";
      } else if (r.status === 400 && "total_rows" in data) {
        showResult(data);  // nic nie zaimportowano, ale raport pokazuje dlaczego
      } else {
        showImportError(errorText(data, r.status));
      }
    } catch (ex) {
      showImportError(ex.message || "Nie udało się wysłać pliku.");
    }
    btn.disabled = false; btn.textContent = "Importuj";
  });

  /* Wgrane bazy + „Usuń tę bazę” (usuwa tylko firmy, do których nikt nie dzwonił) */
  async function loadBatches() {
    let rows = [];
    try { const r = await api("/api/batches/overview/"); if (r.ok) rows = await r.json(); } catch { return; }
    $("batches-empty").hidden = rows.length > 0;
    $("batches-table").hidden = !rows.length;
    const day = (s) => new Date(s).toLocaleDateString("pl-PL");
    $("batches-table").innerHTML = rows.length ? `<thead><tr><th>Baza</th><th>Wgrana</th><th>Dla kogo</th><th>Firm</th><th>Jeszcze bez telefonu</th><th></th></tr></thead><tbody>` +
      rows.map((b) => `<tr><td><b>${esc(b.name)}</b></td><td>${day(b.created_at)}</td><td>${esc(b.assigned_to_name || "Wspólna pula")}</td>
        <td>${b.total}</td><td>${b.untouched}</td>
        <td><button type="button" class="btn-mini" data-del="${b.id}" data-name="${esc(b.name)}" data-total="${b.total}" data-fresh="${b.untouched}">Usuń tę bazę</button></td></tr>`).join("") + "</tbody>" : "";
  }
  /* Okno „Usuń tę bazę”: trzeba wpisać USUŃ (bez polskich liter też działa: USUN) */
  const firms = (n) => `${n} ${n === 1 ? "firma" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "firmy" : "firm"}`;
  const delDlg = $("del-dialog");
  let delId = null;
  const wordOk = () => ["USUŃ", "USUN"].includes($("del-word").value.trim().toUpperCase());
  $("batches-table").addEventListener("click", (e) => {
    const b = e.target.closest("[data-del]"); if (!b) return;
    const fresh = +b.dataset.fresh, kept = +b.dataset.total - fresh;
    delId = b.dataset.del;
    $("del-name").textContent = b.dataset.name;
    $("del-fresh").textContent = firms(fresh);
    $("del-kept").textContent = firms(kept);
    $("del-kept-row").hidden = !kept;
    $("del-word").value = ""; $("del-ok").disabled = true; $("del-error").hidden = true;
    delDlg.showModal(); $("del-word").focus();
  });
  $("del-word").addEventListener("input", () => { $("del-ok").disabled = !wordOk(); });
  $("del-cancel").addEventListener("click", () => delDlg.close());
  $("del-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!wordOk() || !delId) return;
    $("del-ok").disabled = true;
    try {
      const r = await api(`/api/batches/${delId}/`, { method: "DELETE" });
      if (!r.ok) throw new Error("Nie udało się usunąć bazy (" + r.status + ").");
      const d = await r.json();
      delDlg.close();
      $("batches-msg").textContent = `Usunięto bazę „${d.name}”: ${firms(d.removed)}${d.kept ? `, ${firms(d.kept)} z historią zostaje w CRM` : ""}.`;
      $("batches-msg").hidden = false;
      loadBatches();
    } catch (ex) { $("del-error").textContent = ex.message; $("del-error").hidden = false; $("del-ok").disabled = false; }
  });
  loadBatches();

  $("export-all").addEventListener("click", async () => {
    const btn = $("export-all");
    btn.disabled = true;
    try {
      const r = await api("/api/leads/export/");
      if (!r.ok) throw new Error("Eksport nie powiódł się (" + r.status + ").");
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "leady.xlsx" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (ex) {
      $("page-error").textContent = ex.message; $("page-error").hidden = false;
    }
    btn.disabled = false;
  });
};

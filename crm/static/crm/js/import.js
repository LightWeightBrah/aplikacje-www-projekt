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
      : "Pierwszy arkusz, pierwszy wiersz = nagłówki. Max 5 MB i 5000 wierszy.";
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
      let url = "/api/products/", items = [];
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
    ].map(([l, v]) => `<span>${esc(l)} <b>${v}</b></span>`).join("");
    $("result-batch").innerHTML = d.batch
      ? `Utworzono bazę <b>${esc(d.batch.name)}</b>. Leady są w puli: przypiszesz je w <a href="/crm/manager/">panelu managera</a>.`
      : "";
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
    const btn = $("import-btn");
    btn.disabled = true; btn.textContent = "Importuję…";
    try {
      const r = await api("/api/batches/import/", { method: "POST", body });
      let data = {};
      try { data = await r.json(); } catch { /* odpowiedź bez JSON */ }
      if (r.status === 201) {
        showResult(data);
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

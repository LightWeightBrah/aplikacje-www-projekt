/* Katalog produktów (tylko manager): nazwa, cena jednostkowa, jednostka, włączenie / wyłączenie.
   API: /api/products/. Agent wybiera te produkty przy wyniku „Sprzedane”, a wartość sprzedaży liczy się z ich cen. */
CRM.initProducts = function initProducts() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager") {
    $("page-error").textContent = "Ta strona jest dostępna tylko dla managera.";
    $("page-error").hidden = false;
    $("products-area").hidden = true;
    $("add-open").hidden = true;
    $("subtitle").textContent = "";
    return;
  }

  let products = [], editing = null, toastTimer;
  const JSON_HEADERS = { "Content-Type": "application/json" };

  function toast(msg, isError) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
  }
  function setError(msg) { $("prod-error").textContent = msg || ""; $("prod-error").hidden = !msg; }

  /* Błędy DRF ({pole: ["..."]} albo {detail: "..."}) jako jeden czytelny tekst */
  function errorText(data, status) {
    if (data && data.detail) return data.detail;
    if (data && typeof data === "object") {
      const parts = Object.entries(data).map(([k, v]) => (Array.isArray(v) ? v.join(" ") : String(v)));
      if (parts.length) return parts.join(" ");
    }
    return "Nie udało się zapisać (" + status + ").";
  }
  /* „1 200,50” / „1200.5” → "1200.50"; null, gdy to nie kwota (do 8 cyfr przed przecinkiem, do 2 po) */
  function parsePrice(text) {
    const t = text.trim().replace(/\s/g, "").replace(",", ".");
    if (t === "") return "0.00";
    return /^\d{1,8}(\.\d{1,2})?$/.test(t) ? Number(t).toFixed(2) : null;
  }
  const priceInput = (v) => Number(v).toFixed(2).replace(".", ",");

  async function load() {
    $("page-error").hidden = true;
    try { products = await CRM.fetchAll("/api/products/"); }
    catch (ex) { $("page-error").textContent = ex.message; $("page-error").hidden = false; return; }
    render();
  }

  function render() {
    const active = products.filter((p) => p.is_active);
    $("subtitle").textContent = `Do sprzedaży: ${active.length}` + (products.length > active.length ? ` · wyłączone: ${products.length - active.length}` : "");
    const noPrice = active.filter((p) => !(Number(p.price) > 0));
    const warn = $("no-price-warning");
    warn.hidden = !noPrice.length;
    warn.textContent = noPrice.length ? `Produkty bez ceny: ${noPrice.map((p) => p.name).join(", ")}. Sprzedaż takiego produktu liczy się jako 0 zł, więc uzupełnij cenę.` : "";
    if (!products.length) {
      $("products-table").innerHTML = `<tbody><tr><td class="muted">Katalog jest pusty. Dodaj pierwszy produkt, żeby agenci mogli wpisywać, co sprzedali. Dopóki katalog jest pusty, sprzedaż zapisuje się bez wartości.</td></tr></tbody>`;
      return;
    }
    $("products-table").innerHTML = `<thead><tr><th>Produkt</th><th class="num">Cena</th><th>Jednostka</th><th>Status</th><th></th></tr></thead><tbody>` +
      products.map((p) => `<tr class="${p.is_active ? "" : "inactive"}">
        <td><strong>${esc(p.name)}</strong>${p.description ? `<small class="muted block">${esc(p.description)}</small>` : ""}</td>
        <td class="num">${Number(p.price) > 0 ? esc(CRM.money(p.price)) : '<span class="chip" style="--c:#f59e0b">brak ceny</span>'}</td>
        <td>${esc(p.unit)}</td>
        <td>${p.is_active ? '<span class="chip" style="--c:#16a34a">Do sprzedaży</span>' : '<span class="chip" style="--c:#94a3b8">Wyłączony</span>'}</td>
        <td class="row-actions"><button type="button" class="btn-mini" data-act="edit" data-id="${p.id}">Edytuj</button>` +
        `<button type="button" class="btn-mini${p.is_active ? " danger" : ""}" data-act="toggle" data-id="${p.id}">${p.is_active ? "Wyłącz" : "Włącz"}</button></td></tr>`).join("") + "</tbody>";
  }

  function openDialog(p) {
    editing = p;
    $("prod-title").textContent = p ? "Edytuj produkt" : "Dodaj produkt";
    $("prod-name").value = p ? p.name : "";
    $("prod-price").value = p ? priceInput(p.price) : "";
    $("prod-unit").value = p ? p.unit : "szt.";
    $("prod-desc").value = p ? p.description : "";
    $("prod-hint").textContent = p
      ? "Zmiana ceny dotyczy tylko nowych sprzedaży. Dawne sprzedaże zostają z ceną z dnia sprzedaży."
      : "Cena to wartość jednej sztuki (albo jednej umowy). Przy sprzedaży 3 sztuk system policzy 3 × cena.";
    setError("");
    $("prod-dialog").showModal();
    $("prod-name").focus();
  }

  $("add-open").addEventListener("click", () => openDialog(null));
  $("prod-cancel").addEventListener("click", () => $("prod-dialog").close());
  $("prod-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("");
    const name = $("prod-name").value.trim(), price = parsePrice($("prod-price").value), unit = $("prod-unit").value.trim() || "szt.";
    if (!name) { setError("Wpisz nazwę produktu."); return; }
    if (price === null) { setError("Cena musi być kwotą, np. 1200 albo 1200,50."); return; }
    const body = { name, price, unit, description: $("prod-desc").value.trim() };
    $("prod-ok").disabled = true;
    try {
      const r = await api(editing ? `/api/products/${editing.id}/` : "/api/products/", {
        method: editing ? "PATCH" : "POST", headers: JSON_HEADERS, body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { setError(errorText(data, r.status)); return; }
      $("prod-dialog").close();
      toast(editing ? `Zapisano produkt: ${data.name}.` : `Dodano produkt: ${data.name}.`);
      await load();
    } catch {
      setError("Brak połączenia z serwerem. Spróbuj ponownie.");
    } finally {
      $("prod-ok").disabled = false;
    }
  });

  $("products-table").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const p = products.find((x) => x.id === Number(btn.dataset.id));
    if (!p) return;
    if (btn.dataset.act === "edit") { openDialog(p); return; }
    btn.disabled = true;
    try {
      const r = await api(`/api/products/${p.id}/`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ is_active: !p.is_active }) });
      if (!r.ok) throw new Error(errorText(await r.json().catch(() => ({})), r.status));
      toast(p.is_active ? `Wyłączono: ${p.name}. Agenci nie wybiorą go już przy sprzedaży.` : `Włączono: ${p.name}.`);
      await load();
    } catch (ex) {
      toast(ex.message, true);
      btn.disabled = false;
    }
  });

  load();
};

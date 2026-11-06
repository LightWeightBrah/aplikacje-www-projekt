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
    $("tariff-export").hidden = $("tariff-import").hidden = true;
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
      $("products-table").innerHTML = `<tbody><tr><td class="muted">Katalog jest pusty. Wgraj taryfy z wzornika ofert, wgraj swój plik taryf („Wgraj plik taryf…”) albo dodaj taryfę przyciskiem „+ Dodaj taryfę”. Dopóki katalog jest pusty, sprzedaż zapisuje się bez wartości.
        <div class="empty-actions"><button type="button" class="btn btn-primary" id="load-standard">Wgraj taryfy z wzornika (S, M100, M, L, VIP)</button></div></td></tr></tbody>`;
      $("load-standard").addEventListener("click", loadStandard);
      return;
    }
    $("products-table").innerHTML = `<thead><tr><th>Taryfa / produkt</th><th class="num">Cena</th><th class="num">Z promocją</th><th class="num">Kolejny numer</th><th>Status</th><th></th></tr></thead><tbody>` +
      products.map((p) => `<tr class="${p.is_active ? "" : "inactive"}">
        <td><strong>${esc(p.name)}</strong>${p.description ? `<small class="muted block">${esc(p.description)}</small>` : ""}</td>
        <td class="num">${Number(p.price) > 0 ? esc(CRM.money(p.price)) : '<span class="chip" style="--c:#f59e0b">brak ceny</span>'}</td>
        <td class="num">${p.promo_price != null ? esc(CRM.money(p.promo_price)) : "–"}</td>
        <td class="num">${Number(p.extra_price) > 0 ? "+ " + esc(CRM.money(p.extra_price)) : "–"}</td>
        <td>${p.is_active ? '<span class="chip" style="--c:#16a34a">Do sprzedaży</span>' : '<span class="chip" style="--c:#94a3b8">Wyłączony</span>'}</td>
        <td class="row-actions"><button type="button" class="btn-mini" data-act="edit" data-id="${p.id}">Edytuj</button>` +
        `<button type="button" class="btn-mini${p.is_active ? " danger" : ""}" data-act="toggle" data-id="${p.id}">${p.is_active ? "Wyłącz" : "Włącz"}</button>` +
        `<button type="button" class="btn-mini ghost danger" data-act="delete" data-id="${p.id}" title="Usuń produkt dodany przez pomyłkę">Usuń</button></td></tr>`).join("") + "</tbody>";
  }

  /* Taryfy z wzornika ofert zespołu (ceny potem można zmienić przyciskiem „Edytuj”) */
  async function loadStandard(e) {
    e.target.disabled = true;
    try {
      const r = await api("/api/products/load-standard/", { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { toast(errorText(data, r.status), true); e.target.disabled = false; return; }
      toast(data.created.length ? `Dodano taryfy: ${data.created.join(", ")}.` : "Te taryfy już są w katalogu.");
      await load();
    } catch {
      toast("Brak połączenia z serwerem. Spróbuj ponownie.", true);
      e.target.disabled = false;
    }
  }

  function openDialog(p) {
    editing = p;
    $("prod-title").textContent = p ? "Edytuj produkt" : "Dodaj produkt";
    $("prod-name").value = p ? p.name : "";
    $("prod-price").value = p ? priceInput(p.price) : "";
    $("prod-unit").value = p ? p.unit : "szt.";
    $("prod-desc").value = p ? p.description : "";
    $("prod-promo").value = p && p.promo_price != null ? priceInput(p.promo_price) : "";
    $("prod-extra").value = p && Number(p.extra_price) ? priceInput(p.extra_price) : "";
    $("prod-features").value = p ? p.features || "" : "";
    $("prod-hint").textContent = p
      ? "Zmiana ceny dotyczy tylko nowych sprzedaży. Dawne sprzedaże zostają z ceną z dnia sprzedaży."
      : "Cena to wartość jednej sztuki (albo jednej umowy). Przy sprzedaży 3 sztuk system policzy 3 × cena.";
    setError("");
    $("prod-dialog").showModal();
    $("prod-name").focus();
  }

  $("add-open").addEventListener("click", () => openDialog(null));

  /* Plik taryf (.xlsx): pobranie całego cennika i wgranie poprawionego / z innego serwera */
  $("tariff-export").addEventListener("click", async () => {
    const btn = $("tariff-export"); btn.disabled = true;
    try {
      const r = await api("/api/products/export/");
      if (!r.ok) throw new Error("Nie udało się pobrać pliku (" + r.status + ").");
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "taryfy.xlsx" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast("Pobrano taryfy.xlsx.");
    } catch (ex) { toast(ex.message, true); }
    btn.disabled = false;
  });
  $("tariff-import").addEventListener("click", () => $("tariff-file").click());
  $("tariff-file").addEventListener("change", async () => {
    const file = $("tariff-file").files[0]; if (!file) return;
    const btn = $("tariff-import"); btn.disabled = true;
    const body = new FormData(); body.append("file", file);
    try {
      const r = await api("/api/products/import/", { method: "POST", body });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(errorText(d, r.status), true); return; }
      const parts = [];
      if (d.created.length) parts.push(`dodane: ${d.created.join(", ")}`);
      if (d.updated.length) parts.push(`zaktualizowane: ${d.updated.join(", ")}`);
      if (d.errors.length) parts.push(`pominięte wiersze: ${d.errors.join("; ")}`);
      toast(parts.length ? "Plik taryf wgrany – " + parts.join(" · ") + "." : "W pliku nie było żadnej taryfy.", !!d.errors.length);
      await load();
    } catch { toast("Brak połączenia z serwerem. Spróbuj ponownie.", true); }
    finally { btn.disabled = false; $("tariff-file").value = ""; }
  });
  $("prod-cancel").addEventListener("click", () => $("prod-dialog").close());
  $("prod-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("");
    const name = $("prod-name").value.trim(), price = parsePrice($("prod-price").value), unit = $("prod-unit").value.trim() || "szt.";
    if (!name) { setError("Wpisz nazwę produktu."); return; }
    if (price === null) { setError("Cena musi być kwotą, np. 1200 albo 1200,50."); return; }
    const promoRaw = $("prod-promo").value.trim(), promo = promoRaw ? parsePrice(promoRaw) : null, extra = parsePrice($("prod-extra").value);
    if (promoRaw && promo === null) { setError("Cena z promocją musi być kwotą, np. 85 albo 85,50."); return; }
    if (extra === null) { setError("Dopłata za kolejny numer musi być kwotą, np. 25."); return; }
    const body = { name, price, unit, description: $("prod-desc").value.trim(), promo_price: promo, extra_price: extra, features: $("prod-features").value.trim() };
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
    if (btn.dataset.act === "delete") {
      if (!confirm(`Usunąć produkt „${p.name}”?

Tego nie da się cofnąć. Jeśli produkt był już sprzedawany, CRM go nie usunie – wtedy kliknij „Wyłącz”.`)) return;
      btn.disabled = true;
      try {
        const r = await api(`/api/products/${p.id}/`, { method: "DELETE" });
        if (!r.ok) throw new Error(errorText(await r.json().catch(() => ({})), r.status));
        toast(`Usunięto produkt: ${p.name}.`);
        await load();
      } catch (ex) {
        toast(ex.message, true);
        btn.disabled = false;
      }
      return;
    }
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

/* Ustawienia ofert (dokąd idą oferty, prowizja, treść) i „Solid info” – tylko manager. */
CRM.initOfferSettings = function initOfferSettings() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager" || !$("offer-form")) return;
  const req = async (url, method, body) => {
    const r = await api(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const data = r.status === 204 ? {} : await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || Object.values(data).flat().join(" ") || "Nie udało się zapisać.");
    return data;
  };
  const num = (v) => String(v).replace(",", ".").trim();

  async function loadConfig() {
    const c = await req("/api/offer-config/", "GET");
    $("oc-mode").value = c.mode; $("oc-email").value = c.forward_email; $("oc-name").value = c.forward_name;
    $("oc-term").value = String(c.default_term); $("oc-c36").value = String(c.commission_36).replace(".", ",");
    $("oc-c24").value = String(c.commission_24).replace(".", ","); $("oc-promo").value = c.promo_label;
    $("oc-greeting").value = c.greeting; $("oc-closing").value = c.closing;
    $("oc-signature").value = c.signature || ""; $("oc-phone").value = c.contact_phone || "";
    $("mail-state").innerHTML = c.email_enabled ? "✓ Serwer poczty jest skonfigurowany: oferty wysyłają się same."
      : "Serwer poczty nie jest jeszcze skonfigurowany (ustawienia <code>EMAIL_HOST</code> na serwerze). Do tego czasu agent dostaje gotową wiadomość i wysyła ją jednym kliknięciem „Otwórz w poczcie”.";
  }
  $("offer-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await req("/api/offer-config/", "PATCH", {
        mode: $("oc-mode").value, forward_email: $("oc-email").value.trim(), forward_name: $("oc-name").value.trim(),
        default_term: Number($("oc-term").value), commission_36: num($("oc-c36").value), commission_24: num($("oc-c24").value),
        promo_label: $("oc-promo").value.trim(), greeting: $("oc-greeting").value, closing: $("oc-closing").value,
        signature: $("oc-signature").value.trim(), contact_phone: $("oc-phone").value.trim(),
      });
      $("oc-msg").textContent = "Zapisano ✓";
    } catch (ex) { $("oc-msg").textContent = ex.message; }
  });

  async function loadAnn() {
    const items = await req("/api/announcements/", "GET");
    $("ann-list").innerHTML = items.length ? items.map((a) => `<div class="ann-row${a.is_active ? "" : " off"}"><div><strong>${a.pinned ? "📌 " : ""}${esc(a.title)}</strong>${a.body ? `<p>${esc(a.body)}</p>` : ""}</div>
      <button type="button" class="btn-mini" data-ann-toggle="${a.id}" data-active="${a.is_active}">${a.is_active ? "Ukryj" : "Pokaż"}</button>
      <button type="button" class="btn-mini danger" data-ann-del="${a.id}">Usuń</button></div>`).join("") : `<p class="muted small">Brak informacji.</p>`;
  }
  $("ann-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const t = $("ann-title").value.trim(); if (!t) return;
    await req("/api/announcements/", "POST", { title: t, body: $("ann-body").value.trim(), pinned: $("ann-pinned").checked });
    $("ann-title").value = ""; $("ann-body").value = ""; $("ann-pinned").checked = false;
    loadAnn();
  });
  $("ann-list").addEventListener("click", async (e) => {
    const tg = e.target.closest("[data-ann-toggle]"), del = e.target.closest("[data-ann-del]");
    if (tg) { await req(`/api/announcements/${tg.dataset.annToggle}/`, "PATCH", { is_active: tg.dataset.active !== "true" }); loadAnn(); }
    if (del && confirm("Usunąć tę informację?")) { await req(`/api/announcements/${del.dataset.annDel}/`, "DELETE"); loadAnn(); }
  });
  /* Losowanie nowych firm dla całego zespołu: wszystko losowo albo najpierw firmy jednoosobowe */
  async function loadDraw() {
    const r = await req("/api/settings/", "GET");
    document.querySelector(`input[name="draw"][value="${r.companies_last ? "last" : "random"}"]`).checked = true;
  }
  document.querySelectorAll('input[name="draw"]').forEach((i) => i.addEventListener("change", async () => {
    try {
      await req("/api/settings/", "PATCH", { companies_last: i.value === "last" });
      $("draw-msg").textContent = "Zapisano ✓ – działa od następnego losowania.";
    } catch (ex) { $("draw-msg").textContent = ex.message; }
  }));
  loadConfig().catch(() => {}); loadAnn().catch(() => {}); loadDraw().catch(() => {});
};

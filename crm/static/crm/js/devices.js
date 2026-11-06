/* „Urządzenia z dostępem” (manager): kody aktywacji i blokowanie urządzeń. API: /api/devices/… */
CRM.initDevices = function initDevices() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager" || !$("dev-card")) return;
  const when = (v) => (v ? new Date(v).toLocaleString("pl-PL", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–");

  async function load() {
    const r = await api("/api/devices/");
    if (!r.ok) { $("dev-list").innerHTML = `<tr><td colspan="5" class="form-error">Nie udało się pobrać urządzeń.</td></tr>`; return; }
    const d = await r.json();
    $("dev-state").innerHTML = (d.device_lock
      ? "✓ <b>Blokada włączona</b> – wchodzą tylko aktywowane urządzenia."
      : "Blokada jest <b>wyłączona</b> (tak jest na komputerze deweloperskim; na serwerze firmy włącza się sama).")
      + (d.codes_waiting ? ` Niewykorzystane kody: ${d.codes_waiting}.` : "");
    $("dev-list").innerHTML = d.results.length ? d.results.map((x) => `<tr class="${x.revoked_at ? "att-off" : ""}">
        <td><strong>${esc(x.name)}</strong>${x.this_device ? ' <small class="muted">(to urządzenie)</small>' : ""}</td>
        <td>${when(x.created_at)}</td><td>${when(x.last_seen)}</td><td>${esc(x.created_by || "–")}</td>
        <td class="num">${x.revoked_at ? `<span class="muted">zablokowane ${when(x.revoked_at)}</span>`
          : x.this_device ? "" : `<button type="button" class="btn-mini" data-revoke="${x.id}" data-name="${esc(x.name)}">Zablokuj</button>`}</td></tr>`).join("")
      : `<tr><td colspan="5" class="muted">Jeszcze żadne urządzenie nie jest aktywowane.</td></tr>`;
  }

  $("dev-new").addEventListener("click", async () => {
    const r = await api("/api/devices/code/", { method: "POST" });
    if (!r.ok) return;
    const c = await r.json(), box = $("dev-code");
    box.innerHTML = `<b class="dev-code-val">${esc(c.code)}</b><span>Wpisz ten kod na nowym urządzeniu (otwórz adres CRM – pokaże się pole na kod).
      Działa <b>raz</b>, do ${when(c.expires_at)}. Kodu nie da się potem podejrzeć – zapisz go albo przekaż od razu.</span>`;
    box.hidden = false;
    load();
  });
  $("dev-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-revoke]"); if (!b) return;
    if (!confirm(`Zablokować „${b.dataset.name}”? To urządzenie straci dostęp do CRM od razu.`)) return;
    const r = await api(`/api/devices/${b.dataset.revoke}/revoke/`, { method: "POST" });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).detail || "Nie udało się zablokować."); return; }
    load();
  });
  load();
};

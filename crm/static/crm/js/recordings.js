/* Nagrywanie rozmów (opcjonalne): karta w Ustawieniach (manager) i odsłuch nagrań w szufladzie leada (Zespół).
   API: /api/recordings/settings/, /api/recordings/google/connect|disconnect/, /api/recordings/?lead=, /api/recordings/{id}/audio/ */
(() => {
  const { api, esc, $ } = CRM;
  const when = (iso) => (iso ? new Date(iso).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

  async function req(url, method = "GET", body) {
    const r = await api(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const data = r.status === 204 ? {} : await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || Object.values(data).flat().join(" ") || "Nie udało się.");
    return data;
  }

  /* ---------------------------------------------------------- Ustawienia */
  CRM.initRecordings = function initRecordings() {
    const me = CRM.currentUser() || {};
    if (me.role !== "manager" || !$("rec-card")) return;
    const err = (msg) => { $("rec-error").textContent = msg || ""; $("rec-error").hidden = !msg; };

    function phoneLine(p) {
      const state = p.can_record === true ? `<span class="rec-ok">✓ nagrywanie włączone</span>`
        : p.can_record === false ? `<span class="rec-bad">✕ nagrywanie wyłączone na telefonie</span>`
        : `<span class="muted">stara wersja dialera – zainstaluj 1.2</span>`;
      return `<li><strong>${esc(p.user)}</strong> <span class="muted">· ${esc(p.device || "telefon")}${p.app_version ? " · " + esc(p.app_version) : ""}</span> – ${state}</li>`;
    }

    function draw(s) {
      let drive;
      if (!s.oauth_configured) {
        drive = "Na serwerze brakuje danych Google (<code>GOOGLE_CLIENT_ID</code> i <code>GOOGLE_CLIENT_SECRET</code> w pliku .env). Jednorazowa konfiguracja jest opisana w <code>docs/NAGRYWANIE.md</code>.";
      } else if (s.drive_connected) {
        drive = `✓ Połączono z kontem <strong>${esc(s.google_email || "Google")}</strong>${s.connected_at ? " (" + esc(when(s.connected_at)) + ")" : ""}. Nagrań na Dysku: ${s.recordings}.`;
      } else {
        drive = "Dysk Google nie jest połączony. Najlepiej użyć osobnego, firmowego konta Google (15 GB za darmo, wspólne z Gmailem i Zdjęciami tego konta).";
      }
      $("rec-drive-state").innerHTML = drive;
      $("rec-connect").hidden = !s.oauth_configured || s.drive_connected;
      $("rec-disconnect").hidden = !s.drive_connected;
      $("rec-enabled").checked = s.enabled;
      $("rec-days").value = s.retention_days;
      $("rec-enabled-hint").textContent = s.active ? "Włączone: rozmowy z dialera są nagrywane."
        : s.enabled ? "Włączone, ale telefony nagrywają dopiero po połączeniu Dysku Google." : "Wyłączone: telefony nic nie nagrywają.";
      $("rec-phones").innerHTML = s.phones.length ? s.phones.map(phoneLine).join("") : `<li class="muted">Żaden telefon nie jest jeszcze sparowany z dialerem.</li>`;
      $("rec-meta").textContent = s.updated_by_name ? `Ostatnia zmiana nagrywania: ${s.updated_by_name}, ${when(s.updated_at)}` : "";
    }

    async function load() {
      try { draw(await req("/api/recordings/settings/")); err(""); } catch (ex) { err(ex.message); }
    }

    $("rec-connect").addEventListener("click", async () => {
      $("rec-connect").disabled = true;
      try { location.href = (await req("/api/recordings/google/connect/", "POST")).url; }
      catch (ex) { err(ex.message); $("rec-connect").disabled = false; }
    });
    $("rec-disconnect").addEventListener("click", async () => {
      if (!confirm("Odłączyć Dysk Google? Telefony przestaną nagrywać. Nagrania, które już są na Dysku, zostaną tam.")) return;
      try { draw(await req("/api/recordings/google/disconnect/", "POST")); err(""); } catch (ex) { err(ex.message); }
    });
    $("rec-save").addEventListener("click", async () => {
      const days = Number($("rec-days").value);
      if (!Number.isInteger(days) || days < 7 || days > 3650) { $("rec-msg").textContent = "Wpisz od 7 do 3650 dni."; return; }
      try {
        draw(await req("/api/recordings/settings/", "PATCH", { enabled: $("rec-enabled").checked, retention_days: days }));
        $("rec-msg").textContent = "Zapisano ✓"; err("");
      } catch (ex) { $("rec-msg").textContent = ex.message; }
    });

    // powrót ze strony Google: komunikat i czysty adres
    const q = new URLSearchParams(location.search);
    if (q.get("drive")) {
      if (q.get("drive") === "ok") $("rec-msg").textContent = "Dysk Google połączony ✓ – zaznacz „Nagrywaj rozmowy” i zapisz.";
      else err(q.get("msg") || "Nie udało się połączyć Dysku Google.");
      history.replaceState(null, "", location.pathname);
      setTimeout(() => $("rec-card").scrollIntoView({ block: "start" }), 300);
    }
    load();
  };

  /* ---------------------------------------------------------- Odsłuch (szuflada leada) */
  const urls = new Map();   // id nagrania → adres pliku w przeglądarce (pobrany raz)

  CRM.drawRecordings = async function drawRecordings(leadId, box) {
    if (!box) return;
    let rows;
    try {
      const r = await api(`/api/recordings/?lead=${leadId}`);
      if (!r.ok) { box.hidden = true; return; }
      rows = (await r.json()).results;
    } catch { box.hidden = true; return; }
    if (!rows.length) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<h3>Nagrania rozmów</h3><ul class="history rec-list">${rows.map((x) => `
      <li data-rec="${x.id}"><button type="button" class="btn btn-secondary btn-sm rec-play" aria-label="Odtwórz nagranie">▶ Odtwórz</button>
        <span class="who">${esc(x.agent_name || "–")} · ${esc(CRM.talk(x.duration_seconds / 60))}</span><time>${esc(when(x.started_at))}</time>
        <div class="rec-player"></div></li>`).join("")}</ul>`;
    box.querySelectorAll(".rec-play").forEach((btn) => btn.addEventListener("click", async () => {
      const li = btn.closest("li"), id = li.dataset.rec, slot = li.querySelector(".rec-player");
      btn.disabled = true; btn.textContent = "Pobieram…";
      try {
        if (!urls.has(id)) {
          const r = await api(`/api/recordings/${id}/audio/`);
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Nie udało się pobrać nagrania.");
          urls.set(id, URL.createObjectURL(await r.blob()));
        }
        slot.innerHTML = `<audio controls autoplay src="${urls.get(id)}"></audio>`;
        btn.hidden = true;
      } catch (ex) {
        slot.innerHTML = `<p class="form-error">${esc(ex.message)}</p>`;
        btn.disabled = false; btn.textContent = "▶ Odtwórz";
      }
    }));
  };
})();

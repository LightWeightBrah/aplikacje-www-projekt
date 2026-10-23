/* Zarządzanie użytkownikami (tylko manager): /api/users/ – dodanie, reset hasła, dezaktywacja. */
CRM.initUsers = function initUsers() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager") {
    $("page-error").textContent = "Ta strona jest dostępna tylko dla managera.";
    $("page-error").hidden = false;
    $("users-area").hidden = true;
    $("add-open").hidden = true;
    $("subtitle").textContent = "";
    return;
  }

  let users = [];
  let iAmSuperuser = false;
  let toastTimer;
  const JSON_HEADERS = { "Content-Type": "application/json" };
  const ROLE = { agent: "Agent", manager: "Manager" };

  function toast(msg, isError) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
  }
  function setError(id, msg) { $(id).textContent = msg || ""; $(id).hidden = !msg; }

  /* Błędy DRF ({pole: ["..."]} albo {detail: "..."}) jako jeden czytelny tekst. */
  function errorText(data, status) {
    if (data && data.detail) return data.detail;
    if (data && typeof data === "object") {
      const parts = Object.entries(data).map(([k, v]) => (Array.isArray(v) ? v.join(" ") : String(v)));
      if (parts.length) return parts.join(" ");
    }
    return "Nie udało się wykonać operacji (" + status + ").";
  }
  async function call(path, method, body) {
    const r = await api(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data };
  }

  const when = (iso) => iso
    ? new Date(iso).toLocaleString("pl-PL", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : "nie logował się";
  const label = (u) => (u.full_name === u.username ? u.username : `${u.full_name} (${u.username})`);
  const initial = (u) => esc((u.full_name || u.username).charAt(0).toUpperCase());
  const avatarStyle = (u) => (u.avatar ? ` style="background-image:url('${esc(u.avatar)}')"` : "");
  const avatarHtml = (u) => {
    const style = u.avatar ? ` style="background-image:url('${esc(u.avatar)}')"` : "";
    return `<span class="avatar av-rank${u.avatar ? " has-img" : ""}"${style} aria-hidden="true">${u.avatar ? "" : initial(u)}</span>`;
  };

  /* ---- lista ---- */
  async function load() {
    setError("page-error", "");
    try {
      const r = await api("/api/users/");
      if (!r.ok) throw new Error(errorText(await r.json().catch(() => ({})), r.status));
      users = await r.json();
    } catch (ex) {
      setError("page-error", ex.message);
      return;
    }
    iAmSuperuser = users.some((u) => u.is_me && u.is_superuser);
    render();
  }

  function render() {
    const active = users.filter((u) => u.is_active).length;
    $("subtitle").textContent = `Aktywnych kont: ${active}` + (users.length > active ? ` · nieaktywnych: ${users.length - active}` : "");
    const head = `<thead><tr><th>Użytkownik</th><th>Rola</th><th class="num">Aktywne leady</th><th>Ostatnie logowanie</th><th>Status</th><th></th></tr></thead>`;
    const groups = [
      ["Managerowie", users.filter((u) => u.is_active && u.role === "manager")],
      ["Agenci", users.filter((u) => u.is_active && u.role === "agent")],
      ["Nieaktywne konta", users.filter((u) => !u.is_active)],
    ].filter(([, list]) => list.length);
    $("users-table").innerHTML = head + groups.map(([title, list]) =>
      `<tbody><tr class="group-row"><th colspan="6">${title} <span>${list.length}</span></th></tr>${list.map(row).join("")}</tbody>`
    ).join("");
  }

  function row(u) {
    const locked = u.is_superuser && !iAmSuperuser;
    let actions;
    if (locked) {
      actions = `<span class="muted small">Administrator systemu</span>`;
    } else {
      actions = `<button type="button" class="btn-mini" data-act="edit" data-id="${u.id}">Edytuj</button>` +
        `<button type="button" class="btn-mini" data-act="reset" data-id="${u.id}">Reset hasła</button>`;
      if (u.is_me) actions += `<span class="muted small">to Twoje konto</span>`;
      else if (u.is_active) actions += `<button type="button" class="btn-mini danger" data-act="deactivate" data-id="${u.id}">Dezaktywuj</button>`;
      else actions += `<button type="button" class="btn-mini" data-act="activate" data-id="${u.id}">Aktywuj</button>`;
      if (u.can_delete) actions += `<button type="button" class="btn-mini danger" data-act="delete" data-id="${u.id}">Usuń</button>`;
    }
    return `<tr class="${u.is_active ? "" : "inactive"}${u.is_me ? " me" : ""}">
      <td><span class="who-cell">${avatarHtml(u)}
        <span class="who"><span><strong>${esc(u.full_name)}</strong>${u.is_me ? ' <small class="you">to Ty</small>' : ""}</span><small class="muted">${esc(u.username)}</small></span></span></td>
      <td>${ROLE[u.role] || esc(u.role)}</td>
      <td class="num">${u.is_active ? u.active_leads : "–"}</td>
      <td class="muted small">${when(u.last_login)}</td>
      <td>${u.is_active ? '<span class="chip" style="--c:#16a34a">Aktywne</span>' : '<span class="chip" style="--c:#94a3b8">Nieaktywne</span>'}</td>
      <td class="row-actions">${actions}</td></tr>`;
  }

  /* ---- okno z hasłem (pokazywane raz) ---- */
  function showSecret({ title, intro, username, password }) {
    $("secret-title").textContent = title;
    $("secret-intro").textContent = intro;
    $("secret-login").textContent = username;
    $("secret-password").textContent = password;
    $("secret-copy").textContent = "Kopiuj hasło";
    $("secret-dialog").showModal();
  }
  $("secret-copy").addEventListener("click", async () => {
    const text = $("secret-password").textContent;
    try {
      await navigator.clipboard.writeText(text);
      $("secret-copy").textContent = "Skopiowano";
    } catch {
      const range = document.createRange();
      range.selectNodeContents($("secret-password"));
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
      $("secret-copy").textContent = "Zaznaczone, skopiuj Ctrl+C";
    }
  });
  $("secret-dialog").addEventListener("close", () => {
    $("secret-password").textContent = "";  // hasło nie zostaje w stronie po zamknięciu okna
  });

  /* ---- dodawanie ---- */
  const ascii = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ł/g, "l").replace(/Ł/g, "L")
    .toLowerCase().replace(/[^a-z0-9]+/g, "");
  let usernameTouched = false;
  function suggestUsername() {
    if (usernameTouched) return;
    const f = ascii($("add-first").value), l = ascii($("add-last").value);
    $("add-username").value = f && l ? f + "." + l : f || l;
  }
  $("add-first").addEventListener("input", suggestUsername);
  $("add-last").addEventListener("input", suggestUsername);
  $("add-username").addEventListener("input", () => { usernameTouched = true; });

  $("add-open").addEventListener("click", () => {
    $("add-form").reset();
    usernameTouched = false;
    setError("add-error", "");
    $("add-dialog").showModal();
    $("add-first").focus();
  });
  $("add-cancel").addEventListener("click", () => $("add-dialog").close());
  $("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("add-error", "");
    const body = {
      first_name: $("add-first").value.trim(), last_name: $("add-last").value.trim(),
      username: $("add-username").value.trim(), role: $("add-role").value,
      password: $("add-password").value,
    };
    if (!body.first_name || !body.last_name || !body.username) {
      setError("add-error", "Uzupełnij imię, nazwisko i login.");
      return;
    }
    $("add-ok").disabled = true;
    try {
      const res = await call("/api/users/", "POST", body);
      if (!res.ok) { setError("add-error", errorText(res.data, res.status)); return; }
      $("add-dialog").close();
      await load();
      const u = res.data;
      if (u.password) {
        showSecret({
          title: "Konto utworzone",
          intro: `${u.full_name} (${ROLE[u.role]}) zaloguje się tym hasłem tymczasowym i od razu zostanie poproszony o ustawienie własnego.`,
          username: u.username, password: u.password,
        });
      } else {
        toast(`Dodano konto: ${u.full_name} (${u.username}).`);
      }
    } catch (ex) {
      setError("add-error", ex.message);
    } finally {
      $("add-ok").disabled = false;
    }
  });

  /* ---- edycja konta: imię, nazwisko, login, rola ---- */
  function openEdit(u) {
    target = u;
    $("edit-title").textContent = "Edytuj konto: " + label(u);
    $("edit-first").placeholder = u.first_name ? "" : "brak (w aplikacji widać login)";
    $("edit-last").placeholder = u.last_name ? "" : "brak";
    $("edit-first").value = u.first_name;
    $("edit-last").value = u.last_name;
    $("edit-username").value = u.username;
    $("edit-role").value = u.role;
    const lockedRole = u.is_me || u.is_superuser;
    $("edit-role").disabled = lockedRole;
    $("edit-role-hint").textContent = u.is_me ? "Własnej roli nie zmienisz." : u.is_superuser ? "Rola administratora wynika z jego uprawnień."
      : "Manager widzi wszystko i zarządza zespołem. Agent widzi tylko swoje leady. Leady zostają przy osobie.";
    setError("edit-error", "");
    paintEditAvatar(u);
    $("edit-avatar-msg").textContent = "Zdjęcie zapisuje się od razu, niezależnie od przycisku „Zapisz”.";
    $("edit-dialog").showModal();
    $("edit-first").focus();
  }
  /* Zdjęcie profilowe edytowanego użytkownika: zapisuje się od razu przez /api/users/{id}/avatar/. */
  function paintEditAvatar(u) {
    const el = $("edit-avatar");
    el.style.backgroundImage = u.avatar ? `url("${u.avatar}")` : "";
    el.classList.toggle("has-img", !!u.avatar);
    el.textContent = u.avatar ? "" : (u.full_name || u.username).charAt(0).toUpperCase();
    $("edit-avatar-remove").hidden = !u.avatar;
  }
  async function afterAvatarChange(avatar, text) {
    target.avatar = avatar;
    paintEditAvatar(target);
    $("edit-avatar-msg").textContent = text;
    if (target.is_me) {  // własne zdjęcie w menu profilu
      const me = CRM.currentUser() || {};
      try { localStorage.setItem("crm_user", JSON.stringify({ ...me, avatar })); } catch { /* brak dostępu */ }
    }
    await load();
  }
  $("edit-avatar-change").addEventListener("click", () => $("edit-avatar-file").click());
  $("edit-avatar-file").addEventListener("change", async () => {
    const input = $("edit-avatar-file"), file = input.files[0];
    input.value = "";
    if (!file) return;
    $("edit-avatar-msg").textContent = "Wysyłanie…";
    try {
      const image = await CRM.resizeToSquare(file);
      const res = await call(`/api/users/${target.id}/avatar/`, "POST", { image });
      if (!res.ok) throw new Error((res.data.image || [])[0] || errorText(res.data, res.status));
      await afterAvatarChange(image, "Zdjęcie zapisane.");
    } catch (ex) { $("edit-avatar-msg").textContent = ex.message; }
  });
  $("edit-avatar-remove").addEventListener("click", async () => {
    const r = await api(`/api/users/${target.id}/avatar/`, { method: "DELETE" });
    if (r.status === 204) await afterAvatarChange("", "Zdjęcie usunięte.");
    else $("edit-avatar-msg").textContent = "Nie udało się usunąć zdjęcia.";
  });
  $("edit-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("edit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("edit-error", "");
    /* Wysyłamy tylko to, co faktycznie zmieniono, żeby przy edycji loginu czy roli
       nie nadpisać imienia i nazwiska (np. gdy konto ich nie ma). */
    const body = {};
    const first = $("edit-first").value.trim(), last = $("edit-last").value.trim(), login = $("edit-username").value.trim();
    if (!login) { setError("edit-error", "Login nie może być pusty."); return; }
    if (first !== target.first_name) body.first_name = first;
    if (last !== target.last_name) body.last_name = last;
    if (login !== target.username) body.username = login;
    if (!$("edit-role").disabled && $("edit-role").value !== target.role) body.role = $("edit-role").value;
    if (!Object.keys(body).length) { $("edit-dialog").close(); return; }
    if (body.role && body.role !== target.role &&
        !confirm(`Zmienić rolę: ${label(target)} na ${ROLE[body.role]}?`)) return;
    $("edit-ok").disabled = true;
    try {
      const res = await call(`/api/users/${target.id}/`, "PATCH", body);
      if (!res.ok) { setError("edit-error", errorText(res.data, res.status)); return; }
      $("edit-dialog").close();
      toast(`Zapisano zmiany: ${res.data.full_name}.`);
      await load();
    } catch (ex) {
      setError("edit-error", ex.message);
    } finally {
      $("edit-ok").disabled = false;
    }
  });

  /* ---- reset hasła ---- */
  let target = null;
  const byId = (id) => users.find((u) => u.id === Number(id));

  function openReset(u) {
    target = u;
    $("reset-intro").textContent = `${label(u)}: ustawisz nowe hasło, a obecne przestanie działać. ` +
      "Jeśli osoba jest zalogowana, zostanie wylogowana.";
    $("reset-password").value = "";
    setError("reset-error", "");
    $("reset-dialog").showModal();
  }
  $("reset-cancel").addEventListener("click", () => $("reset-dialog").close());
  $("reset-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("reset-error", "");
    $("reset-ok").disabled = true;
    try {
      const res = await call(`/api/users/${target.id}/reset-password/`, "POST", { password: $("reset-password").value });
      if (!res.ok) { setError("reset-error", errorText(res.data, res.status)); return; }
      $("reset-dialog").close();
      showSecret({
        title: "Hasło zresetowane",
        intro: `Hasło tymczasowe dla: ${target.full_name}. Przy najbliższym logowaniu zostanie poproszony o ustawienie własnego.`,
        username: target.username, password: res.data.password,
      });
      if (target.is_me) toast("Zmieniłeś własne hasło, przy następnym żądaniu zostaniesz wylogowany.");
    } catch (ex) {
      setError("reset-error", ex.message);
    } finally {
      $("reset-ok").disabled = false;
    }
  });

  /* ---- dezaktywacja ---- */
  function openDeactivate(u) {
    target = u;
    const others = users.filter((x) => x.is_active && x.id !== u.id);
    const hasLeads = u.active_leads > 0;
    $("deact-intro").textContent = hasLeads
      ? `${u.full_name} ma aktywne leady: ${u.active_leads}. Po dezaktywacji nie zaloguje się do systemu. Zdecyduj, co z jego leadami.`
      : `${label(u)} nie zaloguje się do systemu. Historia rozmów zostaje, konto możesz później aktywować.`;
    $("deact-handover").hidden = !hasLeads;
    $("deact-to").innerHTML = others.map((x) => `<option value="${x.id}">${esc(x.full_name)} (${ROLE[x.role]})</option>`).join("");
    document.querySelector('input[name="handover"][value="pool"]').checked = true;
    $("deact-to").hidden = true;
    setError("deact-error", "");
    $("deact-dialog").showModal();
  }
  document.querySelectorAll('input[name="handover"]').forEach((r) => r.addEventListener("change", () => {
    $("deact-to").hidden = document.querySelector('input[name="handover"]:checked').value !== "agent";
  }));
  $("deact-cancel").addEventListener("click", () => $("deact-dialog").close());
  $("deact-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("deact-error", "");
    const body = {};
    if (target.active_leads > 0) {
      body.handover = document.querySelector('input[name="handover"]:checked').value;
      if (body.handover === "agent") {
        body.to_agent = Number($("deact-to").value);
        if (!body.to_agent) { setError("deact-error", "Wybierz, komu przekazać leady."); return; }
      }
    }
    $("deact-ok").disabled = true;
    try {
      const res = await call(`/api/users/${target.id}/deactivate/`, "POST", body);
      if (res.status === 409) {
        // lista była nieaktualna: agent dostał leady po załadowaniu strony
        await load();
        const fresh = byId(target.id);
        $("deact-dialog").close();
        if (fresh) openDeactivate(fresh);
        return;
      }
      if (!res.ok) { setError("deact-error", errorText(res.data, res.status)); return; }
      $("deact-dialog").close();
      const moved = res.data.leads_moved;
      toast(`Konto ${target.full_name} zdezaktywowane` + (moved ? ` (leady: ${moved}).` : "."));
      await load();
    } catch (ex) {
      setError("deact-error", ex.message);
    } finally {
      $("deact-ok").disabled = false;
    }
  });

  /* ---- usuwanie (tylko konta bez historii) ---- */
  function openDelete(u) {
    target = u;
    $("del-intro").textContent = `Konto ${label(u)} zostanie trwale usunięte i nie da się go przywrócić. ` +
      "Dozwolone tylko dla kont bez historii rozmów i leadów (np. testowych).";
    setError("del-error", "");
    $("del-dialog").showModal();
  }
  $("del-cancel").addEventListener("click", () => $("del-dialog").close());
  $("del-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("del-error", "");
    $("del-ok").disabled = true;
    try {
      const r = await api(`/api/users/${target.id}/`, { method: "DELETE" });
      if (r.status !== 204) {
        setError("del-error", errorText(await r.json().catch(() => ({})), r.status));
        if (r.status === 409) await load();
        return;
      }
      $("del-dialog").close();
      toast(`Konto ${label(target)} zostało usunięte.`);
      await load();
    } catch (ex) {
      setError("del-error", ex.message);
    } finally {
      $("del-ok").disabled = false;
    }
  });

  /* ---- aktywacja i kliknięcia w tabeli ---- */
  async function activate(u) {
    const res = await call(`/api/users/${u.id}/activate/`, "POST");
    if (!res.ok) { toast(errorText(res.data, res.status), true); return; }
    toast(`Konto ${u.full_name} jest znów aktywne.`);
    await load();
  }
  $("users-table").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const u = byId(btn.dataset.id);
    if (!u) return;
    if (btn.dataset.act === "edit") openEdit(u);
    else if (btn.dataset.act === "reset") openReset(u);
    else if (btn.dataset.act === "deactivate") openDeactivate(u);
    else if (btn.dataset.act === "activate") activate(u);
    else if (btn.dataset.act === "delete") openDelete(u);
  });

  load();
};

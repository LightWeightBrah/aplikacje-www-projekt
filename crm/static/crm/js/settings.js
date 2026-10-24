/* Ustawienia zespołu (tylko manager): limity prób, ponowne próby, zwroty do puli. API: /api/settings/ */
CRM.initSettings = function initSettings() {
  const { api, esc, $ } = CRM;
  const me = CRM.currentUser() || {};
  if (me.role !== "manager") {
    $("page-error").textContent = "Ta strona jest dostępna tylko dla managera.";
    $("page-error").hidden = false;
    return;
  }
  $("settings-area").hidden = false;

  /* base: jednostka, w której API trzyma wartość (min / h / d / count); units: jednostki do wyboru w formularzu */
  const GROUPS = [
    { title: "Ile prób ma agent", intro: "Ile razy agent może nie dodzwonić się do nowego leada, zanim lead wróci do wspólnej puli.", fields: [
      { key: "max_attempts_per_agent", label: "Limit prób jednego agenta", base: "count", suffix: "prób", hint: "Po tylu nieodebranych telefonach lead wraca do puli i trafia do innego agenta." },
      { key: "unreachable_after_agents", label: "Kiedy lead jest nieosiągalny", base: "count", suffix: "agentów", hint: "Gdy tylu różnych agentów nie dodzwoni się do leada, trafia on do nieosiągalnych i przestaje krążyć." },
    ] },
    { title: "Ponowna próba po nieodebraniu", intro: "System sam proponuje agentowi termin kolejnej próby (agent może go zmienić jednym kliknięciem).", fields: [
      { key: "retry_after_first_minutes", label: "Po 1. nieodebranym telefonie", base: "min", units: ["min", "h", "d"], hint: "Ludzie często oddzwaniają lub odbierają przy kolejnej próbie tego samego dnia." },
      { key: "retry_after_second_minutes", label: "Po 2. nieodebranym telefonie", base: "min", units: ["min", "h", "d"], hint: "Najlepiej inny dzień niż poprzednia próba." },
      { key: "retry_after_next_minutes", label: "Po kolejnych nieodebranych", base: "min", units: ["min", "h", "d"], hint: "Dotyczy 3. i dalszych prób (jeśli limit prób jest wyższy niż 3)." },
      { key: "work_start_hour", label: "Dzień pracy zaczyna się o", base: "count", suffix: ":00", hint: "Termin wypadający przed tą godziną przesuwa się na jej początek." },
      { key: "work_end_hour", label: "Dzień pracy kończy się o", base: "count", suffix: ":00", hint: "Termin wypadający po tej godzinie przesuwa się na początek następnego dnia." },
    ] },
    { title: "Umówione oddzwonienia", intro: "Dotyczy leadów, którym obiecano kontakt (oddzwonić, zainteresowany).", fields: [
      { key: "callback_retry_minutes", label: "Gdy klient nie odebrał w umówionym terminie", base: "min", units: ["min", "h"], hint: "Za tyle system ustawia ponowną próbę, żeby lead nie został zaległy w nieskończoność." },
    ] },
    { title: "Zwroty do puli", intro: "Co dzieje się z leadami, którymi nikt się nie zajmuje.", fields: [
      { key: "claim_timeout_hours", label: "Nietknięty lead wraca do puli po", base: "h", units: ["h", "d"], hint: "Dotyczy leadów pobranych przez agenta, do których nie zadzwonił ani razu (uruchamia to komenda release_stale_leads)." },
      { key: "unreachable_cooldown_days", label: "Nieosiągalne leady można zwrócić do obiegu po", base: "d", units: ["d"], hint: "Przycisk „Zwróć gotowe do puli” w panelu managera działa dopiero po tylu dniach od ostatniej próby." },
    ] },
  ];
  const FIELDS = GROUPS.flatMap((g) => g.fields);
  const FACTOR = { min: { min: 1, h: 60, d: 1440 }, h: { h: 1, d: 24 }, d: { d: 1 } };   // ile jednostek bazowych ma 1 jednostka z listy
  const UNIT_LABEL = { min: "min", h: "godz.", d: "dni" };

  let saved = null, current = {};   // saved = ostatnio zapisane przez serwer, current = to, co jest w formularzu

  function bestUnit(f, value) {
    if (f.base === "count") return null;
    const units = f.units.slice().reverse();   // od największej
    for (const u of units) {
      const k = (FACTOR[f.base][u] || 1);
      if (value % k === 0) return u;
    }
    return f.units[0];
  }
  function fieldHtml(f) {
    const v = saved[f.key];
    if (f.base === "count") {
      return `<div class="rule"><label for="r-${f.key}">${esc(f.label)}</label>
        <div class="rule-input"><input id="r-${f.key}" data-key="${f.key}" type="number" class="select" min="0" step="1" inputmode="numeric" value="${v}"> <span class="muted">${esc(f.suffix)}</span></div>
        <small class="muted">${esc(f.hint)}</small><small class="form-error" id="e-${f.key}" hidden></small></div>`;
    }
    const unit = bestUnit(f, v), k = FACTOR[f.base][unit] || 1;
    const opts = f.units.map((u) => `<option value="${u}"${u === unit ? " selected" : ""}>${UNIT_LABEL[u]}</option>`).join("");
    return `<div class="rule"><label for="r-${f.key}">${esc(f.label)}</label>
      <div class="rule-input"><input id="r-${f.key}" data-key="${f.key}" type="number" class="select" min="0" step="1" inputmode="numeric" value="${v / k}">
        ${f.units.length > 1 ? `<select class="select" data-unit-for="${f.key}" aria-label="Jednostka">${opts}</select>` : `<span class="muted">${UNIT_LABEL[f.units[0]]}</span>`}</div>
      <small class="muted">${esc(f.hint)}</small><small class="form-error" id="e-${f.key}" hidden></small></div>`;
  }
  function render() {
    $("rules-groups").innerHTML = GROUPS.map((g) => `<section class="card rules-group"><h2>${esc(g.title)}</h2>
      <p class="muted small">${esc(g.intro)}</p><div class="rules-grid">${g.fields.map(fieldHtml).join("")}</div></section>`).join("");
    readForm(); refresh();
  }

  function unitOf(f) {
    const sel = document.querySelector(`[data-unit-for="${f.key}"]`);
    return sel ? sel.value : (f.units ? f.units[0] : null);
  }
  /* Wartości z formularza przeliczone na jednostki bazowe API */
  function readForm() {
    FIELDS.forEach((f) => {
      const raw = $("r-" + f.key).value.trim();
      const n = raw === "" ? NaN : Number(raw);
      current[f.key] = f.base === "count" ? n : n * (FACTOR[f.base][unitOf(f)] || 1);
    });
  }
  const dur = (minutes) => {
    if (minutes % 1440 === 0) { const d = minutes / 1440; return d === 1 ? "1 dzień" : `${d} dni`; }
    if (minutes % 60 === 0) { const h = minutes / 60; return h === 1 ? "1 godzinę" : `${h} godz.`; }
    return `${minutes} min`;
  };
  const hours = (h) => (h % 24 === 0 ? dur(h * 60) : `${h} godz.`);
  function summary(r) {
    const ok = FIELDS.every((f) => Number.isFinite(r[f.key]));
    if (!ok) return ["Uzupełnij wszystkie pola liczbami."];
    return [
      `Agent ma <b>${r.max_attempts_per_agent}</b> ${r.max_attempts_per_agent === 1 ? "próbę" : "próby"} dodzwonienia do nowego leada. Po ${r.max_attempts_per_agent}. nieodebranym telefonie lead wraca do puli.`,
      `Po 1. nieodebraniu system proponuje ponowną próbę za <b>${dur(r.retry_after_first_minutes)}</b>, po 2. za <b>${dur(r.retry_after_second_minutes)}</b>, po kolejnych za <b>${dur(r.retry_after_next_minutes)}</b> (zawsze między ${r.work_start_hour}:00 a ${r.work_end_hour}:00).`,
      `Kolejne próby wypadają o <b>różnych porach dnia</b>: najpierw rano, w środku dnia i po południu, a potem system wybiera godziny jak najdalsze od tych, o których już dzwoniono.`,
      `Gdy klient nie odbierze w umówionym terminie, ponowna próba jest za <b>${dur(r.callback_retry_minutes)}</b>.`,
      `Gdy <b>${r.unreachable_after_agents}</b> różnych agentów nie dodzwoni się, lead trafia do nieosiągalnych; wraca do obiegu po <b>${dur(r.unreachable_cooldown_days * 1440)}</b>.`,
      `Lead, do którego agent nie zadzwonił w ciągu <b>${hours(r.claim_timeout_hours)}</b> od pobrania, wraca do puli.`,
    ];
  }
  const dirty = () => FIELDS.some((f) => current[f.key] !== saved[f.key]);
  function refresh() {
    $("rules-summary").innerHTML = summary(current).map((t) => `<li>${t}</li>`).join("");
    $("rules-save").disabled = !dirty();
    $("rules-undo").disabled = !dirty();
  }
  function meta() {
    const when = saved.updated_at ? new Date(saved.updated_at).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    $("rules-meta").textContent = saved.updated_by_name ? `Ostatnia zmiana: ${saved.updated_by_name}, ${when}` : "Ustawienia domyślne – jeszcze nie zmieniane.";
  }
  function showErrors(data) {
    let general = "";
    document.querySelectorAll("#rules-form .form-error[id^=e-]").forEach((e) => { e.hidden = true; });
    Object.entries(data || {}).forEach(([k, v]) => {
      const msg = [].concat(v).join(" "), box = $("e-" + k);
      if (box) { box.textContent = msg; box.hidden = false; } else general += msg + " ";
    });
    $("rules-error").textContent = general.trim(); $("rules-error").hidden = !general.trim();
  }

  $("rules-form").addEventListener("input", () => { readForm(); showErrors({}); refresh(); });
  $("rules-form").addEventListener("change", () => { readForm(); refresh(); });
  $("rules-undo").addEventListener("click", () => { showErrors({}); render(); });
  $("rules-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    readForm();
    if (!FIELDS.every((f) => Number.isFinite(current[f.key]))) { showErrors({ _: "Uzupełnij wszystkie pola liczbami." }); return; }
    const body = {};
    FIELDS.forEach((f) => { if (current[f.key] !== saved[f.key]) body[f.key] = current[f.key]; });
    const btn = $("rules-save"); btn.disabled = true;
    try {
      const r = await api("/api/settings/", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showErrors(d.detail ? { _: d.detail } : d); refresh(); return; }
      saved = d; showErrors({}); render(); meta();
      $("rules-meta").textContent = "✓ Zapisano. " + $("rules-meta").textContent;
    } catch { showErrors({ _: "Brak połączenia z serwerem. Spróbuj ponownie." }); refresh(); }
  });
  $("rules-reset").addEventListener("click", async () => {
    if (!confirm("Przywrócić ustawienia domyślne (3 próby, ponowna próba po 2 godz. / 1 dniu / 2 dniach itd.)?")) return;
    try {
      const r = await api("/api/settings/reset/", { method: "POST" });
      if (!r.ok) { showErrors({ _: "Nie udało się przywrócić ustawień." }); return; }
      saved = await r.json(); showErrors({}); render(); meta();
    } catch { showErrors({ _: "Brak połączenia z serwerem." }); }
  });

  api("/api/settings/").then(async (r) => {
    if (!r.ok) throw new Error("http " + r.status);
    saved = await r.json(); render(); meta();
  }).catch(() => {
    $("page-error").textContent = "Nie udało się wczytać ustawień. Odśwież stronę (Ctrl+F5).";
    $("page-error").hidden = false; $("settings-area").hidden = true;
  });
};

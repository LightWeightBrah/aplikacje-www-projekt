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
    ] },
    { title: "Dni i godziny pracy", intro: "Terminy proponowane przez system i podpowiedzi przy dzwonieniu (np. „Jutro 10:00”) wypadają tylko w te dni i godziny.", fields: [
      { key: "work_start_hour", label: "Dzień pracy zaczyna się o", base: "count", suffix: ":00", hint: "Termin wypadający przed tą godziną przesuwa się na jej początek." },
      { key: "work_end_hour", label: "Dzień pracy kończy się o", base: "count", suffix: ":00", hint: "Termin wypadający po tej godzinie przesuwa się na następny dzień pracy." },
      { key: "work_days", label: "Dni pracy", type: "days", hint: "Np. w piątek po pracy system zaproponuje poniedziałek, a przy pracujących sobotach – sobotę." },
    ] },
    { title: "Umówione oddzwonienia", intro: "Dotyczy leadów, którym obiecano kontakt (oddzwonić, zainteresowany).", fields: [
      { key: "callback_retry_steps", label: "Gdy klient nie odebrał w umówionym terminie, kolejne próby", type: "steps", hint: "Po ostatniej próbie z listy kolejne są co tyle samo. Agent zawsze może sam wybrać inny termin." },
    ] },
    { title: "Zwroty do puli", intro: "Co dzieje się z leadami, którymi nikt się nie zajmuje.", fields: [
      { key: "claim_timeout_hours", label: "Nietknięty lead wraca do puli po", base: "h", units: ["h", "d"], hint: "Dotyczy leadów pobranych przez agenta, do których nie zadzwonił ani razu (uruchamia to komenda release_stale_leads)." },
      { key: "unreachable_cooldown_days", label: "Nieosiągalne leady można zwrócić do obiegu po", base: "d", units: ["d"], hint: "Przycisk „Zwróć gotowe do puli” w panelu managera działa dopiero po tylu dniach od ostatniej próby." },
    ] },
  ];
  const FIELDS = GROUPS.flatMap((g) => g.fields);
  const NUM_FIELDS = FIELDS.filter((f) => !f.type);
  const DAYS = [[1, "Pon"], [2, "Wt"], [3, "Śr"], [4, "Czw"], [5, "Pt"], [6, "Sob"], [7, "Niedz"]];
  const DAY_NAME = { 1: "pon", 2: "wt", 3: "śr", 4: "czw", 5: "pt", 6: "sob", 7: "niedz" };
  const STEPS_MAX = 8;
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  // karta „Losowanie nowych firm” (radio w szablonie) zapisuje się razem z zasadami
  const KEYS = FIELDS.map((f) => f.key).concat("companies_last");
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
  const STEP_UNITS = ["min", "h", "d"];
  function stepHtml(minutes, i) {
    const f = { base: "min", units: STEP_UNITS };
    const unit = Number.isFinite(minutes) && minutes > 0 ? bestUnit(f, minutes) : "min", k = FACTOR.min[unit];
    const opts = STEP_UNITS.map((u) => `<option value="${u}"${u === unit ? " selected" : ""}>${UNIT_LABEL[u]}</option>`).join("");
    return `<li class="step"><span class="step-n">${i + 1}. za</span>
      <input type="number" class="select" data-step min="0" step="1" inputmode="numeric" value="${Number.isFinite(minutes) ? minutes / k : ""}" aria-label="${i + 1}. próba za">
      <select class="select" data-step-unit aria-label="Jednostka">${opts}</select>
      <button type="button" class="step-del" data-step-del aria-label="Usuń ${i + 1}. próbę" title="Usuń tę próbę">×</button></li>`;
  }
  function paintSteps(list) {
    const box = $("r-callback_retry_steps");
    box.innerHTML = list.map(stepHtml).join("");
    box.querySelectorAll("[data-step-del]").forEach((b) => { b.disabled = list.length <= 1; });
    $("step-add").disabled = list.length >= STEPS_MAX;
  }
  function fieldHtml(f) {
    const v = saved[f.key];
    if (f.type === "days") {
      const on = new Set(String(v || "12345"));
      return `<div class="rule rule-span2"><span class="rule-label" id="l-${f.key}">${esc(f.label)}</span>
        <div class="day-chips" role="group" aria-labelledby="l-${f.key}">${DAYS.map(([n, l]) => `<label class="day-chip"><input type="checkbox" data-day="${n}"${on.has(String(n)) ? " checked" : ""}><span>${l}</span></label>`).join("")}</div>
        <small class="muted">${esc(f.hint)}</small><small class="form-error" id="e-${f.key}" hidden></small></div>`;
    }
    if (f.type === "steps") {
      return `<div class="rule rule-wide"><span class="rule-label">${esc(f.label)}</span>
        <div class="steps-row"><ol class="steps" id="r-${f.key}"></ol><button type="button" class="btn-mini" id="step-add">+ Dodaj próbę</button></div>
        <small class="muted">${esc(f.hint)}</small><small class="form-error" id="e-${f.key}" hidden></small></div>`;
    }
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
    paintSteps(saved.callback_retry_steps && saved.callback_retry_steps.length ? saved.callback_retry_steps : [15, 60, 120, 1440]);
    const draw = document.querySelector(`input[name="draw"][value="${saved.companies_last ? "last" : "random"}"]`);
    if (draw) draw.checked = true;
    readForm(); refresh();
  }

  function unitOf(f) {
    const sel = document.querySelector(`[data-unit-for="${f.key}"]`);
    return sel ? sel.value : (f.units ? f.units[0] : null);
  }
  /* Wartości z formularza przeliczone na jednostki bazowe API */
  function readSteps() {
    return [...document.querySelectorAll("#r-callback_retry_steps .step")].map((li) => {
      const raw = li.querySelector("[data-step]").value.trim(), n = raw === "" ? NaN : Number(raw);
      return n * FACTOR.min[li.querySelector("[data-step-unit]").value];
    });
  }
  function readForm() {
    current.work_days = [...document.querySelectorAll("[data-day]:checked")].map((c) => c.dataset.day).join("");
    current.callback_retry_steps = readSteps();
    NUM_FIELDS.forEach((f) => {
      const raw = $("r-" + f.key).value.trim();
      const n = raw === "" ? NaN : Number(raw);
      current[f.key] = f.base === "count" ? n : n * (FACTOR[f.base][unitOf(f)] || 1);
    });
    const draw = document.querySelector('input[name="draw"]:checked');
    current.companies_last = draw ? draw.value === "last" : saved.companies_last;
  }
  const dur = (minutes) => {
    if (minutes % 1440 === 0) { const d = minutes / 1440; return d === 1 ? "1 dzień" : `${d} dni`; }
    if (minutes % 60 === 0) { const h = minutes / 60; return h === 1 ? "1 godzinę" : `${h} godz.`; }
    return `${minutes} min`;
  };
  const hours = (h) => (h % 24 === 0 ? dur(h * 60) : `${h} godz.`);
  /* „pon–pt”, „pon–sob”, „pon, śr, pt” */
  function daysText(str) {
    const d = [...new Set(String(str || ""))].map(Number).filter((n) => n >= 1 && n <= 7).sort((a, b) => a - b);
    if (!d.length) return "brak dni";
    const contiguous = d.every((n, i) => i === 0 || n === d[i - 1] + 1);
    return contiguous && d.length > 2 ? `${DAY_NAME[d[0]]}–${DAY_NAME[d[d.length - 1]]}` : d.map((n) => DAY_NAME[n]).join(", ");
  }
  function stepsText(steps) {
    const parts = steps.map((m, i) => (i === 0 ? `za <b>${dur(m)}</b>` : `potem za <b>${dur(m)}</b>`));
    return `${parts.join(", ")}, a dalej co <b>${dur(steps[steps.length - 1])}</b>`;
  }
  /* 1 próbę, 2–4 próby, 5+ prób (12–14 też „prób”) */
  const tries = (n) => (n === 1 ? "próbę" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "próby" : "prób");
  const allOk = (r) => NUM_FIELDS.every((f) => Number.isFinite(r[f.key]))
    && r.callback_retry_steps.length > 0 && r.callback_retry_steps.every(Number.isFinite);
  function summary(r) {
    if (!allOk(r)) return ["Uzupełnij wszystkie pola liczbami."];
    return [
      `Agent ma <b>${r.max_attempts_per_agent}</b> ${tries(r.max_attempts_per_agent)} dodzwonienia do nowego leada. Po ${r.max_attempts_per_agent}. nieodebranym telefonie lead wraca do puli.`,
      `Po 1. nieodebraniu system proponuje ponowną próbę za <b>${dur(r.retry_after_first_minutes)}</b>, po 2. za <b>${dur(r.retry_after_second_minutes)}</b>, po kolejnych za <b>${dur(r.retry_after_next_minutes)}</b> (zawsze w dni pracy: <b>${daysText(r.work_days)}</b>, ${r.work_start_hour}:00–${r.work_end_hour}:00).`,
      `Kolejne próby wypadają o <b>różnych porach dnia</b>: najpierw rano, w środku dnia i po południu, a potem system wybiera godziny jak najdalsze od tych, o których już dzwoniono.`,
      `Gdy klient nie odbierze w umówionym terminie, system ponawia próbę ${stepsText(r.callback_retry_steps)}`.replace(/([^.])$/, "$1."),
      `Gdy <b>${r.unreachable_after_agents}</b> różnych agentów nie dodzwoni się, lead trafia do nieosiągalnych; wraca do obiegu po <b>${dur(r.unreachable_cooldown_days * 1440)}</b>.`,
      `Lead, do którego agent nie zadzwonił w ciągu <b>${hours(r.claim_timeout_hours)}</b> od pobrania, wraca do puli.`,
      r.companies_last ? `Nowe firmy: <b>najpierw jednoosobowe</b>, spółki dopiero, gdy jednoosobowych zabraknie.` : `Nowe firmy losowane są <b>wszystkie razem</b> (jednoosobowe i spółki).`,
    ];
  }
  const dirty = () => KEYS.some((k) => !same(current[k], saved[k]));
  function refresh() {
    $("rules-summary").innerHTML = summary(current).map((t) => `<li>${t}</li>`).join("");
    $("rules-save").disabled = !dirty();
    $("rules-undo").disabled = !dirty();
    $("rules-bar").classList.toggle("dirty", dirty());
  }
  function meta() {
    const when = saved.updated_at ? new Date(saved.updated_at).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    $("rules-meta").textContent = saved.updated_by_name ? `Ostatnia zmiana zasad: ${saved.updated_by_name}, ${when}` : "Zasady domyślne – jeszcze nie zmieniane.";
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
  /* „+ Dodaj próbę” i „×” przy próbie po nieodebranym umówionym terminie */
  $("rules-form").addEventListener("click", (e) => {
    const del = e.target.closest("[data-step-del]"), add = e.target.closest("#step-add");
    if (!del && !add) return;
    const list = readSteps();
    if (del) list.splice([...document.querySelectorAll("#r-callback_retry_steps .step")].indexOf(del.closest(".step")), 1);
    if (add && list.length < STEPS_MAX) list.push(Number.isFinite(list[list.length - 1]) ? list[list.length - 1] : 60);
    paintSteps(list); readForm(); refresh();
    if (add) { const inputs = document.querySelectorAll("#r-callback_retry_steps [data-step]"); inputs[inputs.length - 1].focus(); }
  });
  $("rules-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    readForm();
    if (!allOk(current)) { showErrors({ _: "Uzupełnij wszystkie pola liczbami." }); return; }
    if (!current.work_days) { showErrors({ work_days: "Zaznacz co najmniej jeden dzień pracy." }); return; }
    const body = {};
    KEYS.forEach((k) => { if (!same(current[k], saved[k])) body[k] = current[k]; });
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
    if (!confirm("Przywrócić ustawienia domyślne (5 prób, ponowna próba po 2 godz. / 1 dniu / 2 dniach, praca pon–pt 9–17, po nieodebranym terminie: 15 min, 1 h, 2 h, 1 dzień)?")) return;
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

/* Wspólne elementy pracy z kontaktem, używane w kolejce i w planie dnia:
   linia „Ostatnio: …”, przycisk kopiowania numeru i szybkie frazy do notatki. */
(() => {
  const { esc } = CRM;
  const OUT_LABEL = { no_answer: "Nie odebrał", callback: "Oddzwonić", interested: "Zainteresowany", sold: "Sprzedane",
    refused: "Odmowa", wrong_number: "Zły numer", note: "Notatka" };
  const OUT_COLOR = { no_answer: "#64748b", callback: "#f59e0b", interested: "#4f46e5", sold: "#22c55e",
    refused: "#ef4444", wrong_number: "#a855f7", note: "#94a3b8" };
  const hhmm = (d) => d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  /* „dziś 22:01”, „wczoraj 14:10”, „pt 23.10, 10:05” */
  function shortWhen(iso) {
    const d = new Date(iso), now = new Date(), yest = new Date(now.getTime() - 86400000);
    if (d.toDateString() === now.toDateString()) return `dziś ${hhmm(d)}`;
    if (d.toDateString() === yest.toDateString()) return `wczoraj ${hhmm(d)}`;
    const wd = d.toLocaleDateString("pl-PL", { weekday: "short" }).replace(".", "");
    return `${wd} ${d.toLocaleDateString("pl-PL", { day: "numeric", month: "numeric" })}, ${hhmm(d)}`;
  }
  /* Jedna linia o ostatnim kontakcie z leadem (bez samych notatek); pusta, gdy nikt jeszcze nie dzwonił */
  function lastContact(l, meId) {
    if (!l || !l.last_contact_outcome || !l.last_contact_at) return "";
    const who = l.last_contact_agent && meId && l.last_contact_agent !== meId ? " · inny agent" : "";
    return `<small class="last-contact" style="--oc:${OUT_COLOR[l.last_contact_outcome] || "#64748b"}" title="Ostatni kontakt z tym leadem">Ostatnio: <b>${esc(OUT_LABEL[l.last_contact_outcome] || l.last_contact_outcome)}</b> · ${esc(shortWhen(l.last_contact_at))}${who}</small>`;
  }
  const ICON_COPY = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>';
  const copyButton = (text, what = "numer") => `<button type="button" class="copy-btn" data-copy="${esc(text)}" title="Kopiuj ${what}" aria-label="Kopiuj ${what} ${esc(text)}">${ICON_COPY}<span>Kopiuj</span></button>`;

  /* Kopiowanie jednym kliknięciem; działa też bez HTTPS (zapasowa metoda przez ukryte pole) */
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* spróbujemy zapasowo */ }
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    const label = btn.querySelector("span");
    const ok = await copyText(btn.dataset.copy);
    btn.classList.toggle("copied", ok);
    if (label) label.textContent = ok ? "Skopiowano" : "Nie udało się";
    clearTimeout(btn._t);
    btn._t = setTimeout(() => { btn.classList.remove("copied"); if (label) label.textContent = "Kopiuj"; }, 1600);
  }, true);

  /* Szybkie frazy do pola komentarza (klik dopisuje frazę, drugi raz tej samej nie dubluje) */
  const PHRASES = ["Wysłać ofertę mailem", "Wróci za tydzień", "Dzwonić po urlopie"];
  const phraseButtons = () => `<div class="phrases" role="group" aria-label="Szybkie frazy">${PHRASES.map((p, i) => `<button type="button" data-phrase="${i}">${esc(p)}</button>`).join("")}</div>`;
  function wirePhrases(root, textarea) {
    root.querySelectorAll("[data-phrase]").forEach((b) => b.addEventListener("click", () => {
      const phrase = PHRASES[Number(b.dataset.phrase)], cur = textarea.value.trim();
      if (!cur) textarea.value = phrase;
      else if (!cur.toLowerCase().includes(phrase.toLowerCase())) textarea.value = cur.replace(/[.;,]$/, "") + "; " + phrase.toLowerCase();
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }));
  }

  Object.assign(CRM, { OUT_LABEL, OUT_COLOR, shortWhen, lastContact, copyButton, copyText, phraseButtons, wirePhrases });
})();

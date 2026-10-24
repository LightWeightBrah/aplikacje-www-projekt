/* Wyszukiwarka leadów u góry strony (na każdej stronie panelu).
   Działa na żywo: w trakcie pisania wysyła zdarzenie „crm:search”. Strona kolejki je obsługuje: lista po lewej
   zawęża się do znalezionych leadów, a najlepiej pasujący (dokładny numer / NIP) otwiera się od razu po prawej,
   z informacją, po czym go znaleziono. Na innych stronach Enter przechodzi do kolejki z wynikami.
   Skrót: klawisz „/” ustawia kursor w polu. Agent szuka w swoich leadach, manager we wszystkich. */
(() => {
  const { esc } = CRM;
  const CHECK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>';
  const WHY = {   // [dokładne, fragment]
    phone: ["Pasuje numer telefonu", "Pasuje fragment numeru"],
    nip: ["Pasuje NIP", "Pasuje fragment NIP"],
    krs: ["Pasuje KRS", "Pasuje fragment KRS"],
    name: ["Pasuje nazwisko", "Pasuje nazwisko"],
    company: ["Pasuje firma", "Pasuje firma"],
    email: ["Pasuje e-mail", "Pasuje e-mail"],
  };
  const isNumeric = (q) => /^[\d\s+\-().]+$/.test(q);
  function localDigits(v) {   // jak w backendzie: bez prefiksu kraju, same cyfry
    let d = String(v || "").replace(/\D/g, "");
    if (d.startsWith("00")) d = d.slice(2);
    if (d.length === 11 && d.startsWith("48")) d = d.slice(2);
    return d;
  }
  /* Zaznacza w tekście ciąg cyfr zapytania (pomijając spacje i myślniki w numerze) */
  function markDigits(text, digits) {
    const chars = [...String(text || "")], pos = [];
    let joined = "";
    chars.forEach((c, i) => { if (/\d/.test(c)) { pos.push(i); joined += c; } });
    const at = digits ? joined.indexOf(digits) : -1;
    if (at < 0) return esc(text);
    const from = pos[at], to = pos[at + digits.length - 1];
    return esc(chars.slice(0, from).join("")) + "<mark>" + esc(chars.slice(from, to + 1).join("")) + "</mark>" + esc(chars.slice(to + 1).join(""));
  }
  function markText(text, q) {
    const s = String(text || ""), at = q ? s.toLowerCase().indexOf(q.toLowerCase()) : -1;
    if (at < 0) return esc(s);
    return esc(s.slice(0, at)) + "<mark>" + esc(s.slice(at, at + q.length)) + "</mark>" + esc(s.slice(at + q.length));
  }
  /* „✓ Pasuje numer telefonu: 500 000 002” z zaznaczonym dopasowaniem (używa kolejka) */
  CRM.searchMatch = function searchMatch(l, q) {
    const field = l.matched_on || "phone", exact = l.match === "exact", digits = localDigits(q);
    let value;
    if (field === "phone") value = markDigits(l.phone, isNumeric(q) ? digits : "");
    else if (field === "nip") value = markDigits(l.company_nip, digits);
    else if (field === "krs") value = markDigits(l.company_krs, digits);
    else if (field === "name") value = markText(l.contact_name, q);
    else if (field === "company") value = markText(l.company_name, q);
    else value = markText(l.email, q);
    return `<span class="found-why${exact ? "" : " part"}">${CHECK}<span>${WHY[field][exact ? 0 : 1]}:</span> <span class="found-val">${value}</span></span>`;
  };
})();

CRM.initSearch = function initSearch() {
  const { $ } = CRM;
  const input = $("gsearch-input"), wrap = $("gsearch"), clearBtn = $("gsearch-clear");
  if (!input || !wrap) return;
  const isNumeric = (q) => /^[\d\s+\-().]+$/.test(q);
  let timer = null;

  /* true = żadna strona nie obsłużyła wyszukiwania na żywo (czyli nie jesteśmy w kolejce) */
  function dispatch(q, enter) {
    const notHandled = document.dispatchEvent(new CustomEvent("crm:search", { detail: { q, enter: !!enter }, cancelable: true }));
    wrap.classList.toggle("go-mode", notHandled);
    return notHandled;
  }
  function reset() { clearTimeout(timer); input.value = ""; dispatch("", false); }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { dispatch("", false); return; }
    timer = setTimeout(() => dispatch(q, false), isNumeric(q) ? 350 : 220);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { reset(); input.blur(); return; }
    if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) return;
      if (dispatch(q, true)) location.href = "/crm/queue/?find=" + encodeURIComponent(q);
    }
  });
  clearBtn.addEventListener("click", () => { reset(); input.focus(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
    e.preventDefault(); input.focus(); input.select();
  });
};

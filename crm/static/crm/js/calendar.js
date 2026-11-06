/* Solid CRM – kalendarz miesięczny: terminy kontaktów z nazwą firmy, kolor = rodzaj kontaktu. */
(() => {
  const { api, esc, $ } = CRM;
  const SEG_COLOR = { offer: "var(--seg-offer)", callback: "var(--seg-callback)", retry: "var(--seg-retry)", new: "var(--seg-new)" };
  const segOf = (l) => (["offer", "interested"].includes(l.status) ? "offer" : l.status === "callback" ? "callback" : l.attempts_count ? "retry" : "new");
  const DOW = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Niedz"];
  const MAX_PER_DAY = 4;
  const p2 = (n) => String(n).padStart(2, "0");
  const key = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
  let month = new Date(); month.setDate(1);
  const expanded = new Set();

  async function load() {
    $("m-title").textContent = month.toLocaleDateString("pl-PL", { month: "long", year: "numeric" });
    const r = await api(`/api/my-leads/month/?month=${month.getFullYear()}-${p2(month.getMonth() + 1)}`);
    const leads = r.ok ? await r.json() : [];
    const byDay = {};
    leads.forEach((l) => { const k = key(new Date(l.next_contact_at)); (byDay[k] = byDay[k] || []).push(l); });
    const start = new Date(month); start.setDate(1 - ((month.getDay() + 6) % 7));
    const today = key(new Date());
    let html = DOW.map((d) => `<div class="dow">${d}</div>`).join("");
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      if (i >= 35 && d.getMonth() !== month.getMonth()) break;
      const k = key(d), items = byDay[k] || [];
      const shown = expanded.has(k) ? items : items.slice(0, MAX_PER_DAY);
      html += `<div class="cal-day${d.getMonth() !== month.getMonth() ? " other" : ""}${k === today ? " today" : ""}">
        <div class="cal-num"><span>${d.getDate()}</span>${items.length ? `<small>${items.length}</small>` : ""}</div>
        ${shown.map((l) => { const t = new Date(l.next_contact_at), late = t < new Date() && segOf(l) !== "retry";
          return `<button type="button" class="cal-ev${late ? " late" : ""}" style="--c:${SEG_COLOR[segOf(l)]}" data-id="${l.id}" title="${esc(CRM.leadTitle(l))}${l.next_contact_note ? " – " + esc(l.next_contact_note) : ""}"><b>${p2(t.getHours())}:${p2(t.getMinutes())}</b><span>${esc(CRM.leadTitle(l))}</span></button>`; }).join("")}
        ${items.length > shown.length ? `<button type="button" class="cal-more" data-more="${k}">+ ${items.length - shown.length} więcej</button>` : ""}</div>`;
    }
    $("cal-grid").innerHTML = html;
  }
  document.addEventListener("click", (e) => {
    const ev = e.target.closest("[data-id]"); if (ev) { location.href = `/crm/queue/?lead=${ev.dataset.id}`; return; }
    const more = e.target.closest("[data-more]"); if (more) { expanded.add(more.dataset.more); load(); }
  });
  $("m-prev").addEventListener("click", () => { month.setMonth(month.getMonth() - 1); load(); });
  $("m-next").addEventListener("click", () => { month.setMonth(month.getMonth() + 1); load(); });
  $("m-today").addEventListener("click", () => { month = new Date(); month.setDate(1); load(); });
  load();
})();

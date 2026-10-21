/* Wpisywanie terminu z klawiatury: "jutro 9 55", "25.10 10 23", "pon 8", "za 2h", "1023". */
const WHEN = (() => {
  const p2 = (n) => String(n).padStart(2, "0");
  const WEEKDAYS = [["ni", 0], ["pon", 1], ["wt", 2], ["śr", 3], ["sr", 3], ["czw", 4], ["pi", 5], ["pt", 5], ["so", 6]];

  function parseTime(tokens) {
    const t = tokens.join(" ");
    if (!t) return [9, 0];                                   // sama data = 9:00
    let m = t.match(/^(\d{1,2})[:.](\d{2})$/) || t.match(/^(\d{1,2}) (\d{1,2})$/);
    if (!m && /^\d{3,4}$/.test(t)) m = [t, t.slice(0, -2), t.slice(-2)];  // 955 -> 9:55, 1023 -> 10:23
    if (!m && /^\d{1,2}$/.test(t)) m = [t, t, "0"];                       // 9 -> 9:00
    if (!m) return null;
    const h = Number(m[1]), min = Number(m[2]);
    return h < 24 && min < 60 ? [h, min] : null;
  }

  /* Zwraca Date albo null, gdy tekst jest niezrozumiały. Rok jest uzupełniany automatycznie. */
  function parseWhen(raw, now = new Date()) {
    const s = String(raw).toLowerCase().trim().replace(/\s+/g, " ");
    if (!s) return null;

    const rel = s.match(/^za (\d+) ?(min|m|godz|g|h|dni|dzie|d|tyg|t)\S*$/);
    if (rel) {
      const n = Number(rel[1]), u = rel[2][0];
      if (u === "m") return new Date(now.getTime() + n * 60000);
      if (u === "g" || u === "h") return new Date(now.getTime() + n * 3600000);
      const d = new Date(now); d.setDate(d.getDate() + n * (u === "t" ? 7 : 1)); d.setHours(9, 0, 0, 0);
      return d;
    }

    const tokens = s.split(" "), first = tokens[0];
    let date = new Date(now); date.setHours(0, 0, 0, 0);
    let rest = tokens, hasDate = true;
    const dm = first.match(/^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$/);

    if (["dziś", "dzis", "dzisiaj"].includes(first)) rest = tokens.slice(1);
    else if (first === "jutro") { date.setDate(date.getDate() + 1); rest = tokens.slice(1); }
    else if (first === "pojutrze") { date.setDate(date.getDate() + 2); rest = tokens.slice(1); }
    else if (!dm && WEEKDAYS.some(([p]) => first.startsWith(p))) {
      const w = WEEKDAYS.find(([p]) => first.startsWith(p))[1];
      date.setDate(date.getDate() + (((w - date.getDay() + 7) % 7) || 7));   // najbliższy taki dzień tygodnia
      rest = tokens.slice(1);
    } else if (dm) {
      const day = Number(dm[1]), mon = Number(dm[2]);
      let year = dm[3] ? Number(dm[3]) : date.getFullYear();
      if (year < 100) year += 2000;
      const cand = new Date(year, mon - 1, day);
      if (cand.getMonth() === mon - 1 && cand.getDate() === day) {
        if (!dm[3] && cand < date) cand.setFullYear(year + 1);               // data bez roku już minęła -> przyszły rok
        date = cand; rest = tokens.slice(1);
      } else hasDate = false;                                                // np. "10.23" to godzina, nie data
    } else hasDate = false;

    const time = parseTime(rest);
    if (!time) return null;
    date.setHours(time[0], time[1], 0, 0);
    if (!hasDate && date <= now) date.setDate(date.getDate() + 1);            // sama godzina, która minęła -> jutro
    return date;
  }

  const fmtInput = (d) => `${p2(d.getDate())}.${p2(d.getMonth() + 1)}.${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
  const fmtLong = (d) => d.toLocaleString("pl-PL", { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
  const fmtNative = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}`;
  return { parseWhen, fmtInput, fmtLong, fmtNative };
})();

/* Service worker LeadCRM: obsługuje kliknięcie powiadomienia systemowego.
   Bez niego Chrome po kliknięciu powiadomienia, którego strona już nie pamięta (po przejściu
   na inną podstronę), otwierał goły adres serwera (/) i pokazywał błąd 404. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = new URL(data.url || "/crm/plan/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        await w.focus();
        if ("navigate" in w) { try { await w.navigate(target); } catch { /* np. inna karta */ } }
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});

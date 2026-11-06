# Solid Dialer (Android)

Aplikacja na telefon agenta. Klikasz „Zadzwoń” w Solid CRM na komputerze, a telefon sam wybiera numer z karty SIM.
Po rozmowie aplikacja odczytuje z rejestru połączeń, czy klient odebrał i ile trwała rozmowa, i odsyła to do CRM.
Z tych danych CRM liczy minuty rozmów, w tym cel 15 minut na każdą godzinę pracy.

## Zbudowanie i instalacja (Android Studio)
1. Otwórz w Android Studio folder `dialer-android` (File → Open). Przy pierwszym otwarciu Studio samo pobierze Gradle i narzędzia (kilka minut).
2. Na telefonie włącz **Opcje programisty** (Ustawienia → O telefonie → 7× „Numer kompilacji”) i w nich **Debugowanie USB**.
3. Podłącz telefon kablem, zaakceptuj na telefonie „Zezwól na debugowanie”.
4. W Android Studio wybierz telefon u góry i kliknij zielony ▶ (Run). Aplikacja zainstaluje się i uruchomi.
5. Plik do rozesłania innym: Build → Build App Bundle(s) / APK(s) → Build APK(s). Plik `app-debug.apk` instaluje się na telefonie po zgodzie na „instalowanie z nieznanych źródeł”.

## Parowanie z CRM
1. W CRM: menu konta (prawy górny róg) → **Telefon (dialer)…** → **Pokaż kod parowania**.
2. W aplikacji wpisz adres CRM i 6-cyfrowy kod → **Połącz z CRM**. Zgódź się na dzwonienie, rejestr połączeń i powiadomienia.
3. Na telefonie widać powiadomienie „Dialer gotowy do pracy”, a w CRM na dolnym pasku „Telefon połączony”.

Testy w domowej sieci: CRM uruchomiony na komputerze komendą `python manage.py runserver 0.0.0.0:8000`,
a w aplikacji adres `http://ADRES-IP-KOMPUTERA:8000` (telefon i komputer w tej samej sieci Wi-Fi).
Na serwerze docelowo używamy `https://`.

## API (dla porządku)
- `POST /api/dialer/pair/` `{code, name}` → `{token}`
- `GET /api/dialer/poll/` (nagłówek `X-Dialer-Token`) → `{id, phone, name}` albo 204
- `POST /api/dialer/calls/{id}/finish/` `{answered, duration_seconds}`

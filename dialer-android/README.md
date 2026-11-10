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
- `GET /api/dialer/poll/?sms=1` (nagłówek `X-Dialer-Token`) → `{type: "call", id, phone, name}`,
  `{type: "sms", id, phone, text, name}` albo 204 (bez `sms=1` tylko połączenia)
- `POST /api/dialer/calls/{id}/finish/` `{answered, duration_seconds}`
- `POST /api/dialer/sms/{id}/done/` `{ok, error}`
- `POST /api/dialer/calls/{id}/recording/?duration=SEK` – surowy plik nagrania (`audio/ogg` albo `audio/mp4`)
- poll dostaje `?rec=1|0&v=WERSJA` (czy da się nagrywać, wersja aplikacji), a zadanie `call` ma pole `record`
- od 1.3: `POST /api/dialer/phone/ring/` `{phone}` (dzwoni klient), `POST /api/dialer/phone/log/` `{calls: [{phone, type: in|out|missed, seconds, at}]}`
  (rejestr połączeń, których CRM nie zlecał), `POST /api/dialer/phone/sms/` `{sender, text, at}` (przychodzący SMS)
- od 1.4: w trakcie połączenia `GET /api/dialer/calls/{id}/hangup/` → `{hangup}`; gdy `true`, aplikacja kończy połączenie

## Telefon w CRM (od 1.3) i „Rozłącz” (od 1.4)
- Gdy dzwoni klient, CRM od razu pokazuje kartę „Dzwoni do Ciebie: firma”.
- Po rozmowie aplikacja wysyła do CRM połączenia z rejestru (klient oddzwonił, numer wybrany ręcznie). CRM zapisuje
  tylko połączenia z numerami klientów i liczy je do minut rozmów – prywatne rozmowy nie trafiają do CRM.
- Przychodzące SMS-y: CRM zapisuje tylko SMS-y od klientów i wiadomości od sieci o numerze klienta („numer już dostępny”).
- „Rozłącz” w CRM kończy połączenie wybrane z CRM (zgoda „Telefon” – kończenie połączeń; nie każdy telefon na to pozwala).
- Na Xiaomi: Ustawienia → Aplikacje → Solid Dialer → **Autostart** włączony i **Oszczędzanie baterii: bez ograniczeń**,
  inaczej telefon usypia aplikację w tle.

## SMS z przypomnieniem
Przed umówionym telefonem aplikacja sama wysyła klientowi SMS z przypomnieniem (ile wcześniej i treść ustawia
manager w CRM: Ustawienia → „SMS z przypomnieniem”). SMS idzie z karty SIM telefonu, tak jakbyś napisał go ręcznie
(widać go potem w „Wysłanych” w Wiadomościach), więc kosztuje tyle co zwykły SMS w abonamencie, bez żadnej bramki.
Działa tylko, gdy telefon jest włączony i dialer pracuje. Przy pierwszym uruchomieniu nowej wersji zgódź się na „SMS-y”.

## Nagrywanie rozmów (od 1.2, opcjonalne)
Gdy manager włączy nagrywanie w CRM (i połączy Dysk Google), aplikacja nagrywa rozmowy wybrane z CRM, a po rozmowie
wysyła nagranie do CRM. Na telefonie raz: „Włącz nagrywanie rozmów” → zgoda na mikrofon → w ułatwieniach dostępu włączyć
„Solid Dialer – nagrywanie rozmów” (Android 13+: najpierw w informacjach o aplikacji ⋮ → „Zezwól na ustawienia z ograniczeniami”).
Pełny opis i podłączenie Dysku Google: `docs/NAGRYWANIE.md`.

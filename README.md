# Solid CRM – CRM dla zespołu sprzedaży telefonicznej

Projekt zaliczeniowy z przedmiotu **Aplikacje WWW** (UWM Olsztyn, 2026), rozwijany dalej jako prawdziwe narzędzie pracy dla zespołu sprzedaży.

- **Autor:** Julian Łabanowski
- **Język i framework:** Python 3.13, Django 6.1
- **API:** Django REST Framework, SimpleJWT (logowanie tokenem), drf-spectacular (Swagger), django-filter (filtrowanie)
- **Excel:** openpyxl (import i eksport `.xlsx`)
- **Baza danych:** SQLite
- **Frontend:** szablony Django, własny CSS i zwykły JavaScript (bez Node.js), motyw ciemny (domyślny), jasny albo auto
- **Telefon:** aplikacja na Androida **Solid Dialer** (`dialer-android/`, Java): „Zadzwoń” w CRM wybiera numer na telefonie agenta i odsyła czas rozmowy

![Ekran Dzwonienie](docs/queue.png)

## Opis biznesowy

Solid CRM jest dla zespołu, który dzwoni do firm (np. z baz CEIDG i KRS) i sprzedaje usługi telekomunikacyjne dla firm: przeniesienie numerów, internet, telewizję. Są dwie role: **agenci** dzwonią, a **manager** pilnuje baz, zespołu i wyników.

- Manager wgrywa bazę firm z Excela (paczka `LeadBatch`). Bazę można dać jednemu agentowi („jego Excel”) albo do **wspólnej puli**.
- Agent nie wybiera klientów sam. Klika **„Następny klient”** i system podaje najważniejszy kontakt w tej kolejności: 1) follow-upy po ofercie i zainteresowani, 2) umówione oddzwonienia, 3) ponowne próby u tych, którzy nie odebrali, 4) nowe firmy losowane z jego bazy, a potem z puli. Przypisanie jest atomowe, więc dwóch agentów nie dostanie tego samego klienta.
- Po rozmowie agent wybiera wynik: nie odebrał, oddzwonić, zainteresowany, **wyślij ofertę**, **sprzedane**, odmowa, zły numer albo „pomiń firmę”. System sam ustawia następny krok: termin, ponowną próbę o innej porze dnia, follow-up oferty.
- **Oferta**: agent wybiera taryfę, liczbę numerów, okres umowy (24 lub 36 miesięcy) i promocję. CRM na żywo liczy zobowiązanie miesięczne i prowizję, a gotową ofertę wysyła mailem albo kopiuje do poczty.
- **Prowizja** = zobowiązanie miesięczne × mnożnik (domyślnie × 6 przy 24 miesiącach, × 9 przy 36). Mnożniki ustawia manager.
- **Minuty rozmów** liczy aplikacja Solid Dialer z rejestru połączeń telefonu. Cel: **15 minut rozmów na każdą godzinę pracy**. Ranking jest po minutach.
- **Dzień pracy**: zalogowanie = przyjście, wylogowanie = wyjście. Przerwy (co najmniej 15 minut bez ruchu) są tylko pokazywane, nie odejmuje się ich. Odświeżanie strony w tle nie liczy się jako praca.
- Zasada nadrzędna: **klient nigdy nie zostaje bez następnego kroku**. Po wyczerpaniu prób klient wraca do puli, a po kilku agentach bez skutku trafia do „nieosiągalnych”.
- Agent widzi swoich klientów i swoje wyniki. Manager widzi całą pracę zespołu (także agenta, który już odszedł), zarządza kontami, taryfami, zasadami i urządzeniami.

## Modele

| Model | Rola |
|---|---|
| `Product` | taryfa lub produkt: cena bez i z promocją, dopłata za każdy kolejny numer |
| `Company` | firma klienta: nazwa, NIP, KRS, REGON, adres |
| `LeadBatch` | baza (paczka) leadów, np. import z Excela; może należeć do jednego agenta |
| `Lead` | klient do obdzwonienia: opiekun (`owner`, brak = pula), status, próby, termin kolejnego kontaktu, stałe informacje o kliencie (kto i kiedy je zmienił) |
| `Activity` | historia rozmów: agent, wynik, komentarz, terminy |
| `SaleItem` | pozycja sprzedaży: taryfa, liczba numerów, okres, cena i prowizja z chwili sprzedaży |
| `Offer`, `OfferConfig` | wysłane oferty i ustawienia ofert (adres, podpis, mnożniki prowizji, nazwa promocji) |
| `LeadAssignment` | historia przypisań klienta między agentami |
| `AgentProfile` | ustawienia konta: podgląd rankingu, ukrycie managera w rankingu, wymuszona zmiana hasła, zdjęcie |
| `TeamRules` | zasady pracy zespołu (limity prób, kroki ponownych prób, dni i godziny pracy, losowanie firm) |
| `DialerDevice`, `DialerPairing`, `CallRequest` | sparowane telefony, kody parowania i połączenia z dialera (czy odebrał, ile trwało) |
| `RecordingConfig`, `CallRecording` | opcjonalne nagrywanie rozmów: ustawienia i połączony Dysk Google; nagrania (plik leży na Dysku Google) |
| `WorkDay` | obecność: przyjście, ostatni ruch, wyjście, przerwy |
| `Announcement` | „Solid info”: informacje managera dla zespołu (promocje, argumenty, cele) |
| `AccessDevice`, `ActivationCode` | aktywowane urządzenia z dostępem do CRM i jednorazowe kody aktywacji |

## Instalacja i uruchomienie (Windows, PowerShell)

Każdy krok to jedna komenda wpisana w terminalu:

1. **Pobranie projektu**

   ```powershell
   git clone https://github.com/LightWeightBrah/aplikacje-www-projekt.git
   cd aplikacje-www-projekt
   ```

   Kopiuje repozytorium z GitHuba i wchodzi do jego folderu.

2. **Środowisko wirtualne (venv)**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   Pierwsza komenda tworzy osobne środowisko Pythona tylko dla tego projektu. Druga je włącza (w terminalu pojawi się `(.venv)`).

3. **Instalacja bibliotek**

   ```powershell
   pip install -r requirements.txt
   ```

   Instaluje wszystkie biblioteki z listy (Django, DRF, JWT, Swagger, filtry, openpyxl).

4. **Baza danych**

   ```powershell
   python manage.py migrate
   ```

   Tworzy w pliku SQLite wszystkie tabele projektu.

5. **Dane demo**

   ```powershell
   python manage.py seed_demo
   ```

   Wypełnia bazę taryfami, firmami, klientami, przykładowymi rozmowami, ofertami i kontami (szczegóły niżej).

6. **Konto administratora** (opcjonalnie)

   ```powershell
   python manage.py createsuperuser
   ```

   Tworzy konto do panelu `/admin/`. W aplikacji ma ono automatycznie rolę Manager.

7. **Uruchomienie serwera**

   ```powershell
   python manage.py runserver
   ```

   Włącza aplikację pod adresem `http://127.0.0.1:8000/` (przekierowuje na `/crm/`). Zatrzymanie: `Ctrl+C`.

Linux / macOS: włączenie venv to `source .venv/bin/activate`, reszta bez zmian.

Na serwerze (hosting) CRM działa inaczej: bez kont demo, z HTTPS i z aktywacją urządzeń. Instrukcja krok po kroku jest w [`docs/HOSTING.md`](docs/HOSTING.md).

## Dane demo i komendy

Dane w repozytorium są wyłącznie **fikcyjne** (zmyślone firmy, numery i adresy `example.com`).

| Komenda | Co robi |
|---|---|
| `python manage.py seed_demo` | tworzy taryfy, firmy, bazę klientów, rozmowy, sprzedaże, oferty, „Solid info” i konta demo; drugie uruchomienie niczego nie dubluje; tylko lokalnie |
| `python manage.py seed_demo --reset` | usuwa bazę demo i tworzy ją od nowa (przydatne przed pokazem) |
| `python manage.py reset_leads` | kasuje wszystkich klientów, historię, bazy i firmy, zostawia konta, taryfy i ustawienia, wgrywa dane demo; tylko przy `DEBUG=True`, pyta o wpisanie `RESET` (`--empty` bez danych demo) |
| `python manage.py setup_roles [--demo]` | tworzy grupy Agent i Manager (z `--demo` także konta demo, bez klientów) |
| `python manage.py start_firm` | start na serwerze: grupy i pierwsze konto managera, bez danych demo (hasło wpisuje się w konsoli) |
| `python manage.py load_tariffs [--overwrite]` | wgrywa taryfy ze wzornika (komórkowe S–VIP, światłowód, Magenta TV); działa też na serwerze |
| `python manage.py device_code` | wypisuje jednorazowy kod aktywacji urządzenia (ważny 24 h) |
| `python manage.py make_sample_excel` | tworzy przykładowe pliki Excela do importu: `docs/przykladowa_baza.xlsx`, `przykladowa_baza_ceidg.xlsx` i bazy do pokazu `przykladowa_baza_pokaz_1..3.xlsx` |
| `python manage.py release_stale_leads` | zwraca do puli klientów nietkniętych przez agenta ponad ustawiony czas (`--dry-run` tylko liczy) |
| `python manage.py test` | uruchamia testy automatyczne |

Konta demo (hasło każdego: `demo12345`):

| Konto | Rola | Uwagi |
|---|---|---|
| `agent1` | Agent | ma klientów w różnych grupach (oferty, oddzwonienia, nie odebrali), dzisiejsze sprzedaże i własną bazę z CEIDG |
| `agent2` | Agent | ma **zaległe oddzwonienie**, więc `POST /api/leads/next/` zwróci mu 409 |
| `agent3` | Agent | nowy agent z **wyłączonym podglądem rankingu** (`GET /api/leaderboard/` zwraca mu 403) |
| `manager1` | Manager | widzi wszystko, zarządza zespołem, taryfami i ustawieniami |

## Adresy

| Adres | Co to |
|---|---|
| `/crm/` | aplikacja webowa (opis w sekcji „Interfejs webowy”) |
| `/api/docs/` | dokumentacja Swagger (interaktywna) |
| `/api/schema/` | schemat OpenAPI |
| `/api/` | lista endpointów REST (DRF) |
| `/admin/` | panel administratora Django |
| `/crm/leads/` | prosta lista leadów w szablonie Django (Lab 3, wymaga logowania przez `/admin/`) |
| `/crm/dialer.apk` | pobranie aplikacji Solid Dialer na telefon (plik nie jest w repozytorium) |
| `/crm/activate/` | aktywacja nowego urządzenia kodem od managera (gdy blokada urządzeń jest włączona) |

## Uwierzytelnianie (JWT)

1. `POST /api/token/` z body `{"username": "agent1", "password": "demo12345"}` zwraca `access` i `refresh`.
2. Token `access` wysyłamy w nagłówku `Authorization: Bearer <access>`. Żyje 5 minut.
3. `POST /api/token/refresh/` z `{"refresh": "..."}` zwraca nowy `access`.

W Swaggerze: przycisk **Authorize** i wklejenie samego tokenu `access` (bez słowa „Bearer”). Zmiana lub reset hasła unieważnia stare tokeny. Logowanie ma limit 10 prób na minutę.

Telefon z Solid Dialerem nie używa JWT: po sparowaniu przedstawia się własnym tokenem w nagłówku `X-Dialer-Token`.

## Role i uprawnienia

| Rola | Co może |
|---|---|
| bez roli | nic (403) |
| **Agent** | widzi i edytuje tylko swoich klientów i ich historię, zapisuje rozmowy i oferty, bierze „Następnego klienta”, dodaje nowe kontakty, czyta taryfy, ustawienia i „Solid info”, widzi swoje wyniki i minuty oraz (jeśli manager nie wyłączył) ranking |
| **Manager** (grupa Manager lub superuser) | widzi wszystko, zarządza taryfami, firmami, bazami, kontami, zasadami pracy, ofertami, „Solid info” i urządzeniami, importuje bazy, przypisuje i przepisuje klientów, widzi obecność i minuty całego zespołu, włącza i wyłącza podgląd rankingu |

Własne klasy uprawnień (`crm/permissions.py`): `IsAgentOrManager`, `IsManager`, `IsManagerOrReadOnly`, `IsLeadOwnerOrManager` (dostęp do klienta i jego historii ma opiekun albo manager).

## Endpointy

CRUD (`ModelViewSet`) i odczyt:

| Endpoint | Metody | Uwagi |
|---|---|---|
| `/api/products/` | CRUD | zapis tylko manager; usunięcie tylko nieużywanego produktu (inaczej 409 z podpowiedzią „Wyłącz”) |
| `/api/companies/` | CRUD | zapis tylko manager; szukanie po nazwie, NIP, KRS, REGON |
| `/api/batches/` | CRUD | zapis tylko manager |
| `/api/leads/` | CRUD | agent: swoje; usuwanie tylko manager |
| `/api/activities/` | tworzenie, lista, szczegóły, `PATCH` | `PATCH` zmienia tylko komentarz (autor lub manager); wynik i terminy są niezmienne, bez usuwania |
| `/api/assignments/` | tylko odczyt | historia przypisań |
| `/api/users/` | lista, tworzenie, `PATCH`, usuwanie | tylko manager (konta, role, hasła) |

Praca agenta:

| Endpoint | Co robi |
|---|---|
| `GET /api/work/next/` | najważniejszy kontakt teraz: follow-up oferty → oddzwonienie → ponowna próba → nowa firma |
| `GET /api/work/summary/` | liczniki grup (oferty, oddzwonienia, nie odebrali, nowi), liczby dnia i postęp bazy |
| `POST /api/work/contact/` | agent dodaje klienta sam (np. z polecenia); numer już w CRM → 409 z tym klientem |
| `POST /api/leads/next/` | losuje klienta z puli dla zalogowanego agenta; opcjonalnie `?batch=<id>`; 404 gdy pula pusta, 409 gdy są zaległe oddzwonienia |
| `POST /api/activities/` | zapisuje rozmowę i aktualizuje klienta; przy `sold` wymaga `sale_lines` |
| `POST /api/activities/{id}/undo/` | cofa własny ostatni zapis (w CRM przycisk „Cofnij” przez 10 s, serwer pozwala do 30 s) |
| `GET /api/leads/lookup/?q=` | szybkie wyszukiwanie po telefonie (różne zapisy), NIP, KRS, nazwisku, firmie, e-mailu |
| `GET /api/my-leads/`, `due/`, `schedule/`, `summary/` | moi klienci, kontakty do zrobienia, plan dnia, liczby do paska dnia |
| `GET /api/my-leads/calendar/` | plik `.ics` z terminami kontaktów (np. do Kalendarza Google) |
| `GET /api/offers/?lead=ID`, `POST /api/offers/` | oferty klienta; zapis rozmowy „Wysłana oferta” razem z ofertą i wysyłką |
| `POST /api/offers/preview/` | wylicza zobowiązanie miesięczne i prowizję bez zapisywania |
| `GET /api/offers/{id}/html/` | gotowa oferta jako HTML (do pobrania albo skopiowania z formatowaniem) |
| `GET /api/work/minutes/` | minuty rozmów agenta w okresie, dzień po dniu, i cel |
| `POST /api/work/login/`, `/api/work/logout/` | przyjście i wyjście z pracy (obecność) |
| `GET /api/announcements/` | „Solid info” dla zespołu (POST, PATCH, DELETE tylko manager) |
| `GET /api/stats/` | statystyki: rozmowy, dodzwonienia, oferty, sprzedane, prowizja, minuty, wyniki agentów; okres `?days=` albo `?date_from=&date_to=`; manager: zespół (`?scope=me` = tylko jego praca), agent: tylko siebie |
| `GET /api/leaderboard/` | ranking w okresie (minuty, rozmowy, zainteresowani, sprzedane, prowizja); bez danych klientów; agent z wyłączonym podglądem dostaje **403** |
| `GET /api/settings/`, `GET /api/offer-config/` | zasady pracy i ustawienia ofert (czyta każdy zalogowany) |
| `GET /api/me/`, `POST /api/me/password/`, `POST/DELETE /api/me/avatar/` | dane zalogowanego, zmiana hasła, zdjęcie profilowe |
| `POST /api/batches/import/` | import bazy z `.xlsx` (pole `file`, opcjonalnie `name`, `product`, `assigned_to`); odpowiedź to raport |
| `GET /api/leads/export/` | eksport klientów do `.xlsx`; te same filtry co `/api/leads/` |

Solid Dialer (telefon agenta):

| Endpoint | Co robi |
|---|---|
| `POST /api/dialer/pairing/` | agent w CRM prosi o 6-cyfrowy kod parowania telefonu |
| `POST /api/dialer/pair/` | telefon wymienia kod na własny token |
| `POST /api/dialer/call/` | „Zadzwoń” w CRM: telefon wybierze numer klienta (albo podany numer) |
| `GET /api/dialer/poll/` | telefon pyta, czy ma coś wybrać (204 = nic) |
| `POST /api/dialer/calls/{id}/finish/` | telefon odsyła wynik z rejestru połączeń: `answered` i `duration_seconds` |
| `GET /api/dialer/status/`, `DELETE` | stan telefonu i ostatniego połączenia; odłączenie telefonów |
| `POST /api/dialer/calls/{id}/recording/` | telefon wysyła nagranie rozmowy (gdy nagrywanie jest włączone; opis: `docs/NAGRYWANIE.md`) |
| `GET/PATCH /api/recordings/settings/` | manager: nagrywanie wł./wył., po ilu dniach usuwać, stan Dysku Google i telefonów |
| `POST /api/recordings/google/connect/`, `.../disconnect/` | manager: łączenie i odłączanie Dysku Google |
| `GET /api/recordings/?lead=`, `GET /api/recordings/{id}/audio/` | manager: nagrania leada i odsłuch |

Tylko manager (agent dostaje 403):

| Endpoint | Co robi |
|---|---|
| `POST /api/leads/{id}/assign/` | przypisuje klienta agentowi albo zwraca do puli; zapisuje historię przypisań |
| `POST /api/leads/reassign/` | przepisuje wszystkich aktywnych klientów jednego agenta na innego |
| `GET /api/leads/unreachable/`, `POST /api/leads/recycle-unreachable/` | pula nieosiągalnych i zwrot gotowych do obiegu |
| `GET /api/agents/`, `PATCH /api/agents/{id}/`, `POST /api/agents/team-access/` | agenci z liczbą klientów; podgląd rankingu jednej osobie albo wszystkim |
| `GET /api/work/attendance/?date=` | obecność: kto kiedy przyszedł i wyszedł, czas pracy, przerwy, minuty i cel |
| `POST /api/users/{id}/reset-password/`, `deactivate/`, `activate/` | hasło tymczasowe, dezaktywacja (z decyzją, co z klientami) i ponowne włączenie konta |
| `PATCH /api/settings/`, `POST /api/settings/reset/` | zmiana zasad pracy i powrót do wartości domyślnych |
| `PATCH /api/offer-config/` | ustawienia ofert |
| `GET /api/devices/`, `POST /api/devices/code/`, `POST /api/devices/{id}/revoke/` | urządzenia z dostępem, nowy kod aktywacji, blokada urządzenia |

Filtrowanie, wyszukiwanie, sortowanie i paginacja (20 na stronę):

- `GET /api/leads/?status=callback&batch=1&product=2&owner=3`, `?pool=true`, `?overdue=true`
- `GET /api/leads/?search=Alfa` (kontakt, telefon, e-mail, firma, NIP, KRS)
- `GET /api/leads/?ordering=next_contact_at`

## Import z Excela

Format: pierwszy arkusz, max 5 MB i 5000 wierszy. Przykłady (fikcyjne dane): [`docs/przykladowa_baza.xlsx`](docs/przykladowa_baza.xlsx), [`docs/przykladowa_baza_ceidg.xlsx`](docs/przykladowa_baza_ceidg.xlsx) i bazy do pokazu `docs/przykladowa_baza_pokaz_1..3.xlsx` (po 80 firm).

- **baza z CEIDG** (kolumny `tel`, `data kontaktu`, `termin oddzwonki`, `nip`, a dalej bez nagłówków REGON, nazwa firmy, adres) jest rozpoznawana sama po układzie kolumn; przesunięte wiersze i numery budynków zamienione przez Excela na daty są poprawiane i pokazane w raporcie jako ostrzeżenia,
- **inny plik**: pierwszy wiersz to nagłówki, wymagany jest tylko telefon; opcjonalnie firma, imię i nazwisko, dodatkowy telefon, e-mail, NIP, KRS, REGON, ulica, kod pocztowy, miasto, uwagi (polskie i angielskie nagłówki),
- „Dla kogo jest ta baza”: konkretny agent (dostaje najpierw firmy z tej bazy, inni jej nie dostają) albo wspólna pula,
- **duplikaty** (ten sam numer w systemie lub w pliku, niezależnie od zapisu `+48 600-100-200` / `600100200`) są pomijane, **błędne wiersze** też, reszta importuje się normalnie; firmy łączone po NIP,
- raport zawiera `created`, `duplicates`, `errors` i ostrzeżenia z numerami wierszy.

## Zasady pracy z klientami

Manager ustawia je na stronie **Ustawienia i oferty** (`/crm/settings/`) albo przez `PATCH /api/settings/`. Wartości domyślne bierze się z `config/settings.py` (`LEAD_*`, `CALLBACK_RETRY_STEPS`, `WORK_DAYS`), po pierwszej zmianie obowiązują te zapisane w bazie (`TeamRules`).

| Zasada | Domyślnie |
|---|---|
| limit nieodebranych prób jednego agenta; potem klient wraca do puli, a ten agent go już nie dostanie | 5 |
| tylu różnych agentów bez skutku i klient dostaje status „nieosiągalny” | 5 |
| ponowna próba po 1. / 2. / kolejnym nieodebraniu | 2 godz. / 1 dzień / 2 dni |
| dni i godziny pracy, w które system ustawia terminy (dni wolne są pomijane) | pon–pt, 9:00–17:00 |
| kolejne próby po nieodebranym umówionym kontakcie (lista, po ostatniej co tyle samo) | 15 min / 1 godz. / 2 godz. / 1 dzień |
| po tylu dniach bez telefonu pobrany klient wraca do puli (`release_stale_leads`) | 1 dzień |
| po tylu dniach manager może zwrócić nieosiągalnych do obiegu | 30 dni |
| losowanie nowych firm | wszystkie razem (albo najpierw jednoosobowe, potem spółki) |

Ponowna próba po „Nie odebrał” jest **ustawiana automatycznie**, a godzina rotuje (rano, w środku dnia, po południu), żeby nie dzwonić do klienta ciągle o tej samej porze. Agent może terminu nie zmieniać albo wybrać inny. „Zaległe” i blokada nowego klienta (409) dotyczą tylko kontaktów **obiecanych klientowi** (oddzwonienie, zainteresowany, oferta wysłana); zwykła ponowna próba ma osobną grupę „Nie odebrali”.

## Interfejs webowy

Aplikacja pod `/crm/` korzysta z tego samego API i tokenów JWT. Na górze jest menu (Dzwonienie, Kalendarz, Wyniki, Ranking, a u managera także Zespół i Zarządzanie), wyszukiwarka i menu konta w prawym górnym rogu (motyw, zdjęcie, telefon z dialerem, „Moje ustawienia”, zmiana hasła, wylogowanie). Na dole ekranu jest pasek dialera. Zrzuty poniżej są w motywie ciemnym, na danych z `seed_demo`.

Agenci nie muszą znać żadnych kombinacji klawiszy. Pojedyncze klawisze (`N` następny klient, `Z` zadzwoń, `1`–`8` wynik rozmowy, `Enter` zapisz, `Esc` wyczyść, `/` szukaj) są tylko dodatkiem i można je wyłączyć w „Moich ustawieniach”. Pierwsze logowanie uruchamia krótki samouczek (13 kroków dla agenta, 22 dla managera).

| Adres | Kto | Opis |
|---|---|---|
| `/crm/login/` | wszyscy | logowanie; pierwsze logowanie po założeniu konta lub resecie wymusza ustawienie własnego hasła |
| `/crm/queue/` | wszyscy | **Dzwonienie**: następny klient, karta klienta, wynik rozmowy, oferta |
| `/crm/plan/`, `/crm/calendar/` | wszyscy | **Kalendarz**: plan dnia i widok miesiąca, eksport `.ics` |
| `/crm/` | wszyscy | **Wyniki**: „Moja praca”, a u managera także „Cały zespół” |
| `/crm/ranking/` | agent (jeśli może) i manager | ranking zespołu |
| `/crm/manager/` | manager | **Zespół**: obecność, minuty i wszyscy klienci |
| `/crm/import/` | manager | import i eksport baz |
| `/crm/products/` | manager | taryfy i produkty |
| `/crm/users/` | manager | konta użytkowników |
| `/crm/settings/` | manager | zasady pracy, oferty, „Solid info”, urządzenia |

### Dzwonienie

Po lewej: „Następny klient”, „+ Nowy kontakt”, „Zadzwoń na numer”, grupy klientów (oferty, oddzwonienia, nie odebrali, nowi), ile zostało w bazie i lista „Moi klienci / Historia dziś”. Na środku karta klienta: najpierw **nazwa firmy**, potem numer z przyciskiem „Zadzwoń”, osoba, e-mail, a NIP i adres małym drukiem. Pole **„Stałe informacje o kliencie”** widzi cały zespół, z dopiskiem, kto i kiedy je zmienił. Po prawej panel „Dziś” (telefony, rozmowy, oferty, sprzedane, prowizja, minuty w tej godzinie wobec celu), terminy na dziś i „Solid info”.

Wynik rozmowy wybiera się kafelkiem albo klawiszem `1`–`8`. Zapis można cofnąć przyciskiem „Cofnij” przez 10 sekund. Po „Zapisz i dalej” od razu otwiera się następny klient, zawsze od góry karty.

**Wyślij ofertę** (4): taryfa, liczba numerów, okres, promocja, a zobowiązanie i prowizja liczą się na żywo. Oferta idzie mailem na adres ustawiony przez managera. Bez serwera poczty przycisk „Otwórz w poczcie” kopiuje gotową ofertę z formatowaniem.

![Oferta dla klienta](docs/queue_oferta.png)

**Sprzedane** (5) zapisuje umowę: taryfę, liczbę numerów i okres, z prowizją.

![Sprzedaż](docs/queue_sprzedaz.png)

Wyszukiwarka u góry każdej strony znajduje klienta po numerze telefonu, NIP albo nazwie firmy, np. gdy klient sam oddzwania.

![Wyszukiwarka](docs/wyszukiwarka.png)

„Moje ustawienia” (menu konta) to ustawienia jednej osoby: przypomnienia o terminach (karta na ekranie, dźwięk, powiadomienie przeglądarki), panel „Dziś”, chowanie drobnych dodatków na ekranie Dzwonienie (cel minut, „Twoja baza / Wspólna pula”, liczniki na dolnym pasku) i pojedyncze klawisze.

![Moje ustawienia](docs/moje_ustawienia.png)

### Kalendarz

Plan dnia pokazuje umówione kontakty z godziną, sprawą i ostatnim wynikiem. Widok miesiąca pokazuje wszystkie terminy; kliknięcie kontaktu od razu otwiera go do dzwonienia. „Pobierz kalendarz (.ics)” zapisuje terminy do Kalendarza Google.

![Plan dnia](docs/plan.png)

![Kalendarz miesiąca](docs/calendar.png)

### Wyniki i ranking

Kafelki: rozmowy, dodzwonienia, wysłane oferty, sprzedane („1 sprzedaż na X rozmów”), prowizja i klienci czekający na agenta. Pod nimi minuty rozmów godzina po godzinie wobec celu, a u managera tabela **„Wyniki agentów”** (minuty / powinno być, czas pracy, rozmowy, dodzwonienia, oferty, sprzedane, rozmów na 1 sprzedaż, prowizja, klienci w toku), sortowana kliknięciem nagłówka.

![Wyniki zespołu](docs/dashboard.png)

Ranking jest domyślnie po minutach rozmów. Manager włącza i wyłącza agentom podgląd wyników innych (backend zwraca wtedy 403, nie tylko ukrywa przycisk) i może ukryć managera, który sam nie dzwoni.

![Ranking zespołu](docs/ranking.png)

### Zespół (manager)

Obecność dnia (przyszedł, wyszedł, czas pracy, przerwy, telefony, minuty wobec celu) i wszyscy klienci zespołu: zakładki „Wszystkie / Zaległe / Pula / Nieosiągalni”, wyszukiwanie, filtry, masowe przypisywanie, „Przepisz leady agenta…” i eksport do Excela z bieżącymi filtrami. Kliknięcie wiersza otwiera szczegóły z historią rozmów i opiekunów.

![Zespół](docs/manager.png)

![Szczegóły klienta](docs/manager_szczegoly.png)

### Zarządzanie (manager)

**Import / eksport baz** z wyborem „Dla kogo jest ta baza”:

![Import i eksport](docs/import.png)

**Taryfy i produkty**: ceny bez i z promocją, dopłata za kolejny numer. „Pobierz plik taryf” i „Wgraj plik taryf…” przenoszą cennik przez Excela (ta sama nazwa = nowe ceny, nowa nazwa = nowa taryfa). Sprzedawany produkt się wyłącza, a „Usuń” jest dla produktu dodanego przez pomyłkę.

![Taryfy i produkty](docs/products.png)

**Użytkownicy**: nowe konta (hasło tymczasowe pokazane raz), role, reset hasła, dezaktywacja (z decyzją, co z klientami) i usuwanie kont bez historii. Nie można zdezaktywować siebie ani ostatniego managera.

![Użytkownicy](docs/users.png)

**Ustawienia i oferty**: na górze „Tak działa teraz” w zwykłych zdaniach, potem limity prób, ponowne próby, dni i godziny pracy, umówione oddzwonienia, zwroty do puli i losowanie firm. Zasady zapisuje jeden przycisk w przyklejonym pasku, z informacją, kto i kiedy ostatnio je zmienił.

![Ustawienia zespołu](docs/settings.png)

Niżej: ustawienia ofert (adres, podpis, okres, mnożniki prowizji, treść), „Solid info” dla zespołu i urządzenia z dostępem do CRM.

![Ustawienia ofert](docs/settings_oferty.png)

### Logowanie

![Logowanie](docs/login.png)

## Solid Dialer i hosting

- **Solid Dialer** (`dialer-android/`): agent paruje telefon kodem 6 cyfr (menu konta → „Telefon (dialer)…”). „Zadzwoń” w CRM wybiera numer na telefonie, a po rozmowie aplikacja odsyła z rejestru połączeń, czy klient odebrał i ile trwała rozmowa. Budowanie i instalacja: [`dialer-android/README.md`](dialer-android/README.md).
- **Hosting** (PythonAnywhere): ustawienia w pliku `.env` (poza repozytorium), HTTPS i bezpieczne ciasteczka, `seed_demo` zablokowane, pierwsze konto przez `start_firm`. **Aktywacja urządzeń**: CRM otwiera się tylko na komputerach i telefonach aktywowanych jednorazowym kodem od managera, a obcy widzi tylko „To urządzenie nie ma dostępu”. Lokalnie blokada jest wyłączona. Instrukcja: [`docs/HOSTING.md`](docs/HOSTING.md).

## Dokumentacja API i strona z Labu 3

Swagger (`/api/docs/`):

![Swagger, widok 1](docs/swagger1.png)
![Swagger, widok 2](docs/swagger2.png)

Lista leadów w szablonie Django (`/crm/leads/`, strona z Labu 3, ma tylko jasny wygląd):

![Lista leadów, widok 1](docs/lead_list1.png)
![Lista leadów, widok 2](docs/lead_list2.png)

## Testy

```powershell
python manage.py test
```

**481 testów** `APITestCase` w trzynastu plikach w `crm/`:

| Plik | Co sprawdza |
|---|---|
| `tests.py` | JWT i role (401/403), widoczność klientów, walidację, wyszukiwanie, `leads/next` (404/409), skutki rozmów, cykl życia, endpointy managera, statystyki, ranking, plan dnia, `.ics`, import i eksport Excela |
| `test_users.py` | zarządzanie kontami, hasła tymczasowe, dezaktywacja, zasady bezpieczeństwa |
| `test_frontend.py` | strony `/crm/`, menu wg roli, zdjęcia profilowe, wyniki, ustawienia osobiste |
| `test_retry.py` | zasady zespołu i automatyczne ponowne próby (pory dnia, godziny pracy, limity) |
| `test_work.py` | „Następny klient”, grupy, oferty, prowizja, dialer, minuty, obecność, „Solid info” |
| `test_sales.py` | taryfy z cenami, zapis sprzedaży, walidacje, prowizja w statystykach i rankingu, usuwanie produktu |
| `test_ceidg.py` | import baz z CEIDG (układ kolumn, poprawki dat), bazy agenta, „Pomiń firmę”, stałe informacje o kliencie |
| `test_missed_calls.py` | nieodebrane umówione kontakty |
| `test_workdays.py` | dni pracy i kolejne próby po nieodebranym terminie |
| `test_undo.py` | cofanie ostatniego zapisu |
| `test_edit.py` | poprawianie komentarza i sprawy, układ ekranu Dzwonienie |
| `test_devices.py` | aktywacja i blokada urządzeń |
| `test_hosting.py` | tryb serwera (`start_firm`, brak kont demo, limit logowania, pobieranie dialera) |

## Struktura projektu

```
config/                       ustawienia i główne trasy
crm/
  models.py                   modele
  serializers.py              serializery i walidacja
  views.py, filters.py        ViewSety, akcje, filtry
  work.py, work_api.py        praca agenta: następny klient, oferty, minuty, obecność, dialer, Solid info
  users_api.py, rules_api.py, me_view.py   konta, zasady zespołu, profil
  device_lock.py, device_api.py            aktywacja urządzeń
  services.py, rules.py       logika biznesowa: losowanie, skutki rozmów, ponowne próby, statystyki
  tariffs.py                  wzornik taryf
  excel.py, sample_data.py    import i eksport xlsx, przykładowe pliki
  permissions.py, roles.py    role i uprawnienia
  web_views.py, web_urls.py   strony HTML
  templates/, static/         szablony, CSS i JavaScript (app.js, queue.js, tour.js, ...)
  management/commands/        seed_demo, reset_leads, setup_roles, start_firm, load_tariffs, device_code, ...
  tests.py, test_*.py         testy
dialer-android/               aplikacja Solid Dialer na Androida (Java)
docs/                         zrzuty ekranu, przykładowe pliki Excela, HOSTING.md
```

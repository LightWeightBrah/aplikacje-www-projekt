# Solid CRM w internecie – PythonAnywhere (serwery w UE)

Instrukcja krok po kroku. Wszystko robisz w przeglądarce na stronie PythonAnywhere,
komendy wklejasz do konsoli „Bash” na tej stronie (nie na swoim komputerze).

W tekście `TWOJLOGIN` zamień na login, który wybierzesz przy zakładaniu konta.
Adres CRM będzie wtedy: `https://TWOJLOGIN.eu.pythonanywhere.com`.

> Zasada: do repo (GitHub) nie trafiają prawdziwe dane klientów ani hasła. Sekrety są tylko
> w pliku `.env` na serwerze, a bazę firm wgrywasz przez CRM (Zarządzanie → Import).

---

## 1. Konto

1. Wejdź na **https://eu.pythonanywhere.com** (koniecznie `eu.` – serwery w Niemczech, ważne dla RODO).
2. „Pricing & signup” → **Create a Beginner account** (darmowe). Login = początek adresu CRM,
   np. `solidcrm` → `https://solidcrm.eu.pythonanywhere.com`.

Darmowe konto wystarczy na start. Płatne (Hacker, ok. 5 $/mies.) będzie potrzebne dopiero
do wysyłania ofert z CRM przez pocztę firmy albo własnej domeny.

## 2. Kod i biblioteki

Na stronie: **Consoles → Bash**. Wklej po kolei:

```bash
git clone https://github.com/LightWeightBrah/aplikacje-www-projekt.git
cd aplikacje-www-projekt
git checkout solid-crm
mkvirtualenv --python=python3.13 solidcrm
pip install -r requirements.txt
```

(`git checkout solid-crm` jest potrzebne, dopóki zmiany nie są scalone do `main`.
Jeśli `python3.13` nie istnieje, użyj `python3.12`.)

## 3. Sekrety – plik `.env`

Wygeneruj tajny klucz:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Skopiuj wynik i utwórz plik `.env` (w tym samym folderze `aplikacje-www-projekt`):

```bash
nano .env
```

Wpisz (zamień klucz i login):

```
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=tu-wklej-wygenerowany-klucz
DJANGO_ALLOWED_HOSTS=TWOJLOGIN.eu.pythonanywhere.com
```

Zapis w nano: klawisz Ctrl+O, Enter, potem Ctrl+X. (Albo zamiast nano: zakładka **Files**,
folder `aplikacje-www-projekt`, „New file” `.env`.)

## 4. Baza i pierwsze konto

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py start_firm
```

`start_firm` zapyta o login, imię i nazwisko oraz hasło **managera** (hasła nie widać przy pisaniu).
Nie ma tu kont demo ani fikcyjnych firm – `seed_demo` na serwerze jest zablokowane.

## 5. Strona WWW

Zakładka **Web → Add a new web app** → Next → **Manual configuration** → **Python 3.13** → Next.

Na stronie aplikacji ustaw:

- **Source code:** `/home/TWOJLOGIN/aplikacje-www-projekt`
- **Virtualenv:** `/home/TWOJLOGIN/.virtualenvs/solidcrm`
- **WSGI configuration file** (kliknij link do pliku) – usuń całą zawartość i wklej:

```python
import os
import sys

path = "/home/TWOJLOGIN/aplikacje-www-projekt"
if path not in sys.path:
    sys.path.insert(0, path)
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

- **Static files:** URL `/static/` → Directory `/home/TWOJLOGIN/aplikacje-www-projekt/staticfiles`
- **Force HTTPS:** włączone.

Kliknij zielony **Reload** na górze.

## 5a. Aktywacja pierwszego urządzenia

Na serwerze CRM otwiera się **tylko na aktywowanych urządzeniach** – obcy, który zna adres,
widzi wyłącznie „To urządzenie nie ma dostępu” (bez ekranu logowania). Pierwszy kod weź z konsoli:

```bash
cd ~/aplikacje-www-projekt
workon solidcrm
python manage.py device_code
```

Wejdź na `https://TWOJLOGIN.eu.pythonanywhere.com`, wpisz kod i nazwę urządzenia (np. „Laptop Juliana”),
potem zaloguj się kontem managera. Kolejne urządzenia: w CRM **Zarządzanie → Ustawienia i oferty →
Urządzenia z dostępem → „Nowy kod aktywacji”**. Tam też blokujesz urządzenie (np. zgubiony laptop,
odejście agenta). Kod działa raz i 24 godziny.

Aplikacja Solid Dialer na telefonie nie potrzebuje aktywacji przeglądarki – łączy się własnym kodem
parowania. Ale żeby agent mógł na telefonie pobrać aplikację i otworzyć CRM w przeglądarce telefonu,
telefon też trzeba aktywować kodem.

## 6. Pierwsze kroki w CRM

1. **Zarządzanie → Użytkownicy** – dodaj agentów (przy pierwszym logowaniu sami zmienią hasło).
2. **Zarządzanie → Taryfy i produkty** – sprawdź taryfy i ceny.
3. **Zarządzanie → Ustawienia i oferty** – adres, na który idą oferty, podpis, telefon.
4. **Zarządzanie → Import / eksport baz** – wgraj plik Excel z bazą firm (z laptopa, przez przeglądarkę)
   i przypisz go agentowi.

## 7. Solid Dialer na telefony

1. Na komputerze w **Android Studio**: otwórz folder `dialer-android` →
   menu **Build → Build App Bundle(s) / APK(s) → Build APK(s)**. Gotowy plik:
   `dialer-android/app/build/outputs/apk/debug/app-debug.apk`.
2. Na PythonAnywhere: zakładka **Files** → `aplikacje-www-projekt` → utwórz folder `downloads` →
   wgraj plik i nazwij go **`solid-dialer.apk`**.
3. Każdy agent na swoim telefonie otwiera **`https://TWOJLOGIN.eu.pythonanywhere.com/crm/dialer.apk`**,
   instaluje (telefon zapyta o zgodę na instalację spoza Sklepu Play – zezwolić), w aplikacji wpisuje
   adres `https://TWOJLOGIN.eu.pythonanywhere.com` i 6-cyfrowy kod z CRM (menu konta → „Telefon (dialer)”).

Plik APK nie trafia do repo (to zbudowany program). Nowa wersja aplikacji = podmień plik w `downloads`.

## 8. Aktualizacja CRM (gdy wypchniesz zmiany na GitHub)

**Consoles → Bash:**

```bash
cd ~/aplikacje-www-projekt
workon solidcrm
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

Potem **Web → Reload**.

## 9. Ważne na co dzień

- **Darmowe konto:** raz na 3 miesiące kliknij w zakładce Web „Run until 3 months from today”
  (PythonAnywhere przypomni mailem), inaczej strona się wyłączy. Dane zostają.
- **Kopia bazy:** zakładka **Files** → `aplikacje-www-projekt/db.sqlite3` → pobierz, np. raz w tygodniu.
  To plik z danymi klientów – trzymaj go tylko na firmowym/swoim komputerze, nie wysyłaj dalej.
- **Wyłączenie:** Web → „Disable” (albo usunięcie konta) – strona przestaje działać.
- **Poczta z CRM (później):** wymaga płatnego konta i danych skrzynki firmy; wtedy w `.env` dochodzą
  `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`.

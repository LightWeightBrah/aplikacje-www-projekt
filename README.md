# LeadCRM – REST API dla zespołu sprzedaży

Projekt zaliczeniowy z przedmiotu **Aplikacje WWW** (UWM Olsztyn, 2026Z).
Autor: Julian Łabanowski. Stos: Python 3.13, Django 6.1, Django REST Framework, SimpleJWT, drf-spectacular, django-filter, SQLite.

## Opis biznesowy

System CRM dla firmy sprzedażowej (telekomunikacja, leasing), w której zespół **agentów** dzwoni do potencjalnych klientów (leadów), a **manager** pilnuje bazy i wyników.

Jak to działa:

- Manager wgrywa bazę kontaktów (paczka `LeadBatch`). Nowe leady trafiają do **puli**: nikt ich jeszcze nie prowadzi.
- Agent nie wybiera leadów sam. Wywołuje `POST /api/leads/next/` i system **losuje mu leada z puli**. Przypisanie jest atomowe, więc dwóch agentów nie dostanie tego samego kontaktu.
- Agent, który ma **zaległe oddzwonienia**, nie dostanie nowego leada (odpowiedź 409). Najpierw musi dokończyć obietnice dane klientom.
- Agent nie dostanie leada, który już wcześniej prowadził.
- Po każdej rozmowie agent dodaje **aktywność** (`Activity`) z wynikiem: nie odebrał, prosił o oddzwonienie, zainteresowany, odmowa, zły numer, notatka. System sam aktualizuje leada: licznik prób, datę ostatniego kontaktu, status i termin kolejnego kontaktu.
- Zasada nadrzędna: **lead nigdy nie zostaje bez następnego kroku**. Status „Do oddzwonienia" wymaga terminu i notatki.
- Agent widzi tylko swoje leady, manager widzi wszystko.

## Modele

| Model | Rola |
|---|---|
| `Product` | oferowany produkt (np. światłowód, leasing) |
| `Company` | firma klienta: nazwa, NIP, KRS, REGON, miasto |
| `LeadBatch` | paczka leadów (np. import z pliku), kto i kiedy dodał |
| `Lead` | kontakt sprzedażowy: opiekun (`owner`, brak = pula), status, próby, termin kolejnego kontaktu |
| `Activity` | historia rozmów leada: agent, wynik, notatka |
| `LeadAssignment` | historia przypisań leada między agentami |

## Instalacja i uruchomienie (Windows, PowerShell)

```powershell
git clone https://github.com/LightWeightBrah/aplikacje-www-projekt.git
cd aplikacje-www-projekt

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

python manage.py migrate
python manage.py seed_demo
python manage.py createsuperuser   # opcjonalnie, do panelu /admin/
python manage.py runserver
```

Linux / macOS: aktywacja venv to `source .venv/bin/activate`, reszta bez zmian.

Aplikacja działa pod `http://127.0.0.1:8000/`.

## Dane demo

Komenda `python manage.py seed_demo` tworzy produkty, firmy, bazę leadów, przykładowe aktywności oraz konta (hasło każdego: `demo12345`):

| Konto | Rola | Uwagi |
|---|---|---|
| `agent1` | Agent | ma kilka leadów w różnych statusach |
| `agent2` | Agent | ma **zaległe oddzwonienie**, więc `POST /api/leads/next/` zwróci mu 409 |
| `manager1` | Manager | widzi wszystkie leady, zarządza słownikami |

`python manage.py seed_demo --reset` usuwa bazę demo i tworzy ją od nowa. Same role i konta (bez leadów) tworzy `python manage.py setup_roles --demo`.

Dane w repozytorium są wyłącznie fikcyjne.

## Adresy

| Adres | Co to |
|---|---|
| `/api/docs/` | dokumentacja Swagger (interaktywna) |
| `/api/schema/` | schemat OpenAPI |
| `/api/` | lista endpointów REST (DRF) |
| `/admin/` | panel administratora |
| `/crm/leads/` | strona HTML z listą leadów (wymaga logowania) |

## Uwierzytelnianie (JWT)

1. `POST /api/token/` z body `{"username": "agent1", "password": "demo12345"}` zwraca `access` i `refresh`.
2. Token `access` wysyłamy w nagłówku `Authorization: Bearer <access>`. Żyje 5 minut.
3. `POST /api/token/refresh/` z `{"refresh": "..."}` zwraca nowy `access`.

W Swaggerze: przycisk **Authorize** i wklejenie samego tokenu `access` (bez słowa „Bearer").

## Role i uprawnienia

| Rola | Co może |
|---|---|
| bez roli | nic (403) |
| **Agent** | widzi i edytuje tylko swoje leady i ich historię, dodaje aktywności do swoich leadów, losuje leady z puli, czyta produkty, firmy i paczki |
| **Manager** (grupa Manager lub superuser) | widzi wszystko, dodaje i zmienia produkty, firmy, paczki, usuwa leady |

Własne klasy uprawnień (`crm/permissions.py`): `IsAgentOrManager`, `IsManager`, `IsManagerOrReadOnly`, `IsLeadOwnerOrManager` (dostęp do leada i jego historii ma opiekun albo manager).

## Endpointy

CRUD (`ModelViewSet`) i odczyt:

| Endpoint | Metody | Uwagi |
|---|---|---|
| `/api/products/` | CRUD | zapis tylko manager |
| `/api/companies/` | CRUD | zapis tylko manager; szukanie po nazwie, NIP, KRS, REGON |
| `/api/batches/` | CRUD | zapis tylko manager |
| `/api/leads/` | CRUD | agent: swoje; usuwanie tylko manager |
| `/api/activities/` | tworzenie, lista, szczegóły | bez edycji i usuwania (historia jest niezmienna) |
| `/api/assignments/` | tylko odczyt | historia przypisań |

Endpointy poza CRUD (logika biznesowa):

| Endpoint | Co robi |
|---|---|
| `POST /api/leads/next/` | losuje lead z puli i przypisuje zalogowanemu agentowi; opcjonalnie `?batch=<id>`; 404 gdy pula pusta, 409 gdy są zaległe oddzwonienia |
| `GET /api/my-leads/` | leady zalogowanego użytkownika |
| `GET /api/my-leads/due/` | leady do zrobienia: zaległe i z terminem w oknie `?minutes=` (domyślnie 15, max 1440) |

Filtrowanie, wyszukiwanie, sortowanie i paginacja (20 na stronę):

- `GET /api/leads/?status=callback&batch=1&product=2&owner=3`
- `GET /api/leads/?search=Alfa` (szuka po kontakcie, telefonie, e-mailu, nazwie firmy, NIP i KRS)
- `GET /api/leads/?ordering=next_contact_at`

## Testy

```powershell
python manage.py test
```

27 testów `APITestCase` (`crm/tests.py`) sprawdza: autoryzację JWT i role (401/403), widoczność leadów, walidację, wyszukiwanie po firmie/NIP/KRS, losowanie `leads/next` (w tym 404 i 409), skutki dodania aktywności oraz `my-leads/due`.

## Zrzuty ekranu

Dokumentacja Swagger (`/api/docs/`):

![Swagger, widok 1](docs/swagger1.png)
![Swagger, widok 2](docs/swagger2.png)

Strona z listą leadów (`/crm/leads/`):

![Lista leadów, widok 1](docs/lead_list1.png)
![Lista leadów, widok 2](docs/lead_list2.png)

## Struktura projektu

```
config/                      ustawienia i główne trasy
crm/
  models.py                  modele
  serializers.py             serializery i walidacja
  views.py                   ViewSety i akcje
  services.py                logika biznesowa (losowanie leadów, skutki aktywności)
  permissions.py, roles.py   role i uprawnienia
  tests.py                   testy APITestCase
  management/commands/       setup_roles, seed_demo
  templates/, static/        strona HTML (MVT)
docs/                        zrzuty ekranu do README
```
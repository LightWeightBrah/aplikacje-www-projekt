# Nagrywanie rozmów (opcjonalne)

Nagrywanie jest **domyślnie wyłączone**. Bez niego CRM i dialer działają normalnie. Po włączeniu:

1. Agent klika „Zadzwoń” w CRM, a telefon (Solid Dialer 1.2+) wybiera numer i nagrywa rozmowę.
2. Po rozmowie telefon wysyła nagranie do CRM. Przy braku internetu nagranie czeka i idzie później (do 7 dni).
3. CRM odkłada plik na **Dysk Google** połączonego konta, osobno dla każdego agenta i miesiąca:
   ```
   Solid CRM – nagrania/
     Jan Kowalski (agent1)/
       2026-11/
         2026-11-08 14-32 Jan Kowalski Omega Logistyka 555300001.ogg
   ```
   Pliki zaczynają się od daty i godziny, więc w folderze układają się po kolei. Szukanie na Dysku
   po nazwie firmy albo numerze też działa (są w nazwie pliku).
4. Manager słucha w CRM: **Zespół → kliknij leada → „Nagrania rozmów” → ▶ Odtwórz**.
5. Nagrania starsze niż ustawiona liczba dni (domyślnie 90) CRM sam usuwa z Dysku.

Nagrywane są tylko rozmowy, w których klient odebrał. Format: Opus (.ogg), ok. 0,18 MB na minutę, więc
15 GB darmowego Dysku Google wystarcza na wiele miesięcy.

## Prawo (RODO) – przed włączeniem
- Klient musi **na początku rozmowy** usłyszeć, że rozmowa jest nagrywana.
- Agenci muszą wiedzieć, że ich rozmowy są nagrywane (najlepiej informacja na piśmie).
- Decyzję podejmuje firma (administrator danych). Najlepiej użyć **osobnego, firmowego konta Google**, a nie prywatnego.

## 1. Jednorazowo: dane Google dla CRM (ok. 10 minut)
Robi to osoba z dostępem do serwera, na koncie Google, na którym mają leżeć nagrania.

1. Wejdź na https://console.cloud.google.com/ i załóż projekt (np. „Solid CRM”).
2. **APIs & Services → Library** → wyszukaj **Google Drive API** → **Enable**.
3. **Google Auth Platform** (menu ☰ → APIs & Services → OAuth consent screen). Po lewej są zakładki
   Overview, Branding, Audience, Clients, Data Access.
   1. **Overview** → niebieski przycisk **Get started**. Kreator ma 4 kroki:
      - *App Information*: **App name** `Solid CRM`, **User support email** wybierz swój adres → **Next**;
      - *Audience*: zaznacz **External** → **Next**;
      - *Contact Information*: wpisz swój e-mail → **Next**;
      - *Finish*: zaznacz zgodę na „Google API Services: User Data Policy” → **Continue** → **Create**.
      (Jeśli zamiast „Get started” widać wykresy, kreator był już zrobiony – przejdź dalej.)
   2. **Data Access** → **Add or remove scopes** → w polu „Filter” wpisz `drive.file` → zaznacz
      `.../auth/drive.file` („See, edit, create, and delete only the specific Google Drive files you use
      with this app”) → **Update** → na dole **Save**. To dostęp tylko do plików, które CRM sam utworzy.
   3. **Audience** → w „Publishing status” przycisk **Publish app** → **Confirm**. Status zmieni się na
      „In production”. Bez tego (tryb „Testing”) Google co 7 dni odcina dostęp. Zakres `drive.file`
      nie wymaga weryfikacji przez Google.
4. **Clients** → **+ Create client**:
   - **Application type**: **Web application**, **Name**: `Solid CRM`;
   - niżej **Authorized redirect URIs** → **+ Add URI** i wklej
     `https://lightweightbrah.eu.pythonanywhere.com/crm/recordings/google/callback/`;
     do testów u siebie jeszcze raz **+ Add URI**: `http://127.0.0.1:8001/crm/recordings/google/callback/`
     („Authorized JavaScript origins” zostaw puste);
   - **Create**. W okienku pojawią się **Client ID** i **Client secret** – od razu je zapisz
     (najlepiej **Download JSON**), bo sekret Google pokazuje tylko raz.
5. Skopiuj **Client ID** i **Client secret** do pliku `.env` (nie do repo!). Na serwerze: `.env` w folderze
   projektu na PythonAnywhere; do testów u siebie: `.env` w `C:\Dev\aplikacje-www-projekt` (jest w `.gitignore`):
   ```
   GOOGLE_CLIENT_ID=....apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=....
   ```
6. PythonAnywhere: zakładka **Web → Reload**.

## 2. W CRM (manager)
**Ustawienia i oferty → Nagrywanie rozmów**:
1. **Połącz z Dyskiem Google** → zaloguj się na konto na nagrania → „Zezwól”.
   Google może pokazać ostrzeżenie „Google nie zweryfikował tej aplikacji” – to Twoja własna aplikacja:
   „Zaawansowane” → „Przejdź do Solid CRM”.
2. Zaznacz **Nagrywaj rozmowy z dialera**, ustaw po ilu dniach usuwać nagrania → **Zapisz nagrywanie**.
3. W kolumnie „Telefony agentów” widać, na których telefonach nagrywanie już działa.

## 3. Na telefonie każdego agenta (raz)
1. Zainstaluj Solid Dialer **1.2** (`/crm/dialer.apk`) na starą wersję – parowanie zostaje.
2. W aplikacji: **Włącz nagrywanie rozmów** → zgoda na mikrofon.
3. Otworzą się **ułatwienia dostępu**: „Zainstalowane aplikacje” / „Pobrane usługi” →
   **Solid Dialer – nagrywanie rozmów** → włącz.
4. Jeśli przełącznik jest szary („Ustawienie z ograniczeniami”, Android 13+): w aplikacji
   **Otwórz informacje o aplikacji** → ⋮ (prawy górny róg) → **Zezwól na ustawienia z ograniczeniami** → wróć do kroku 3.

Dlaczego ułatwienia dostępu: od Androida 10 zwykła aplikacja dostaje w trakcie rozmowy telefonicznej ciszę
z mikrofonu. Wyjątek Android robi dla aplikacji z włączoną usługą ułatwień dostępu (tak działają też aplikacje
typu Cube ACR). Usługa Solid Dialera nic nie czyta z ekranu i niczym nie steruje.

**Jakość:** nagrywany jest mikrofon telefonu, więc agenta słychać zawsze dobrze, a klienta zależnie od modelu
telefonu. Jeśli klient jest cichy, pomaga rozmowa na głośniku albo na słuchawkach przewodowych.
Sprawdź to na początku na jednym telefonie (zadzwoń na swój drugi numer).

## Odłączenie
**Odłącz Dysk Google** w Ustawieniach: telefony przestają nagrywać, Google cofa dostęp CRM,
a nagrania, które już są na Dysku, zostają tam (można je usunąć ręcznie).

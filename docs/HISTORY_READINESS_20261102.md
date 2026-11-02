# Checkpoint 2026-11-02 — audyt gotowości historii

Baza: 916650e1772ae817d3c2b2a7ff2375621c7833d2. Remote ref sprawdzony przed zapisem;
brak nowych cudzych commitów. PR #2 odczytany: open, nie zmieniono stanu.

Dodano shadow_history_audit.py (read-only CLI CSV) i 6 testów. Audyt rozdziela
kompletność okresów, poprawność cen/JWCD, flagę walidacji actual i dowód dostępności
JWCD przed D-1 09:50 (publikacja, start i koniec pobrania). Nie dopuszcza 24h
etykiet na dobach DST bez jawnych okresów UTC, wykrywa duplikaty, braki, NaN
oraz błędne daty. Nie wykonuje SQL, aktualizacji, importów ani eksportu arkusza.

Wykonanie na DWÓCH prawdziwych plikach referencyjnych Fix1 w repozytorium:
- 02.11.2026: 24/24 poprawne ceny i okresy, JWCD 0, dowody dostępności 0.
- 03.11.2026: 24/24 poprawne ceny i okresy, JWCD 0, dowody dostępności 0.
- model_ready_days=0. Brak kolumny actual_verified w tych CSV również jest
  wskazany; sam komentarz o referencji nie stanowi maszynowego dowodu walidacji.
To wynik tych dwóch CSV, NIE pełny audyt wszystkich danych w PostgreSQL.
Wynik maszynowy zapisany osobno w docs/HISTORY_REFERENCE_READINESS_20261102.json.

Odczyt history_import.py wykazał:
- sync_pk5 nadpisuje jwcd kolejnymi snapshotami, bez ograniczenia czasu cutoff;
- pomija doby inne niż 24h;
- sync_actuals kopiuje actual.tge_fix1 bez bramki jakości;
- seed import wykonuje ON CONFLICT DO UPDATE.
Importera nie uruchamiano i nie modyfikowano.

Odczyt logów Railway km1-readonly-audit zwrócił ostatni raport z 01.11 17:04:58 UTC,
nie raport dzisiejszy. Nie uruchamiano usługi ani zapytań do bazy, nie przedstawiać
starych 51 snapshotów/1080 cen jako aktualnego stanu z 02.11.

Źródło XLSB odzyskano do lokalnej analizy read-only; brak wartości w Library read.
Plik 21436884 bajty, SHA256 8d06d403720703cd09735a846f91ff6802fef1304af211b67577c1120fbb73b6.
ZIP testzip bez błędów, 11 binarnych strumieni arkuszy. Nie odczytano komórek,
zakresów dat ani par JWCD/Fix1: brak pyxlsb/calamine/LibreOffice w środowisku.
Nie kopiowano pliku użytkownika, jego komórek ani formuł do publicznego repozytorium.
Nie wolno z kontroli ZIP wywodzić kompletności historii.

Lokalnie 69 testów: 57 OK, 12 PostgreSQL SKIPPED. Commity 82d58a5/e96b122.
Konieczne potwierdzenie CI. Brak backtestu i rankingu; historia nadal nie została
zweryfikowana w całości. Produkcja, 90D, FROZEN, archiwa i konfiguracja bez zmian.

## Dowód CI

https://github.com/mateuszpielka-arch/model-b-energy/actions/runs/41471340391
Commit e96b12295aa8de4fe3bbc2661e6afaead60566a2; job 125365670993.
Krok unittest zakończony success. Odczytane logi: Ran 69 tests, OK, bez pominięć,
w tym 12 integracyjnych testów PostgreSQL. Nie oznacza to walidacji komórek XLSB.

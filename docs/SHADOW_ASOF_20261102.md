# Checkpoint 2026-11-02 — JWCD dostępne przed cutoff

Baza 9f911bc03a5535b8889c5d3edf718b9a1151cdd1. PR #2 odczytany: open.
Przed zapisem sprawdzono remote ref bez nowych cudzych zmian.

Dodano shadow_asof.py: czysta funkcja select_snapshot, bez SQL/sieci/mutacji.
Wybiera najpóźniej zakończony kompletny snapshot dostępny do D-1 09:50 Warsaw.
Każdy kandydat musi mieć jawne retrieved_at, retrieval_completed_at oraz
publication_ts dla KAŻDEGO okresu. Warunki: start pobrania <= koniec <= cutoff;
każda publikacja <= koniec pobrania. Wymagane kompletne unikalne UTC okresy
23/24/25, właściwa data dostawy, skończone JWCD, unikalny snapshot_id.
Odrzucone kandydatury mają listę powodów. Brak dowodu oznacza nieweryfikowalność,
a nie automatyczne dopuszczenie historycznych danych. Nie zmienia to produkcji.

W odczytanym collector.py potwierdzono, że out['retrieved_at_utc'] jest ustawiane
PRZED fetch_pse. Nie wolno utożsamiać tego znacznika z końcem pobierania.
Legacy raw.snapshots nie zawiera osobnego pola retrieval_completed_at; adapter
musi znaleźć wiarygodny dowód zakończenia (np. powiązany log) albo oznaczyć
snapshot jako nieweryfikowalny. Nie zgadywać timestampów lokalnych ani DST.
To uwaga z kodu repozytorium, NIE nowy audyt bazy Railway.

11 nowych testów: wybór najnowszej dopuszczalnej rewizji, późniejsze pobranie,
request start przed cutoff / koniec po cutoff, brak dowodu końca, późna publikacja,
braki i duplikaty, nieprawidłowe JWCD, naive timestamps, powtórzone ID, 23/25h,
letnia granica czasu, równość cutoff i niemutowanie wejścia.
Pierwsza próba testu 25h ujawniła błędną stałą UTC w fixture — poprawiono fixture
na Europe/Warsaw, bez zmiany algorytmu selekcji.

Lokalnie: 63 testy, 51 OK, 12 PostgreSQL SKIPPED. Commity 507431f / 6332d34.
Wymagane potwierdzenie CI. Dane testowe JWCD są syntetyczne; nie wyliczono
pokrycia prawdziwej historii, żadnego backtestu ani rankingu modeli.
Brak adaptera legacy payload PSE -> kanoniczne okresy UTC, szczególnie DST.
Funkcja jest przygotowaniem do walk-forward; sam walk-forward nie jest wdrożony.

Produkcja, model 90D, FROZEN, archiwa, konfiguracja Railway i PR #2 bez zmian.

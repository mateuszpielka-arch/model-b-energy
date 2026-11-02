# Checkpoint 2026-11-02 — trwały scoring w staging PostgreSQL

Baza gałęzi: b1247d7afa9e56d121d9cece85772078b0f3ce0a. PR #2 sprawdzony: open.
Remote ref ponownie sprawdzony przed zapisem; brak nowych cudzych zmian.

Dodano shadow_postgres_scores.py oraz 6 testów PostgreSQL.
Scorer pobiera actual z testowego archiwum, sprawdza pełne 24 okresy/jeden batch,
odrzuca kwarantannę, ponownie sprawdza hash surowego HTML i porównuje KAŻDĄ
zapisaną cenę/okres z wynikiem parsera. Dopiero wtedy wywołuje score_day z kontrolą
publication_ts <= retrieved_at <= issued_at <= D-1 09:50 Europe/Warsaw.
Parser nadal blokuje DST; nie deklarujemy pełnej ścieżki archiwizacji/scoringu DST.

Wynik trafia do osobnej tabeli fix1_shadow_test.scores w jednej transakcji pod
blokadą daty. Klucz forecast_id/model_version/batch_id/scoring_version, fingerprint
każdego wejścia prognozy i timestampów, niezmienny JSONB z metrykami i pokryciem.
Powtórka zgodnych wejść daje REPLAY, inna prognoza o tym samym kluczu daje błąd,
bez UPDATE wcześniejszego wyniku. Kolejność wejściowych okresów nie wpływa na replay.
Wersja actual jest związana z datą, hashami raw/referencji oraz wersją parsera.

Nowe testy integracyjne: insert/replay/coverage; konflikt prognozy bez nadpisania;
niezgodne z raw actual i brak okresu; kwarantanna; przekroczony cutoff;
cztery równoległe identyczne scoringi. Dane forecast w testach są SYNTETYCZNE,
nie są oceną żadnego rzeczywistego FROZEN ani wynikiem skuteczności modelu.
Zmodyfikowano czyszczenie testowych tabel archiwum: CASCADE uwzględnia nową
zależność scores. Dotyczy tylko disposable schema, nigdy produkcji.

Lokalnie: python -m unittest discover -s tests -q -> 52 testy, 40 OK, 12 SKIPPED
(wszystkie integracyjne PostgreSQL). Wymagane potwierdzenie pełnego CI.
Commity: 448db59 (moduł), ad8be2f (testy), f276cf5 (reset fixture).

Nie zmieniono kolektora, modelu 90D, FROZEN, istniejących archiwów, konfiguracji
Railway ani PR #2. Ten moduł łączy wyłącznie lokalny disposable PostgreSQL;
nie jest wdrożonym automatycznym scoringiem produkcyjnym.
Następny krok: potwierdzić CI i dodać provenance historycznych snapshotów oraz
read-only raport gotowości danych do walk-forward (bez liczenia na błędnej historii).

## Potwierdzenie CI

https://github.com/mateuszpielka-arch/model-b-energy/actions/runs/41451803205
Commit f276cf53e3f94bd6eef584f623eeeab73c640586; job integration 125298753557:
completed/success. Odczytano logi: 52 testy, OK, bez pominięć. Wszystkie 12
integracyjnych testów PostgreSQL wykonało się, w tym 6 nowych trwałego scoringu.
To usuwa blokadę potwierdzenia CI; nie zmienia ograniczeń wdrożenia produkcyjnego.

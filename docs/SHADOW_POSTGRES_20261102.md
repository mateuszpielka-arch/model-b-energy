# Checkpoint 2026-11-02 — adapter PostgreSQL SHADOW

Baza: cb88640fb7dcd81967370e8fb0d467978d14c60b. Przed zapisem sprawdzono zdalny
ref gałęzi test/tge-layout-validation-20261101: bez cudzych nowych commitów.
PR #2 sprawdzony jako open, nie zmieniono go.

Zapisane zmiany:
- shadow_postgres_archive.py: jawny DSN lokalnej bazy fix1_shadow_test*, bez odczytu
  DATABASE_URL; dedykowany schema fix1_shadow_test, źródłowe paczki, ceny, kwarantanna.
- Jedna transakcja na decyzję i cały zapis; pg_advisory_xact_lock na datę dostawy.
  INSERT pełnej doby, REPLAY identycznych cen, QUARANTINE konfliktów/częściowej doby.
  Żaden kod adaptera nie wykonuje UPDATE/DELETE cen. Nieprzewidziany konflikt
  unikalności lub błąd w połowie inserta ma wycofać całą transakcję.
- Surowy HTML jest zachowywany i musi pasować do SHA256 paczki. Dowód niezależnej
  walidacji referencji pozostaje obowiązkiem warstwy admission.
- tests/test_shadow_postgres_archive.py: 6 testów integracyjnych: insert/replay,
  konflikt bez nadpisania, częściowa doba, błąd w połowie/rollback, czterech równoległych
  writerów oraz błędny hash źródła. Testy zmieniają jedynie disposable test schema.
- .github/workflows/fix1-shadow-postgres.yml: PostgreSQL 16 jako jednorazowa usługa,
  jawne testowe hasło, zero sekretów produkcji, prawa contents:read, timeout 10 min,
  trigger wyłącznie push w gałęzi testowej i wskazane ścieżki.

Dowody lokalne:
python -m unittest discover -s tests -q -> 42 testy, 36 OK, 6 SKIPPED.
Sześć SKIPPED to WSZYSTKIE testy PostgreSQL; bez FIX1_SHADOW_TEST_DSN baza nie jest
używana. NIE jest to dowód atomowości, rollbacku ani współbieżności adaptera.
Brak lokalnego silnika/drivera pozostaje blokadą testów integracyjnych.
Po zapisie workflow odczyt GitHub actions/runs dla gałęzi zwrócił total_count=0.
Nie potwierdzono uruchomienia CI; nie nazywać adaptera przetestowanym na PostgreSQL.

Commity kodu: 69f3666 (adapter), fe49192 (testy), e6a41d8 (workflow).
Produkcja, model 90D, FROZEN, istniejące archiwa, konfiguracja Railway i PR #2
bez zmian. Moduł nie jest podłączony do kolektora.

Kolejny krok: uzyskać i sprawdzić wynik sześciu testów na izolowanym PostgreSQL,
naprawić ewentualne błędy; dopiero potem budować trwały zapis scoringu.

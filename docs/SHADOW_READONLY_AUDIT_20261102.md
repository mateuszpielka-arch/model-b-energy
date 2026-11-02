# Checkpoint 2026-11-02 — audit istniejących schematów w READ ONLY

Baza8536a0fba4b2a1c05c531ac6b47fb0c4f76bfc9c. PR#2 sprawdzony:open.
Sprawdzono remote ref przed zapisem. Brak modyfikacji produkcji/90D/FROZEN/archiwów.

Próba dostępu do PostgreSQL Railway: OAuth zwraca nazwy zmiennych, valuesRedacted=true.
Nie uzyskano DSN/hasła; brak lokalnego CLI i tokenów Railway. Nie pobierano ani
zapisywano sekretów. Odczyt logów km1-readonly-audit nadal zwraca wynik01.11
17:04:58UTC; nie przedstawiać go jako aktualny raport02.11. Nie restartowano/
redeployowano funkcji ani nie zmieniano konfiguracji w celu uzyskania danych.

Dodano shadow_readonly_audit.py: jawny DSN z prywatnego lokalnego pliku,
bez DATABASE_URL i bez eksportu surowych payloadów/sekretów.
Połączenie ma default_transaction_read_only=on i statement_timeout30s.
Każdy audit rozpoczyna top-level transakcję, wykonuje SET TRANSACTION READ ONLY,
weryfikuje SHOW transaction_read_only i dopiero wtedy odczytuje istniejące tabele.
Brak tabel raportuje bez CREATE. Brak dowodu końca pobrania blokuje model_ready;
nie zgaduje lokalnych timestampów PSE ani mapowania etykiet DST.

Wynik: per-snapshot liczby okresów/JWCD/publikacji, duplikaty surowych etykiet,
czas retrieval; per-day actual liczby okresów, zgodność z23/24/25, liczba cen60/2026.
Ta ostatnia to diagnostyka, NIE samodzielny dowód błędnej ceny. Źródło musi zostać
porównane z TGE. Brak realnego uruchomienia na produkcyjnej bazie w tym cyklu.

4 nowe testy PostgreSQL na disposable fix1_shadow_test imitują stare schematy:
25JWCD/24actual i jawna blokada availability; odczyt bez zmiany rekordów;
serwerowe odrzucenie INSERT na readonly connection; brak tabel bez ich tworzenia.
Wszystkie DDL/DML testów są ograniczone connect_test_database do lokalnej bazy
z prefiksem testowym i nigdy nie używają produkcyjnych sekretów.
Lokalnie117testów:99OK,18PostgreSQL SKIPPED. Wymagane potwierdzenie CI.

Nie jest to wykonany aktualny audyt Railway. Kolejny potrzebny dowód: autoryzowany
read-only dostęp lub aktualny export snapshotów z wiarygodnym czasem zakończenia,
a także źródłowo zweryfikowane historyczne actual. Backtest rynkowy nadal niewykonany.

## Dowód CI

https://github.com/mateuszpielka-arch/model-b-energy/actions/runs/41535498955
Commit2892f5d43f9d91636313a4493033793aef97d217;job125586179877 completed/success.
Odczytane logi:117testów,OK,bez pominięć,także18 integracyjnych PostgreSQL.
Potwierdzono serwerowe odrzucanie zapisu i brak mutacji podczas audytu.
To dowód na disposable bazie, nie aktualny wynik danych produkcyjnych Railway.

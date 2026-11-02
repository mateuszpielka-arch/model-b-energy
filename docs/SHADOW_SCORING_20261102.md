# Checkpoint 2026-11-02 — scoring SHADOW

Baza gałęzi test/tge-layout-validation-20261101: 18fd07465615f2c5a7cff9cebbeb7e3fba6ca2d6.
Przed zapisem ponownie sprawdzono remote ref: bez zmian. PR #2 nadal open,
head 5e993ee966458af7457a074c6083e3cf383d6ca9. Nie zmieniono PR.

Dodano shadow_scoring.py i tests/test_shadow_scoring.py.
- Pure scoring Decimal: MAE, MAPE, WAPE, bias (forecast - actual).
- MAPE pomija wyłącznie zera i zwraca liczbę użytych/wyłączonych okresów.
  Gdy brak niezerowych actual: MAPE=None; WAPE=None gdy suma abs(actual)=0.
- Wymagane kompletne identyczne okresy UTC dla doby Europe/Warsaw (23/24/25).
  Duplikaty, błędne i niefinitywne ceny, naive timestamps są odrzucane.
- Wymagane jawne forecast_id, model_version, actual_version i FROZEN/SHADOW.
- Chronologia: publication_ts <= retrieved_at <= issued_at <= D-1 09:50 Warsaw.
  Wejściowe czasy muszą pochodzić z niezmiennego provenance; dla wielu JWCD
  należy przekazać najpóźniejsze czasy. Funkcja nie odczytuje historii za wywołującego.
- Actual musi mieć actual_verified=True od niezależnej walidacji źródła.
  Flaga nie jest dowodem autentyczności; integrator musi dostarczyć ten dowód.
- Wynik jest niezmiennym dataclass z tożsamością forecast/model/actual/scoring version.
  Powtórne wywołanie jest deterministyczne; trwała idempotencja w PostgreSQL
  NIE została jeszcze zaimplementowana ani przetestowana.

Weryfikacja: python -m unittest discover -s tests -v -> 29/29 OK
(12 nowych testów scoringu + 17 istniejących testów parsera).
Kontrola ręczna: powtórzony 8 razy wektor actual [0,-100,100], forecast [10,-80,90]
daje MAE=13.3333..., bias=6.6666..., MAPE=15% (16/24), WAPE=20%.
To wyniki testów syntetycznych, NIE wynik skuteczności modelu na danych rynkowych.

Ograniczenia: brak adaptera parser->okresy UTC dla DST; parser nadal blokuje DST.
Brak nowego audytu Railway/SQL, archiwizacji PostgreSQL, backtestu lub rankingu.
Moduł nie jest importowany w tge_collector.py ani forecast.py. Produkcja, FROZEN,
archiwum i konfiguracja pozostały bez zmian. Kolejny krok: staging PostgreSQL
z atomowością, kwarantanną konfliktów i testami integracji na izolowanej bazie.

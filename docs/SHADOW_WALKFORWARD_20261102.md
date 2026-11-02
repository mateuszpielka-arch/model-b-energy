# Checkpoint 2026-11-02 — runner i ranking na wspólnym zbiorze

Baza 31eeaeb4e7c93551226f1699533a4bc59b99d598; sprawdzono remote ref bez nowych
cudzych zmian i PR #2 open. PR i produkcja bez ingerencji.

Dodano shadow_walkforward.py i 8 testów. run_walkforward uruchamia na każdej
dacie OLS30/60/90, similar_shape90/k5, similar_level90/k5 i median4 (OLS3+shape).
Parametry są z góry stałe. Każdy krok korzysta z historycznych bramek as-of.
Skoring docelowych actual wymaga niezależnej walidacji, kompletności i czasu
udostępnienia <= jawnie podany evaluated_at. Target actual NIE jest cechą treningową.

Równe warunki rankingu: dzień jest dopuszczony wyłącznie, jeśli wszystkie 6 modeli
i scoring są gotowe; w przeciwnym razie raport skipped zawiera datę, etap i powód.
Brak wspólnych dni = pusta tabela rankingu, nie sztuczny zwycięzca.
MAE/bias agregowane po WSZYSTKICH okresach UTC wspólnego zbioru. WAPE używa
sumy abs(error)/sumy abs(actual), MAPE jawnie pomija wyłącznie actual=0 z pokryciem.
Ranking wg MAE, dokładne remisy dostają ten sam rank (competition rank).
Predykcje pozostają surowymi Decimal, bez clippingu/zaokrąglenia do publikowanych
2 miejsc — przed porównaniem z produkcyjnym FROZEN trzeba jawnie uzgodnić precyzję.
Nie dołączono jeszcze stałego produkcyjnego 90D jako osobnego benchmarku.

Tryb to WYŁĄCZNIE retrospektywna symulacja (simulation=True). issued_at jest
symulowaną granicą D-1 09:50, a nie sfabrykowanym dowodem zapisania realnej prognozy.
Nie zapisuje FROZEN i nie podszywa się pod produkcyjne forecast_id.
Caller musi dostarczyć rzeczywiste dowody snapshotów i cen. Flagowanie syntetycznych
wejść jako verified w testach nie weryfikuje prawdziwej historii.

8 testów: 2 dni/48 wspólnych okresów; brak dnia okna90 odrzuca dzień dla wszystkich;
zmiana własnych actual nie zmienia własnych prognoz; niedostępne actual blokują
ranking; zera i ujemne actual; duplikaty i niemutowanie wejścia; brak danych bez
zwycięzcy; dzień24+25 daje49 okresów i niezależnie policzone MAE/WAPE.
Dane modele/runner w testach są SYNTETYCZNE. Nie publikujemy ich jako wyników rynku.

Lokalnie 98 testów: 86 OK, 12 PostgreSQL SKIPPED. Commity 39920f9 / 24a4dbc.
Wymagane potwierdzenie CI. Nie wykonano jeszcze rzeczywistego backtestu ani rankingu.
Zweryfikowana historia/adaptacja PSE/DST i odczyt wartości XLSB nadal wymagają pracy.
Nie zmieniono produkcyjnego 90D, FROZEN, archiwów, konfiguracji Railway ani PR #2.

## Dowód CI

https://github.com/mateuszpielka-arch/model-b-energy/actions/runs/41498860264
Commit 24a4dbc7f5f6fa2d0f01903ae233c17a926e5c5f, job 125461320421:
completed/success. Odczytane logi: 98 testów, OK, bez pominięć (także PostgreSQL).
Nie jest to dowód wyników rzeczywistych modeli; runner sprawdzono syntetycznie.

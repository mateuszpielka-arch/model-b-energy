# Checkpoint 2026-11-02 — dni podobne i mediana SHADOW

Baza a1bea40b91378af3ddbf5d76d153648fd1ccc86e. Remote ref sprawdzony przed zapisem,
bez cudzych nowych zmian. PR #2 sprawdzony open; stan i gałąź PR nietknięte.

Dodano shadow_training.py: wspólne bramki używane teraz przez OLS i dni podobne;
logika dopuszczenia OLS zachowana i objęta dotychczasowymi testami.
Dodano shadow_similar_consensus.py:
- predict_similar(mode=level|shape,window_days=30|60|90,k): k sąsiadów spośród
  zweryfikowanych dni przed cutoff, predykcja jako średnia równoważona ich cen;
- level: średni kwadrat różnicy MW; shape: to samo po odjęciu średniej dobowej,
  bez normalizacji wariancją. Parametry muszą być ustalone przed testem out-of-sample;
- remisy dystansu rozstrzyga najnowsza data, niezależnie od kolejności danych;
- wszystkie dni okna muszą przejść walidację historii. Do dystansu wymagany ten sam
  zbiór lokalnych godzin; nieporównywalne profile jawnie raportowane w excluded;
- powtórka godziny DST uśredniana w profilu, wynik ma wszystkie oryginalne okresy UTC.
  Cel 25h obsługiwany na kanonicznych danych testowych; cel 23h może zostać zablokowany
  z braku k porównywalnych profili. Nie twierdzimy, że rozwiązano oficjalne mapowanie DST.
- median_consensus wymaga dokładnie kluczy ols30/ols60/ols90/similar_shape, wspólnego
  snapshotu celu i daty, kompletnych identycznych UTC okresów. Dla 4 członków bierze
  średnią dwóch środkowych wartości. Brak członka nie uruchamia częściowej mediany.
  Caller wiąże klucze ze z góry ustalonymi wersjami modeli; funkcja sama nie certyfikuje
  pochodzenia dostarczonych prognoz. Test integracji łączy faktyczne wyjścia wszystkich
  czterech implementacji na tym samym syntetycznym wejściu.

11 nowych testów: znani sąsiedzi i ceny, shape/remisy, przyszły dzień/kolejność,
brak dnia/k za duże, późne actual/niedowiedziony JWCD, niemutowanie danych,
jawna porównywalność DST, mediana/outlier, brak członka, rozbieżny snapshot/okres,
połączenie OLS30/60/90+similar. Wszystkie testy modeli używają danych syntetycznych.
Lokalnie: 90 testów, 78 OK, 12 PostgreSQL SKIPPED. Wymagane potwierdzenie CI.
Kod/testy: 909368f / 7114d44 / 6c3d04b / a053a83.

Nie wykonano rzeczywistego backtestu walk-forward ani rankingu. Brak zweryfikowanej
historii i adaptera legacy PSE/DST nadal blokuje wnioski rynkowe. Brak Ridge/PV/wiatru/P1/P2.
Nie zmieniono produkcyjnego 90D, FROZEN, archiwów, kolektora ani konfiguracji Railway.
Następny krok: runner walk-forward na wspólnych datach i raport braków przy odrzuceniu dnia.

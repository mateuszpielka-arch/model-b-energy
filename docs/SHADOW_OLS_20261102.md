# Checkpoint 2026-11-02 — OLS JWCD 30/60/90D SHADOW

Baza gałęzi test/tge-layout-validation-20261101: 3804827631b61cbd932bda656a2b8555c9fd0851.
Przed zapisem remote ref bez cudzych nowych zmian. PR #2 sprawdzony: open.

Dodano shadow_ols.py i 10 testów. predict_ols wykonuje jeden krok prognozy na
zadany dzień dla dokładnie 30/60/90 poprzednich dni kalendarzowych. Regresje
oddzielne dla 24 lokalnych godzin Europe/Warsaw, tylko JWCD, intercept+slope*x.
Brak Ridge, PV, wiatru, P1/P2, clippingu lub cichego fallbacku.

Bramki:
- historyczny JWCD wybierany przez select_snapshot według jego WŁASNEGO D-1 09:50;
- actual wymagają actual_verified=True, wersji źródła i dostępności przed cutoff
  dnia docelowego; caller musi dostarczyć rzeczywiste, niezależnie zweryfikowane dowody;
- kompletne UTC okresy actual i JWCD dla każdego wymaganego dnia;
- braki, duplikaty dat i stałe JWCD (nieoznaczony slope) blokują cały fit;
- rekordy spoza okna, w tym docelowe/przyszłe ceny, nie wpływają na trening;
- kompletne target JWCD przed cutoff; wynik zawiera współczynniki, liczebności,
  identyfikatory snapshotów i wersji actual wszystkich dni treningowych.

Jawna polityka DST w silniku: kanoniczne UTC okresy zachowane, grupowanie po
lokalnej godzinie startu. Powtórzona godzina wnosi dwa treningowe observations,
a obie powtórki celu dostają te same współczynniki. Doba 23h ma brakujący bucket
jednego dnia, ale pozostali sąsiedzi w oknie nadal uczą tę godzinę.
NIE jest to dowód mapowania oficjalnych etykiet PSE/TGE, adapter nadal brak.
Nie zmienia to polityki ani współczynników produkcyjnej prognozy.

Testy: znana relacja y=10+2*JWCD dla wszystkich 3 okien; brak dnia; późne actual;
późna historyczna rewizja JWCD; przyszłe actual; nieweryfikowane/brakujące actual;
stałe JWCD bez fallbacku; cele 23h/25h; brak mutacji/duplikaty; późny target.
Dla rachunku Decimal zastosowano tolerancję 1e-25 przy znanej relacji liniowej
(zamiast nierealistycznej dokładnej równości dzielenia w bucketach 31 obserwacji).

Lokalnie: 79 testów, 67 OK i 12 PostgreSQL SKIPPED. Commity b1261a1/298e7db.
Wymagane potwierdzenie CI. Wyłącznie SYNTETYCZNE wyniki testów OLS, bez MAPE
rynkowego, pełnego walk-forward na rzeczywistej historii, dni podobnych,
konsensusu lub rankingu. Nie przedstawiać jako skończony backtest.

Nie zmieniono forecast.py, tge_collector.py, FROZEN, istniejących archiwów,
konfiguracji Railway ani PR #2. Moduł nie jest podłączony do produkcji.
Dalszy krok: zweryfikowany historyczny adapter danych i runner walk-forward;
bez źródeł gotowość rynku/ranking pozostaje niewykazana.

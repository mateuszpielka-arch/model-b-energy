# PR #2: walidacja przed wdrożeniem — 2026-11-01

Status: NO-GO dla produkcji. PR #2 pozostaje DRAFT i otwarty.
Baza: PR head 5e993ee966458af7457a074c6083e3cf383d6ca9; main 28469149f83445458034d8444cc98d027dccbe58.
Zmiany wyłącznie w gałęzi testowej; bez podłączenia parsera do kolektora.

## Wykonane i dowody
- Odczyt konfiguracji Railway: tge-collector co 15 minut, forecast 06:40 UTC.
- Log audytu READ ONLY z 2026-11-01 17:04:58 UTC: 51 snapshotów / 46 dat,
  1080 cen / 45 dat (19.09–02.11), brak history_hourly i tabeli stored scores
  według audytu. Okna 30/60/90: 0 dni zaakceptowanych przez kontrolę jakości.
  Jest to wynik istniejącego audytu, nie nowy pełny audyt SQL ani dowód braku historii CSV.
- Na 02.11 audyt pokazuje 24 ceny po 60; kolektor raportuje COMPLETE.
- 25.10: 25 wierszy JWCD, 24 odrębne etykiety; tylko 24 ceny actual.
- Oryginalne 5 testów PR przechodzi. Po zmianach 10 testów przechodzi:
  `python -m unittest discover -s tests -v`.
- Zapisany surowy HTML TGE i manifest SHA256/czas/URL. Parser zwraca na 02.11
  dokładnie 24/24 cen zgodnych z istniejącym reference CSV (porównanie Decimal).
  To regresja na jednym rzeczywistym snapshotcie, nie pełna walidacja historyczna.
- Dodano blokadę niekompletnych dób i obu zmian czasu, odrzucenie wadliwej ceny.
  DST jest blokowane, NIE zaimplementowane. Wrapper nie potwierdza rynku/sekcji.

## Ryzyka znalezione w kodzie
1. Parser PR zakłada trzecią komórkę bez identyfikacji tabeli i nagłówków Fixing I.
   Zmiana kolumn lub obca tabela może dać poprawnie wyglądające błędne ceny.
2. Kolektor produkcyjny używa innego parsera regex; PR sam go nie naprawia.
3. Kolektor ma ON CONFLICT DO UPDATE cen actual. Komplet 24 nie certyfikuje jakości.
4. Scoring w kodzie kolektora sprawdza liczbę actual, wybiera pierwszy FROZEN bez
   jawnego model_version, nie wymaga jakości ani zgodności zbiorów kluczy; brak MAPE/WAPE.
5. forecast.py pobiera najnowszy snapshot bez ograniczenia czasem publikacji/pozyskania
   w zapytaniu; cutoff w kodzie to 10:20, nie wymagane 09:50. Nie zmieniono go.

## Bramki i kolejność — jeszcze do wykonania

### A. Parser i źródła
- Wybrać tabelę przez stabilny identyfikator + sprawdzić wielopoziomowe nagłówki:
  instrument, 60 minut, Fixing I, kurs PLN/MWh. Zmiana układu = błąd i kwarantanna.
- Dodać testy obcej tabeli (MC/Fixing II), zamiany kolejności kolumn, braku nagłówka,
  przerwanego HTML, pustej ceny, różnych duplikatów, H00/H25 i błędnej daty.
- Dodać surowe odpowiedzi dla minimum 7 kolejnych dni: dzień roboczy/weekend,
  cena zerowa/ujemna (jeżeli dostępne), brak publikacji oraz odpowiedzi 429/5xx/timeout.
  Nie rekonstruować HTML z CSV i nie nazywać go źródłem rzeczywistym.
- Osobno pozyskać oficjalne okresy 25.10.2026 i 29.03.2026. Mapować okres na
  start/end UTC + lokalną etykietę i offset/fold. Nigdy nie sklejać powtórzonej godziny.
- Dla każdej daty niezależnie odczytać oficjalny Fixing I z tabeli/eksportu TGE,
  potwierdzić datę dostawy, jednostkę i nagłówek. Porównać KAŻDY okres, nie średnią.
  Raport: expected, parsed, delta, klucz okresu, SHA źródła, wersja parsera.
  Akceptacja: identyczne klucze i zero różnic w publikowanej precyzji ceny;
  wewnętrznie Decimal. Ceny 60 i 2026 nie są same w sobie dowodem błędu.

### B. Archiwum — testy na izolowanym PostgreSQL
- Osobne tabele staging/quarantine, osobna rola bez UPDATE/DELETE na actual/FROZEN.
  Tryb dry-run domyślny. Bez produkcyjnego DATABASE_URL w testach.
- Zapisuj niezmienny surowy snapshot, SHA256, URL, czas pobrania, datę dostawy,
  parser_version, market, resolution, status walidacji i porównanie referencyjne.
- Zapis całej zweryfikowanej doby atomowo. Idempotencja po source hash/okresie;
  konflikt innej ceny = kwarantanna i alarm, nigdy automatyczny UPDATE.
- Testy transakcji: błąd w połowie = zero częściowych zapisów; równoległe uruchomienia,
  powtórzenie identycznego źródła, konflikt ceny, timeout i retry z backoff.
- Obecne archiwum pozostaje nietknięte. Odtworzenie błędnych dni wymaga osobnego
  manifestu stara/nowa wartość + dowód źródłowy i zgody użytkownika.

### C. Historia PK5/JWCD
- Read-only inwentaryzacja CSV/XLS i bazy osobno: zakresy, braki, duplikaty,
  23/24/25 okresów; powiązanie UTC zamiast samego numeru godziny.
- Dla historycznej prognozy D użyj snapshotu D dostępnego przed D-1 09:50 Warsaw:
  publication_ts ORAZ retrieved_at <= cutoff. Brak historycznego czasu dostępności
  = nieweryfikowalny backtest, nie dowód braku leakage.
- Raport pokrycia wszystkich dat i konfliktów źródeł; nie nadpisuj archiwum.

### D. Scoring
- Tylko zweryfikowany kompletny actual + kompletny forecast z identycznymi okresami,
  forecast_id/model_version/status i dowodem utworzenia przed cutoff. SHADOW osobno.
- error = forecast - actual; MAE = mean(abs(error)); bias = mean(error).
  MAPE = 100*mean(abs(error)/abs(actual)) dla actual != 0, z podaniem liczby
  wyłączonych zer i pokrycia. Dla braku niezerowych actual: NULL.
  WAPE = 100*sum(abs(error))/sum(abs(actual)); mianownik 0 => NULL.
- Testy ręcznie policzone: actual [0,-100,100], forecast [10,-80,90]:
  MAE=40/3, bias=20/3, MAPE=15% (2/3 okresów), WAPE=20%.
- Testy zer, cen ujemnych, braków/duplikatów, DST, złego modelu, późnego FROZEN,
  konfliktów i ponownego scoringu. Unikalny wynik per forecast_id + actual_version
  + scoring_version, bez aktualizacji dawnych wyników.

### E. SHADOW i ranking
- Tylko OLS JWCD 30/60/90D, dni podobne i mediana; bez Ridge/PV/wiatru/P1/P2.
- Walk-forward: trening wyłącznie na zakończonych, zweryfikowanych dniach z ceną
  dostępną przed cutoff danej prognozy. Selekcja sąsiadów i ustawień tylko w przeszłości.
- Jawnie ustalić okna kolejnych dni kalendarzowych; brak wymaganych danych = brak
  wyniku z przyczyną, bez cichego skracania okna. Dni podobne: metryka JWCD i k
  ustalone przed testem. Mediana wyłącznie kompletnych prognoz ustalonego składu.
- Porównanie na wspólnych datach/okresach wszystkich modeli, MAE jako metryka główna,
  WAPE/MAPE/bias pomocniczo; raport pokrycia, liczby dni, per godzina/weekend/DST.
  Obecny FROZEN 90D jako osobny benchmark; nie utożsamiać stałych współczynników
  produkcji z przetrenowywanym walk-forward OLS 90D. Nie publikować rankingu bez danych.

## Zgoda na wdrożenie — dopiero po dowodach
Warunki: wszystkie testy wyżej, 7 dni SHADOW bez rozbieżności, audyt uprawnień,
próba rollbacku aplikacji bez cofania danych, CI bez sekretów produkcji oraz
osobna zgoda użytkownika. PR #2 pozostaje otwarty. Produkcja, model 90D, FROZEN
oraz istniejące archiwa bez zmian. Brak nowego projektu Railway.

## Raportowanie
Utrzymać istniejące zadania, nie tworzyć duplikatów: prognoza D+1 gotowa i
sprawdzona do 09:50 Europe/Warsaw, raport postępu 18:00. Sprawdzić rzeczywisty
czas zakończenia i dostarczenie, retry oraz alarm przy braku danych przed 09:50.
Stały cron UTC nie jest dowodem dostarczenia. Harmonogramów w tej zmianie nie zmieniono.

## Checkpoint 2026-11-03 — zapis sesji walidacyjnych

- Narzędzie `tools/capture_tge_validation.py` wymaga nowej ścieżki w
  `--output`. Zajmuje ją przed pobieraniem i odrzuca istniejący katalog,
  także pusty. Kolejne uruchomienie zachowuje wcześniejszy HTML, CSV i manifest.
- Test CLI porównuje zawartość wszystkich trzech plików bajt po bajcie.
  Lokalnie uruchomiono 133 testy, z których 108 przeszło, a 25 testów
  PostgreSQL pominięto z powodu braku lokalnej instancji testowej.
- Manifest sesji jest publikowany atomowo po zapisaniu kompletnego JSON.
  Testy potwierdzają poprawny odczyt oraz brak manifestu po błędzie serializacji.
  Lokalnie przeszło 110 testów, a 25 testów PostgreSQL pominięto.

## Checkpoint 2026-11-03 — scalone komórki danych RDN

Parser SHADOW odrzuca `colspan` w komórkach danych RDN, bo może przesunąć
kolumnę ceny względem nagłówka. Test modyfikuje pojedynczą komórkę w rzeczywistej
sesji dla dostawy 02.11.2026. Lokalnie przeszło 111 testów, w tym porównanie
168 cen z siedmiu zapisanych sesji. Pominięto 25 testów PostgreSQL.

## Checkpoint 2026-11-03 — szerokość wiersza godzinowego RDN

Parser SHADOW porównuje liczbę komórek każdego wiersza godzinowego z nagłówkiem.
Test na zmodyfikowanym HTML rzeczywistej sesji odtwarza brak komórki ceny oraz
liczbowy wolumen w jej miejscu. Lokalnie przeszło 112 testów, w tym porównanie
168 cen z siedmiu zapisanych sesji. Pominięto 25 testów PostgreSQL.

## Checkpoint 2026-11-03 — jednoznaczny nagłówek Fixing I

Parser SHADOW wymaga dokładnie dwóch kolumn pod nagłówkiem Fixing I.
Test na zmodyfikowanej sesji dostawy 02.11.2026 potwierdza odrzucenie drugiej
grupy o tej samej nazwie. Lokalnie przeszło 113 testów, w tym porównanie
168 cen z siedmiu zapisanych sesji. Pominięto 25 testów PostgreSQL.
- Ekstraktor referencyjny lxml również wymaga jednej grupy Fixing I
  z dwiema kolumnami. Test potwierdza odczyt 24 cen z oryginalnej sesji
  i odrzucenie jej kopii z powtórzonym nagłówkiem. Lokalnie przeszło
  114 testów, a 25 testów PostgreSQL pominięto.
- Test ponownie uruchamia ekstraktor lxml na siedmiu zapisanych plikach HTML.
  Potwierdza zgodność dat, kolejności okresów i wszystkich 168 cen z CSV
  dla dostaw 27.10–02.11.2026. HTML i CSV pochodzą z tej samej publikacji TGE.
  Lokalnie przeszło 115 testów, a 25 testów PostgreSQL pominięto.
- Ekstraktor lxml odrzuca skrócone i scalone wiersze godzinowe. Test
  usuwa komórkę ceny z zapisanej sesji, pozostawiając liczbowy wolumen.
  Lokalnie przeszło 116 testów, a 25 testów PostgreSQL pominięto.
- Ekstraktor lxml odrzuca scalenia nagłówków przesuwające Fixing I.
  Test zmienia colspan pierwszej komórki nagłówka w zapisanej sesji.
  Lokalnie przeszło 117 testów, a 25 testów PostgreSQL pominięto.

## Checkpoint 2026-11-03 — sprzeczne snapshoty JWCD

Selekcja SHADOW blokuje dobę, gdy najnowsze dopuszczone snapshoty mają
identyczne czasy rozpoczęcia i zakończenia pobrania, ale różne wartości JWCD.
Snapshoty z identycznymi wartościami pozostają dopuszczalne. Dwa testy
potwierdzają oba przypadki. Lokalnie przeszło 119 testów, 25 pominięto.
- Raport walk-forward wskazuje odrzucone snapshoty dnia docelowego i ich
  przyczyny przed dopasowaniem modeli. Test konfliktu potwierdza wyłączenie
  tej doby ze wspólnej próby wszystkich modeli. Lokalnie przeszło 120
  testów, a 25 testów PostgreSQL pominięto.
- Raport walk-forward zachowuje przyczyny odrzucenia snapshotów również
  dla dni uwzględnionych w rankingu. Test potwierdza, że wadliwy kandydat
  jest widoczny w raporcie, a obie doby pozostają we wspólnej próbie.
  Lokalnie przeszło 120 testów, a 25 testów PostgreSQL pominięto.
- Raport walk-forward zapisuje dla użytego snapshotu JWCD najpóźniejszy
  czas publikacji oraz początek i koniec pobrania w UTC. Test porównuje
  raport z danymi wejściowymi i granicą emisji. Lokalnie przeszło 121 testów,
  a 25 testów PostgreSQL pominięto.
- Raport walk-forward zapisuje wersję i moment dostępności cen Fix1
  użytych w rankingu. Test sprawdza zgodność z danymi wejściowymi oraz
  przedział od symulowanej emisji do oceny. Lokalnie przeszły 122 testy,
  a 25 testów PostgreSQL pominięto.
- Walk-forward odrzuca ceny Fix1 oznaczone jako dostępne dokładnie
  w chwili symulowanej emisji D-1 09:50. Test potwierdza, że taka doba
  nie trafia do rankingu. Lokalnie przeszły 123 testy, a 25 testów
  PostgreSQL pominięto.
- Wspólny dobór danych treningowych OLS i dni podobnych odrzuca ceny
  historyczne oznaczone jako dostępne przed emisją lub w chwili emisji
  dla własnej doby dostawy. Test OLS sprawdza obie granice. Lokalnie
  przeszły 124 testy, a 25 testów PostgreSQL pominięto.

## Checkpoint 2026-11-04 — pokrycie historii do backtestu

Audyt CSV liczy najdłuższą serię kolejnych dób spełniających jego kryteria
oraz ile dób brakuje w tej serii do pierwszego porównania 30D, 60D i 90D.
To pomiar pokrycia, a kwalifikację treningową nadal sprawdza walk-forward.
Na zapisanych źródłach TGE dla 27.10–03.11 audyt znalazł 192 ceny w 8 dobach
i zero dni z potwierdzonymi danymi JWCD. Test regresji potwierdza, że luka
przerywa serię. Lokalnie przeszło 125 testów, 25 PostgreSQL pominięto.

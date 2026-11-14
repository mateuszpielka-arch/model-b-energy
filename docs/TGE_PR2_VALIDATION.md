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

Audyt CSV rozdziela brak znaczników czasu, błędny format lub kolejność
oraz zakończenie pobrania po D-1 09:50. Test potwierdza wszystkie kategorie
i dopuszczenie pobrania zakończonego dokładnie o 09:50. Lokalnie przeszło
126 testów, a 25 testów PostgreSQL pominięto.

Audyt CSV obsługuje również pole `retrieved_at`. Jeżeli oba warianty
nazwy są obecne, muszą wskazywać ten sam moment. Test obejmuje równoważne
offsety i konflikt. Lokalnie przeszło 127 testów, 25 PostgreSQL pominięto.

Audyt CSV wymaga dowodu dostępności cen Fix1 po symulowanej emisji.
Pole `actual_available_at` musi zawierać znacznik czasu ze strefą.
Test potwierdza blokadę braku pola i znacznika dokładnie o 09:50.
Lokalnie przeszło 128 testów, a 25 testów PostgreSQL pominięto.

Audyt CSV wymaga jednej wspólnej wersji cen Fix1 dla wszystkich godzin doby.
Test wykrywa brak wersji i mieszanie wersji. Lokalnie przeszło 129 testów,
a 25 testów PostgreSQL pominięto.

Audyt CSV wskazuje pierwszą możliwą dobę porównania dla 30D, 60D i 90D.
Wymaga dostępności wszystkich cen treningowych do emisji dnia docelowego.
Test potwierdza, że spóźnienie ceny o sekundę eliminuje kandydata pomimo
31 kolejnych kompletnych dób. Lokalnie przeszło 130 testów, 25 pominięto.

Audyt CSV zwraca wszystkie daty spełniające kryteria pokrycia i czasu
dla 30D, 60D i 90D. Test wykazuje, że opóźniona cena usuwa tylko datę,
której okno zależy od tej ceny. Lokalnie przeszło 130 testów, 25 pominięto.

Audyt CSV wykrywa mieszanie różnych okien pobrania JWCD w jednej dobie.
Test potwierdza blokadę mimo 24 okresów dostępnych do D-1 09:50.
Zgodne czasy same nie dowodzą tożsamości snapshotu. Lokalnie przeszło
131 testów, a 25 testów PostgreSQL pominięto.

Audyt CSV wymaga jednej niepustej wartości `jwcd_snapshot_id` w całej dobie.
Test wykrywa brak i mieszanie identyfikatorów mimo zgodnych czasów.
Lokalnie przeszły 132 testy, a 25 testów PostgreSQL pominięto.

## Checkpoint 2026-11-04 — audyt przekazanej historii

Audyt rozdziela doby z kompletem par JWCD/Fix1 od dób gotowych do
backtestu. Po odczycie 51 705 wierszy arkusza KSEdo10 potwierdził 2 122
kompletne doby i zero kandydatów do porównania 30D/60D/90D ze względu
na brak dowodów dostępności. Arkusz i wartości pozostają poza repozytorium.
Lokalnie przeszły 133 testy, a 25 testów PostgreSQL pominięto.

Audyt przyjmuje kolumnę `fix1_pln_mwh` z prywatnego eksportu. Na tym
CSV potwierdził 2 122 kompletne doby oraz zero dób z udokumentowaną
dostępnością. Lokalnie przeszły 134 testy, 25 PostgreSQL pominięto.

Audyt CSV porównuje etykietę godziny z okresem UTC dla dób 24-godzinnych.
Test wykrywa zamianę okresów pomimo kompletu 24 odrębnych kluczy.
Lokalnie przeszło 135 testów, a 25 testów PostgreSQL pominięto.
Audyt rozróżnia komplet okresów UTC od błędnego przypisania godzin.
Test potwierdza osobną diagnozę bez dopuszczenia błędnych par.
Lokalnie przeszło 135 testów, a 25 testów PostgreSQL pominięto.

Audyt CSV podaje listy brakujących, zduplikowanych i nadmiarowych okresów
UTC. Test sprawdza oba dni zmiany czasu, bez wnioskowania o mapowaniu
etykiet TGE. Lokalnie przeszło 136 testów, 25 PostgreSQL pominięto.

Audyt odrzuca zduplikowane nagłówki CSV przed odczytem wierszy.
Test potwierdza, że druga kolumna JWCD nie nadpisuje pierwszej.
Lokalnie przeszło 137 testów, 25 PostgreSQL pominięto.

Audyt CSV odrzuca wiersze z nadmiarem lub niedoborem pól względem
nagłówka. Jawnie puste komórki nadal podlegają audytowi jakości.
Lokalnie przeszło 138 testów, 25 PostgreSQL pominięto.

Audyt używa ścisłego odczytu CSV. Test potwierdza odrzucenie
niedomkniętego cudzysłowu. Lokalnie przeszło 139 testów,
25 testów PostgreSQL pominięto.

Audyt CSV sprawdza zgodność wypełnionych kolumn cen Fix1. Test obejmuje
równoważne zapisy liczbowe, pustą kolumnę alternatywną oraz konflikt.
Lokalnie przeszło 140 testów, 25 PostgreSQL pominięto.

Audyt CSV podaje okresy UTC z konfliktem między kolumnami cen Fix1,
jeżeli okres da się odczytać. Test potwierdza wskazanie właściwej godziny.
Lokalnie przeszło 140 testów, 25 PostgreSQL pominięto.

Test rankingu dla doby z samymi cenami zerowymi potwierdza, że wszystkie
modele zachowują MAE i bias, a MAPE i WAPE pozostają niezdefiniowane.
Lokalnie przeszło 141 testów, 25 PostgreSQL pominięto.

Test rankingu potwierdza, że identyczne MAE daje wspólną pozycję
i że po remisie na pierwszym miejscu następny model zajmuje trzecie.
Lokalnie przeszły 142 testy, a 25 testów PostgreSQL pominięto.

Raport walk-forward podaje liczbę okresów w każdej przyjętej dobie
i we wspólnej próbie. Test potwierdza 49 okresów dla dób 24 i 25 h.
Lokalnie przeszły 142 testy, a 25 testów PostgreSQL pominięto.

Raport walk-forward grupuje pominięte doby według etapu odrzucenia.
Test rozróżnia brak snapshotu JWCD i brak danych dnia docelowego.
Lokalnie przeszły 143 testy, a 25 testów PostgreSQL pominięto.

Brak pola snapshotów JWCD skutkuje pominięciem odpowiedniej doby
w walk-forward. Testy obejmują brak danych docelowych i treningowych.
Lokalnie przeszły 144 testy, 25 testów PostgreSQL pominięto.

Selekcja JWCD odrzuca pole snapshotów o błędnym typie. Test potwierdza,
że uszkodzona doba trafia do raportu, a poprawna pozostaje w rankingu.
Lokalnie przeszło 145 testów, 25 testów PostgreSQL pominięto.

Brak pola cen Fix1 w danych docelowych lub treningowych pomija
dotkniętą dobę w walk-forward. Dwa testy potwierdzają diagnostykę.
Lokalnie przeszło 147 testów, 25 PostgreSQL pominięto.

Walidacja cen odróżnia błędną kolekcję od błędnego wiersza. Test
walk-forward potwierdza pominięcie wadliwej doby i ocenę poprawnej.
Lokalnie przeszło 148 testów, 25 PostgreSQL pominięto.

Raport walk-forward wskazuje datę doby treningowej z brakującym JWCD,
brakującymi lub spóźnionymi cenami Fix1 oraz pierwszy brak w oknie.
Testy potwierdzają wskazanie daty dla obu brakujących zestawów.
Lokalnie przeszło 148 testów, 25 PostgreSQL pominięto.

Raport walk-forward dołącza przyczyny odrzucenia snapshotów JWCD do daty
doby treningowej. Test obejmuje zakończenie pobrania po granicy 09:50.
Lokalnie przeszło 149 testów, 25 PostgreSQL pominięto.

Scoring dobowy i ranking walk-forward używają tych samych zwalidowanych
cen Fix1. Test z jednorazowym iteratorem daje wyniki identyczne z listą.
Lokalnie przeszło 150 testów, 25 PostgreSQL pominięto.

Walk-forward zachowuje snapshoty JWCD przekazane jako jednorazowy iterator
do wielokrotnego odczytu przez selekcję, trening i scoring. Test dla dwóch
dób porównuje cały raport wszystkich sześciu modeli z wynikiem dla list.
Lokalnie przeszło 151 testów, 25 PostgreSQL pominięto.

### Wejście CSV do walk-forward SHADOW

`python shadow_walkforward_csv.py --evaluated-at 2026-11-05T07:00:00Z historia.csv`
uruchamia audyt i zwraca JSON z gotowością poszczególnych dób oraz rankingiem
na wspólnej próbie. CSV musi zawierać ceny Fix1, JWCD, wersję i czas dostępności
Fix1 oraz identyfikator snapshotu JWCD i czasy publikacji, rozpoczęcia i
zakończenia pobrania. Na dobach DST wymagane są jawne okresy UTC. Audyt
sprawdza spójność deklarowanych metadanych, a ich pochodzenie wymaga dowodu
ze źródła. Runner ponownie sprawdza dostępność przy granicy D-1 09:50.

Test syntetyczny przeszedł od 91 dób CSV do rankingu sześciu modeli dla doby
25h. Spóźnione pobranie w oknie 90D wykluczyło ranking. Osobny przebieg CLI
na ośmiu rzeczywistych referencjach TGE (27.10–03.11.2026) potwierdził 192
poprawne ceny godzinowe, zero dób gotowych do porównania z powodu braku
danych i metadanych JWCD oraz pusty ranking. Lokalnie przeszły 153 testy,
25 integracyjnych testów PostgreSQL pominięto.

### Audyt paczki ZIP PK5/Fix1

`python shadow_history_bundle.py historia.zip` sprawdza SHA-256 CSV i XLSB
względem manifestu oraz liczbę wierszy, liczbę dób i skrajne daty CSV. Następnie
uruchamia audyt godzin UTC i dostępności danych. Nie rozpakowuje plików, a
wynik zawiera wyłącznie agregaty i listy dat kwalifikujących się do porównania.
Sumy kontrolne dowodzą zgodności zawartości z manifestem. Pochodzenie danych
oraz zgodność komórek XLSB z CSV wymagają odrębnej weryfikacji.

Przebieg na rzeczywistej paczce potwierdził 50 928 par JWCD/Fix1 w 2 122
kompletnych dobach 24h od 01.01.2021 do 04.11.2026. CSV pomija sześć dób 23h
i sześć dób 25h. Brak metadanych publikacji i pobrania JWCD oraz weryfikacji,
wersji i czasu dostępności Fix1 oznacza zero kwalifikujących się dób dla 30D,
60D i 90D. Testy sprawdzają odczyt bez zapisów, wykrywanie brakującej doby
25h, odrzucenie zmienionego CSV i rozbieżności z manifestem. Lokalnie przeszło
155 testów, 25 integracyjnych testów PostgreSQL pominięto.

Opcja `--reference plik1.csv plik2.csv` w audycie ZIP porównuje kompletne
ceny Fix1 okres po okresie w UTC. Raport rozdziela rozbieżne okresy oraz doby
brakujące lub niepoprawne w historii. Odrzuca niekompletną lub niejednoznaczną
referencję. Na rzeczywistej paczce ceny za 27.10–03.11.2026 zgodziły się
z referencjami TGE we wszystkich 192 okresach (8 dób, zero różnic).
Zgodność wartości nie ustala historycznych czasów publikacji lub pobrania
i nie odblokowuje rankingu. Test regresji wykrył zmienioną cenę, brak doby
i powielony okres referencyjny. Lokalnie przeszło 156 testów, a 25
integracyjnych testów PostgreSQL pominięto.

Audyt SQL w trybie read-only pokazuje dla raw.snapshots czas zapisu i wersję
źródła, o ile kolumny istnieją. Wskazuje daty z potencjalnym dowodem zakończenia
pobrania: energy-collector-v3 otwiera transakcję zapisu po zakończeniu pobierania.
Kandydat wymaga startu i zapisu do D-1 09:50 oraz jawnych znaczników publikacji
we wszystkich wierszach, najpóźniej w chwili zapisu. Pole created_at jest
wskazówką wymagającą weryfikacji pochodzenia rekordu (np. brak późnego importu
z historycznym timestampem). Nie zastępuje tej weryfikacji ani mapowania godzin
UTC. Test PostgreSQL obejmuje zapis na granicy, po granicy, przed startem,
obcą wersję i brak czasu publikacji. Kod sprawdzono bez połączenia z produkcją.

Audyt read-only PK5 pokazuje pokrycie jawnych `period_start_utc` dla dób
23/24/25h, w tym brakujące, nadmiarowe i powtórzone okresy. Oddziela kandydatów
czasowych od snapshotów z kompletem godzin UTC i skończonymi wartościami JWCD.
Etykiety legacy nie służą do odtwarzania brakującego UTC. Testy syntetyczne
obejmują obie zmiany czasu, podmianę okresu na duplikat i znaczniki bez strefy.
Test SQL potwierdza, że sam poprawny czas archiwizacji nie wystarcza do
zakwalifikowania snapshotu jako kompletnego. Weryfikacja pochodzenia danych
pozostaje oddzielnym warunkiem dopuszczenia do rankingu.

Audyt SQL stosuje tę samą regułę wyboru co SHADOW do kompletnych kandydatów
PK5. W raporcie wskazuje wstępny identyfikator lub konflikt, gdy snapshoty
z tym samym najpóźniejszym czasem zapisu i rozpoczęcia pobrania różnią się
wartościami JWCD. Równe wartości po normalizacji UTC i liczb oraz starsze
wersje nie blokują wyboru. W teście na izolowanym PostgreSQL snapshot po
09:50 nie wpływa na wybór. Jest to diagnostyka oparta na czasie zapisu.
Pochodzenie rekordu nadal wymaga weryfikacji przed dopuszczeniem do rankingu.

Selekcja SHADOW i audyt SQL zgłaszają teraz CONFLICTING_JWCD_PUBLICATION,
gdy najnowszy dostępny kandydat i inny kompletny snapshot przypisują różne
wartości JWCD temu samemu okresowi UTC i znacznikowi publikacji. Późniejsze
pobranie tej samej publikacji nie rozstrzyga konfliktu. Odrębna publikacja
pozostaje dopuszczalną rewizją, a dane po D-1 09:50 są wykluczone z porównania.
Testy obejmują te przypadki oraz raport odrzuceń w izolowanym PostgreSQL.

Selekcja SHADOW odrzuca jako SUPERSEDED_JWCD_PUBLICATIONS snapshot, gdy
inny kompletny snapshot dostępny do D-1 09:50 ma równie nowe publikacje dla
wszystkich godzin UTC i nowszą publikację dla co najmniej jednej godziny.
Chroni to przed przywróceniem starszej wersji po jej ponownym pobraniu.
Jeśli oba snapshoty mają nowsze publikacje dla różnych godzin, nadal
decyduje czas pobrania, a model korzysta z jednego pełnego snapshotu.
Regułę sprawdzono na danych syntetycznych, także w audycie PostgreSQL.

Audyt PostgreSQL przyjmuje opcjonalne granice daty dostawy `--start-day`
i `--end-day`. Filtry trafiają do obu zapytań SQL przed pobraniem danych PK5
i agregacją Fix1. Daty graniczne są włączone, a raport zapisuje użyty zakres.
Test na izolowanej bazie sprawdza dobę zmiany czasu, zakres jednodniowy,
filtry jednostronne, pełny odczyt i odrzucenie odwróconych granic.

Raport SQL zawiera `delivery_coverage` dla każdej daty obecnej w co najmniej
jednym źródle. Pokazuje brakujące PK5 lub Fix1, brak kompletnego kandydata JWCD,
odrzucenie kandydatów, wstępny wybór oraz niekompletne lub strukturalnie kompletne
ceny Fix1. Statusy diagnostyczne nie potwierdzają pochodzenia danych ani ich
gotowości do rankingu. Test SQL obejmuje dni z tylko jednym źródłem, dobę 25h
z niekompletnym Fix1 i zmianę statusu PK5 po wykryciu konfliktu.

Raport SQL podaje `missing_delivery_ranges`, czyli przedziały dat dostawy
nieobecnych zarówno w PK5, jak i Fix1. Uwzględnia wskazane granice włącznie.
Brakującą granicę wyznacza pierwsza lub ostatnia zaobserwowana data.
Gdy oba źródła są puste, cały przedział zostanie wskazany, jeśli podano obie
granice. Test PostgreSQL obejmuje pusty przedział oraz luki na początku,
w środku i na końcu zakresu. Raport nie tworzy sztucznych rekordów dziennych.

Audyt Fix1 rozdziela komplet godzin od kompletu prawidłowych cen. Wskazuje
liczbę brakujących cen oraz wartości NaN i ±Infinity oraz status INVALID_PRICES,
jeśli doba z kompletem godzin ma niepoprawną cenę. Zera i ceny ujemne są
akceptowane. Test na izolowanym PostgreSQL obejmuje wszystkie te przypadki.
Poprawność techniczna cen nadal nie potwierdza ich pochodzenia.

Audyt SQL Fix1 rozdziela najpóźniejsze czasy pierwszego zapisu i pobrania
aktualnie zapisanych cen. Liczy wiersze bez metadanych czasu, z odwróconą
kolejnością znaczników oraz z ponownym pobraniem. Późniejsze pobranie
może nadpisać cenę w archiwum legacy. Samo przesunięcie znacznika nie
dowodzi jednak zmiany ceny, a pierwszy zapis nie ustala dostępności
aktualnej wartości. Test obejmuje obie wersje schematu na izolowanym PostgreSQL.

Audyt Fix1 liczy zapisane pary adresu źródła i odcisku SHA-256 oraz wiersze
bez użytecznych metadanych. Wskazuje, czy komplet godzin ma jedną parę, bez
ujawniania adresów ani odcisków. To kontrola zapisanych metadanych. Zgodność
z zawartością źródła nadal wymaga odrębnego sprawdzenia. Test na izolowanym
PostgreSQL obejmuje schemat legacy, jednolite i mieszane metadane oraz
nieprawidłowy odcisk.

Audyt Fix1 pokazuje, czy najpóźniejsze pobranie aktualnie zapisanej ceny
mieści się po granicy 09:50 dla prognozy ocenianej doby i do granicy
09:50 dla prognozy kolejnej doby. Braki lub błędna kolejność znaczników
oraz niekompletne godziny lub ceny wykluczają kandydata czasowego. Test
PostgreSQL sprawdza obie granice, brak znacznika i jesienną zmianę czasu.
Wynik jest diagnostyką czasu pobrania, a weryfikacja pochodzenia cen
i mapowania godzin nadal warunkuje ich użycie w walk-forward.

Audyt SQL wylicza wstępne pokrycie wspólnych danych PK5/Fix1: liczbę
sparowanych dób, najdłuższą serię i możliwe daty porównania 30/60/90D.
Wymaga wstępnego wyboru JWCD, jednego zapisanego źródła kompletu Fix1
oraz spójnego czasu pobrania cen po granicy własnej prognozy. Każda doba
treningowa musi być dostępna do D-1 09:50 ocenianej prognozy. Test na 91
syntetycznych dobach sprawdza kompletne okna, spóźnioną cenę i lukę.
Pokrycie jest diagnostyką, a dopuszczenie danych nadal wymaga odrębnej
weryfikacji źródeł i mapowania godzin.

Audyt PostgreSQL przyjmuje `--reference plik.csv` i porównuje ceny Fix1
z niezależną referencją godzinowo, wewnątrz tej samej transakcji tylko do
odczytu. SQL pobiera ceny wyłącznie dla dat z referencji. Doby 23/25h z
samymi numerami godzin nie mają wystarczającego mapowania UTC do porównania.
Test w izolowanym PostgreSQL używa zapisanej referencji TGE z 03.11.2026,
porównuje 24 ceny i wykrywa zmienioną godzinę. Wynik porównania pozostaje
oddzielony od kwalifikacji cen do walk-forward i nie zmienia archiwum.

Jeśli podano referencje TGE, audyt wyklucza ze wstępnego pokrycia 30/60/90D
doby z różnicą ceny albo bez kompletu porównywalnych godzin. Wykluczona
doba nie może być celem ani częścią okna treningowego. Zgodność referencji
nie nadaje danym statusu zweryfikowanego. Doby bez podanej referencji nadal
podlegają dotychczasowym kryteriom wstępnego audytu. Testy obejmują lukę
w oknie oraz zmianę pojedynczej ceny z referencji 03.11.2026 w izolowanej bazie.

Gdy audyt otrzyma referencje TGE, raport pokazuje również
`reference_matched_comparison_coverage`: daty dla okien 30/60/90D, w których
cel i każda doba treningowa mają pełną zgodność godzinową Fix1 z referencją.
Testy pokazują, że brak jednej referencji przerywa okno, a wybrane krótsze
okna pozostają dostępne. Jest to pokrycie diagnostyczne po kontroli czasu
pobrania. Nie nadaje ono danym statusu dopuszczającego je do rankingu.

Porównanie SQL z referencją uwzględnia kolumnę `period_start_utc`, gdy
istnieje w archiwum Fix1. Pozwala porównać doby 23/25h z jawnymi godzinami
UTC po obu stronach i rozróżnić powtórzoną godzinę lokalną. Brak lub
duplikat okresu UTC w takiej dobie wyklucza porównanie. Test na izolowanym
PostgreSQL porównuje syntetyczne doby 23h i 25h. Ścieżka nie dodaje kolumn
do archiwum ani nie zmienia zasad dopuszczenia do rankingu.

Audyt kompletności Fix1 wykorzystuje jawne okresy UTC, gdy doba zawiera
przynajmniej jeden taki znacznik. Wymaga wtedy kompletu poprawnych okresów.
Pozwala odróżnić powtórzoną godzinę lokalną w dobie 25h od duplikatu UTC.
W zwykłej dobie sprawdza także zgodność podanych numerów godzin z UTC.
Dla danych bez znaczników zachowuje osobno oznaczoną diagnostykę legacy.
Test PostgreSQL potwierdza spójność z porównaniem referencji dla dób 23/25h.

Audyt CSV i porównanie z referencją stosują tę samą regułę co audyt SQL:
gdy doba ma jawny okres UTC, każda godzina tej doby musi mieć taki znacznik.
Dla doby 24h w całości zapisanej jako H01–H24 dotychczasowa ścieżka nadal
działa. Testy sprawdzają częściową dobową mapę UTC oraz odrzucenie jej przy
porównaniu z referencją TGE na izolowanym PostgreSQL.

Wstępne okna porównań SQL wymagają jawnej i kompletnej mapy UTC Fix1 dla
dób 23/25h. Sam komplet numerów legacy wystarcza w tej diagnostyce tylko
dla doby 24h. Test regresji sprawdza obie zmiany czasu oraz zwykłą dobę.

Raport SHADOW zapisuje posortowane klucze UTC wspólnej próby dla każdej
ocenionej doby. Test jesiennej zmiany czasu porównuje te klucze z wyjściem
każdego modelu. Test wiosennej zmiany czasu sprawdza 47 godzin w dwóch
dobach i wyklucza dobę 23h z rankingu wszystkich modeli po podmianie jednego
klucza UTC w prognozie jednego modelu. Testy używają danych syntetycznych.

Pierwszy przebieg testu 23h wykazał, że wymóg identycznego zbioru godzin
lokalnych wykluczał zwykłe doby z sąsiedztwa wiosennej doby DST. SHADOW
porównuje teraz profile po godzinach doby docelowej, zarówno dla odległości
poziomów, jak i średniej używanej do centrowania kształtu. Sąsiad nadal musi
mieć każdą wymaganą godzinę. Zmieniono wersje SHADOW dni podobnych i mediany.

Walk-forward odczytuje jednorazowe iteratory cen Fix1 raz na dobę i używa
tych samych wartości w treningu oraz scoringu. Test porównuje pełne raporty
dla dwóch kolejnych dób, z których pierwsza wchodzi do treningu drugiej.
Wyniki dla iteratorów i list są identyczne. Kontrola dostępności ceny
względem granicy D-1 09:50 działa także przy ponownym użyciu danych.

Raport walk-forward zachowuje wersje danych treningowych oraz dodaje czasy
dla każdego użytego snapshotu JWCD i zestawu cen Fix1. Pokazuje publikację,
początek i koniec pobrania, granicę JWCD dla własnej doby i dostępność cen
dla trenowanego modelu. Test syntetyczny potwierdza wybór wcześniejszego
snapshotu mimo spóźnionej rewizji i dopuszczenie cen dostępnych dokładnie
na granicy treningu. Mediana nadal odsyła do raportów modeli składowych.

Scoring i raport SHADOW korzystają ze znaczników czasu zwróconych razem
z wybranym snapshotem docelowym przez selekcję as-of. Test potwierdza, że
spóźniona rewizja nie zmienia czasów przekazanych do scoringu żadnego
z sześciu modeli ani czasów zapisanych w raporcie.

Scoring SHADOW sprawdza najpóźniejsze czasy publikacji i zakończenia pobrania
JWCD z wejścia docelowego i treningu danego modelu. Mediana dziedziczy
maksima swoich czterech modeli składowych. Raport zachowuje czasy snapshotu
docelowego oddzielnie od czasów przekazanych do scoringu. Test z wcześnie
pobranym snapshotem docelowym potwierdza użycie późniejszych czasów danych
treningowych, nadal mieszczących się przed granicą D-1 09:50.

Raport odrzuceń SHADOW zapisuje nazwę modelu, jeśli błąd wystąpi przy
sprawdzaniu czasu wejść lub scoringu. Nadal wyklucza całą dobę ze wspólnej
próby. Testy wskazują wariant dni podobnych z błędną godziną UTC oraz
OLS 60D z czasem pobrania treningowego o sekundę po D-1 09:50.

Scoring dobowy i ranking walk-forward sumują wartości w stałej kolejności
okresów UTC. Przed poprawką odwrócenie kolejności wierszy cen zmieniało
końcowe cyfry metryk przez zaokrąglenia Decimal. Testy potwierdzają
identyczny WAPE dobowy i identyczny pełny raport dla dwóch syntetycznych
dób niezależnie od kolejności wierszy Fix1.

Izolowany zapis scoringu normalizuje zapis dziesiętny cen i metryk bez
zaokrąglania. Równoważne wartości, w tym zera ze znakiem i końcowe zera,
nie tworzą konfliktu przy ponownym zapisie nowych wyników. Wyniki zapisane
wcześniej z odciskiem zależnym od formatu nadal obsługują powtórkę oryginalnych
danych. Równoważnego zapisu w innym formacie nie da się potwierdzić na
podstawie samego starego odcisku. Testy sprawdzają oba warianty na izolowanej
bazie oraz zachowanie istniejącego wiersza.

Izolowany scoring rozróżnia konflikt odcisku wejść od niezgodności metryk
przy zgodnym odcisku. Test PostgreSQL symuluje zmianę zapisanej wartości
MAE i potwierdza, że ponowny scoring zgłasza przyczynę konfliktu oraz
zachowuje wiersz. Oddzielny test sprawdza komunikat przy zmianie prognozy.

## Checkpoint 2026-11-06 — trzy dodatkowe sesje Fixing I

Pobrano trzy odpowiedzi HTTP 200 z oficjalnej tabeli RDN TGE dla dostaw
04–06.11.2026. Test porównuje wszystkie 72 ceny godzinowe z CSV uzyskanym
osobną ekstrakcją lxml z tego samego HTML. Sprawdza też sumy SHA-256,
powiązanie daty sesji z dostawą i dopuszczenie trzech kompletnych paczek
przez walidator archiwizacji. Pliki gzip zachowują bajty odpowiedzi HTTP.
Pobranie historyczne nie dokumentuje dostępności danych wejściowych
przed D-1 09:50 i nie jest dowodem gotowości do backtestu.

Test integracyjny zapisuje w izolowanym PostgreSQL trzy paczki TGE dla
dostaw 04–06.11.2026 i ponawia każdą z nich. Sprawdza 72 ceny z kluczami UTC,
trzy oryginalne odpowiedzi HTTP wraz z sumami SHA-256 i czasami pobrania
oraz brak wpisów w kwarantannie po identycznym ponowieniu.

Test kwarantanny symuluje rewizję rzeczywistej sesji dla dostawy
04.11.2026 przez zmianę ceny H01 o 0,01 PLN/MWh w kopii HTML i CSV.
Izolowany PostgreSQL zachowuje oryginalne 24 ceny i ich powiązanie
z oryginalnym HTML. Zapisuje dowody zmienionej paczki w jednym wpisie
kwarantanny także po jej ponowieniu.

Test scoringu na izolowanym PostgreSQL używa 24 rzeczywistych cen Fixing I
dla dostawy 04.11.2026 i syntetycznej prognozy stałej 1000 PLN/MWh.
Sprawdza zapis MAE, MAPE, WAPE i bias względem wartości obliczonych
niezależnie z referencji CSV, powiązanie wyniku z HTML TGE oraz ponowienie
bez duplikatu. Syntetyczny czas pobrania na granicy D-1 09:50 przechodzi,
a sekundę później blokuje zapis. Test nie ocenia trafności modelu.

Audyt porównania z TGE rozdziela braki w zakresie historii od dat poza
jego granicami. Test obejmuje brakującą dobę 25h wewnątrz zakresu oraz
dzień po jego końcu. Ponowny audyt pełnej kopii przekazanego ZIP potwierdził
24 z 24 zgodnych cen dla dostawy 04.11.2026. Dostawy 05–06.11.2026
wypadają poza zakresem pliku, który kończy się 04.11.2026.
Wynik potwierdza ceny, a liczba dób z udokumentowaną dostępnością do
backtestu pozostaje równa zero.

Audyt CSV i ZIP liczy brakujące znaczniki czasu osobno dla publikacji
JWCD, początku i końca pobrania JWCD oraz dostępności cen Fix1.
Raport CSV pokazuje także liczniki dobowe. Test odróżnia brak znacznika
od nieprawidłowego formatu i przekroczenia granicy D-1 09:50.
Audyt pełnego ZIP wykazał brak każdego z czterech znaczników we wszystkich
50 928 wierszach. Liczba dób gotowych do backtestu pozostaje równa zero.

Audyt CSV, ZIP i wstępny audyt SQL podają dla okien 30D, 60D i 90D
doby docelowe z kompletem danych, które odpadły przez zbyt późną
dostępność cen Fix1 z treningu. Przy każdej podają daty spóźnionych
cen. Testy obejmują cenę dostępną dokładnie o D-1 09:50, sekundę
później oraz okno z luką w historii.

Raport walk-forward z CSV pokazuje osobno liczbę dni źródłowych i dni
dopuszczonych oraz daty i przyczyny odrzuceń na etapie audytu.
Test potwierdza rozdzielenie odrzuceń audytu od pominięć w samym
walk-forward. Na przekazanym ZIP wykluczono w audycie 2122 z 2122
dób i nie utworzono rankingu.

CLI walk-forward z CSV przyjmuje `--target-from` i `--target-to` jako
włączne granice dat dostawy do oceny. Wcześniejsze doby nadal wchodzą
do treningu, jeśli spełniają warunki dostępności dla danej prognozy.
Test z 90 dobami treningowymi i docelową dobą 25h potwierdza identyczny
ranking sześciu modeli po ograniczeniu oceny do tej jednej doby.

Raport walk-forward z CSV podaje zwarte przedziały brakujących dat
dostawy w zakresie oceny. Daty obecne w CSV, które odrzucił audyt,
pozostają w wykazie odrzuceń. Testy obejmują rzeczywiste referencje TGE
przycięte do zakresu oceny oraz pusty zbiór z zadanym zakresem.

Ranking SHADOW podaje liczbę wspólnie ocenionych dób 23h, 24h i 25h.
W jednym przebiegu na danych syntetycznych sześć modeli oceniono na
tych samych 72 godzinach z trzech dób różnej długości. Test z błędnym
kluczem UTC potwierdza, że odrzucona doba nie zwiększa żadnego licznika.

Raport rankingu deklaruje MAE jako kryterium kolejności. Test dwóch
syntetycznych dób 24h i 25h porównuje MAE, MAPE, WAPE i bias wszystkich
sześciu modeli z niezależnym obliczeniem na ułamkach dokładnych. Próba
obejmuje 49 godzin, w tym ceny ujemne i dwa zera pomijane tylko w MAPE.

Walk-forward SHADOW odczytuje zawartość paczek tylko dla zadanych dób
oceny i ich maksymalnie 90-dniowych okien treningowych. Test podstawia
nieczytelne iteratory dla przyszłej doby i doby sprzed 91 dni.
Raport, prognozy i ranking sześciu modeli dla wcześniejszej doby
pozostają identyczne.

Błąd odczytu iteratora cen lub snapshotów wyklucza całą wadliwą paczkę
z walk-forward. Raport wskazuje jej datę i pole, a doby bez zależności
od tej paczki nadal podlegają ocenie. Test obejmuje błąd po odczytaniu
pierwszego elementu w danych doby docelowej i w danych treningowych.

Selekcja JWCD odrzuca snapshot, którego iterator wierszy zgłasza
błąd odczytu. Test potwierdza wybór kompletnego wcześniejszego
snapshotu i odrzucenie uszkodzonego bez wykorzystania jego części.
Drugi test potwierdza odrzucenie spóźnionego snapshotu jeszcze przed
odczytem wierszy.

Walk-forward zachowuje wynik pierwszego odczytu iteratora wierszy
snapshotu JWCD na potrzeby kolejnych modeli i dób. Test porównuje
całe raporty dla list i iteratorów. Drugi potwierdza, że uszkodzony
snapshot nie wpływa na ranking przy kompletnej wersji zastępczej,
a snapshot po D-1 09:50 pozostaje nieodczytany.

Dla sobotniej dostawy 07.11.2026 zapisano osobno odpowiedź HTTP 200
z sesji TGE 06.11.2026. Test porównuje wszystkie 24 ceny Fixing I
z niezależną ekstrakcją lxml z tego samego HTML, weryfikuje oba SHA-256
i potwierdza dopuszczenie paczki przez walidator archiwizacji.

Test integracyjny uruchamia cztery równoczesne importy paczki TGE
dla dostawy 07.11.2026 na osobnych połączeniach z izolowanym PostgreSQL.
Sprawdza jeden zapis, trzy ponowienia, 24 ceny przypisane do jednej
paczki dowodowej i pustą kwarantannę.

Importer manifestu SHADOW obsługuje HTML zapisany jako gzip. Odczyt
wymaga podanej w manifeście długości po rozpakowaniu i ogranicza ją
do 8 MiB. SHA-256 nadal dotyczy oryginalnej odpowiedzi HTML. Suchy
przebieg oficjalnej sesji dla dostawy 07.11.2026 potwierdza 24 ceny.
Testy odrzucają ucięty gzip, błędną długość i błędny SHA-256 przed
inicjalizacją bazy.

Importer manifestu stosuje ścisły odczyt referencyjnego CSV.
Test na kopii sesji 07.11.2026 odrzuca powielony nagłówek ceny,
nadmiarowe lub brakujące pole, niedomknięty cudzysłów i brak
wymaganego nagłówka. Błędy blokują zapis przed inicjalizacją
bazy również wtedy, gdy SHA-256 pliku zgadza się z manifestem.

Zapis wszystkich sesji manifestu SHADOW ma wspólną transakcję.
Blokady dni są zakładane w kolejności dat dostawy. Test na siedmiu
rzeczywistych sesjach wymusza błąd PostgreSQL przy drugiej dobie,
potwierdza brak zapisanych paczek, cen i wpisów kwarantanny, a po
usunięciu wymuszonego błędu sprawdza zapis wszystkich 168 cen.

Raport importu manifestu rozdziela liczbę sesji według wyniku oraz
liczbę faktycznie dopisanych godzin od liczby godzin sprawdzonych.
Test na siedmiu sesjach TGE obejmuje w jednej transakcji pięć zapisów,
jedno ponowienie i jedną kwarantannę. Powtórka nie dopisuje cen ani
kolejnego wpisu kwarantanny, a konfliktowa cena pozostaje bez zmian.

Scoring w izolowanym PostgreSQL sprawdza również format SHA-256
referencji i obsługiwaną wersję parsera przed zapisem lub ponowieniem
wyniku. Test modyfikuje w bazie testowej HTML, odcisk referencji
i wersję parsera po zapisie wyniku. Każdy przypadek blokuje ponowienie,
a zapisany wynik pozostaje bez zmian. Odcisk referencji w bazie
poświadcza identyfikator pliku. Ponowne sprawdzenie jego treści wymaga CSV.

Scoring SHADOW przyjmuje opcjonalnie oryginalne bajty CSV referencji.
Przed zapisem sprawdza SHA-256 z archiwum oraz zgodność wszystkich
cen CSV z zachowanym HTML i wierszami bazy. Test rzeczywistej sesji
odrzuca zmienioną cenę H01 zarówno przy starym, jak i podmienionym
odcisku referencji. Tryb bez CSV pozostaje dostępny dla starszych wywołań.

Scoring w izolowanym PostgreSQL odrzuca archiwalny odczyt Fix1 z czasem
pobrania wcześniejszym niż wydanie prognozy lub równym mu. Test obejmuje
sekundę przed, dokładny czas wydania i sekundę po nim oraz zachowanie
wcześniej zapisanego wyniku przy odrzuconym ponowieniu. Czas odczytu
archiwalnego nie określa czasu publikacji ceny przez TGE.

Scoring SHADOW może przyjąć jawny czas oceny. Odczyt Fix1 dokonany
później blokuje zapis i ponowienie. Test obejmuje sekundę przed
odczytem, dokładną chwilę zapisaną z innym przesunięciem UTC i sekundę
po odczycie. Czas oceny ogranicza dostępność odczytu. Kontrola
kwarantanny nadal uwzględnia bieżący stan bazy.

Dla niedzielnej dostawy 08.11.2026 zapisano osobną odpowiedź HTTP 200
z sesji TGE 07.11.2026, CSV i manifest. Test porównuje wszystkie 24 ceny
Fixing I z osobną ekstrakcją lxml z tego samego HTML i weryfikuje oba
SHA-256. Importer potwierdził komplet cen w suchym przebiegu.

Importer sprawdza, czy `session_date` w manifeście przypada dzień
przed datą dostawy. Test na kopii oficjalnej sesji dla 08.11.2026
potwierdza odrzucenie brakującej lub błędnej daty przed inicjalizacją
bazy, mimo prawidłowego URL i niezmienionych plików HTML oraz CSV.

Importer wymaga, aby pole `hours` było liczbą całkowitą zgodną z liczbą
zweryfikowanych okresów Fixing I. Test regresji na oficjalnej sesji dla
08.11.2026 potwierdza, że brak pola, błędna liczba godzin i niewłaściwy
typ wartości zatrzymują import przed inicjalizacją bazy.

Importer manifestu wymaga historycznego adresu TGE z parametrem
`dateShow` zgodnym z sesją. Test na oficjalnej dostawie 08.11.2026
potwierdza 24 ceny dla poprawnego adresu oraz odrzucenie braku adresu,
adresu bieżącej strony, innej daty i powtórzonego parametru przed
inicjalizacją bazy. Bezpośrednia walidacja paczek nadal dopuszcza
adres bieżącej strony w dotychczasowych wywołaniach.

Selekcja JWCD zgłasza `CONFLICTING_LATEST_SNAPSHOTS`, gdy kompletne,
nierozstrzygnięte wersje mają te same czasy rozpoczęcia i zakończenia
pobrania przed D-1 09:50, lecz różne znaczniki publikacji w godzinach
UTC. Test używa identycznych wartości MW i potwierdza, że kolejność
wejściowa nie rozstrzyga sprzecznych metadanych. Reguła wyboru przy
różnych czasach pobrania pozostaje taka sama.

Adapter zapisu SHADOW sprawdza rzeczywisty adres IP połączenia PostgreSQL
przed wykonaniem SQL. Przy otwieraniu połączenia jawnie ustawia adres
loopback, aby `PGHOSTADDR` nie przekierował lokalnego DSN. Nazwa
`localhost` bez jawnego adresu używa IPv4. Dla IPv6 można podać `::1`.
Testy obejmują odrzucenie zdalnego `hostaddr` przed połączeniem i przed
SQL oraz połączenie z izolowanym PostgreSQL mimo wartości w środowisku.

Dla dostawy 25.10.2026 porównano wszystkie 25 etykiet i cen Fixing I
z niezależną ekstrakcją lxml. Test odtwarza porównanie z utrwalonej
odpowiedzi TGE, sprawdza jej SHA-256 i odrzuca brak H02a. Ponowny odczyt
tej samej sesji z TGE 08.11.2026 dał identyczne etykiety i ceny mimo
różnicy w bajtach HTML. Weryfikacja dotyczy kolejności etykiet TGE.
Mapowanie na UTC i zapis dób zmiany czasu pozostają zablokowane.

Tryb `--autumn-label-reference --days 1` zapisuje dla doby 25h osobny
CSV z kolumną `tge_hour_label`. Test na odpowiedzi TGE dla 25.10.2026
potwierdza osobne wartości dla H02 i H02a oraz SHA-256 CSV.
Manifest oznacza tę paczkę jako `TGE_LABEL_ONLY`. Importer odrzuca ją
przed inicjalizacją bazy, ponieważ paczka nie zawiera mapowania UTC.

Eksport referencji odróżnia brak godzin dla żądanej dostawy od
niekompletnego lub przestawionego zestawu. Test na odpowiedzi TGE
dla 02.11.2026 sprawdza poprawne 24 godziny, brak danych dla innej
dostawy i odrzucenie zestawu po usunięciu H01.

Audyt ZIP pokazuje teraz liczbę obecnych dób według długości oraz
dokładne zakresy brakujących dat. W pełnej kopii paczki z 04.11.2026
wykazał 2122 doby 24h i 12 pojedynczych luk (po sześć dób 23h i 25h).
Test obejmuje także ciągłą lukę zawierającą dobę 23h i dobę 24h.
Rozszerzone porównanie referencji TGE potwierdziło 216 zgodnych cen
z 27.10–04.11.2026. Doby 05–08.11.2026 są poza zakresem paczki.
Brak historycznych znaczników dostępności nadal wyklucza ranking.
SHA-256 paczki przed audytem i po nim był identyczny.

Audyt PostgreSQL zachowuje raport o pozostałych danych, gdy legacy PK5
nie ma kolumny `retrieved_at_utc` lub zawiera NULL. Taki snapshot otrzymuje
status `UNPROVEN_NO_VALID_RETRIEVAL_START` i odpada z selekcji przed
D-1 09:50. Test w izolowanym PostgreSQL obejmuje oba przypadki i potwierdza,
że audyt nadal zwraca dane Fix1 dla tej samej doby.

Audyt Fix1 używa do oceny dostępności tylko kolumn PostgreSQL
`timestamptz`. Starsze `timestamp` bez strefy są liczone jako brak
wiarygodnego czasu. Test na pełnej dobie 24h potwierdza, że zmiana typu
każdego z dwóch znaczników wyklucza dobę z kandydatów czasowych,
zachowując informację o komplecie cen.

Audyt Fix1 w PostgreSQL porównuje teraz okresy UTC z referencją również
w schemacie bez kolumny legacy `hour`. Test na izolowanej bazie obejmuje
72 okresy w dobach 23h, 24h i 25h z jawnymi znacznikami UTC. Audyt
nie wyprowadza mapowania UTC z samych etykiet TGE.

Audyt Fix1 w trybie tylko do odczytu traktuje wartości PostgreSQL
`infinity` i `-infinity` jako brak wiarygodnego czasu dostępności.
Test na izolowanej bazie zachowuje komplet 24 cen i najpóźniejsze
poprawne znaczniki, lecz wyklucza dobę z kwalifikacji czasowej.

Walk-forward SHADOW raportuje przy scoringu każdego modelu najpóźniejszy
czas dostępności cen Fix1 wykorzystanych w treningu. Dla mediany stosuje
najpóźniejszy czas spośród modeli składowych. Dodatkowa kontrola scoringu
usuwa dobę ze wspólnego rankingu, jeśli choć jeden model zgłosi ceny
treningowe dostępne po D-1 09:50. Testy obejmują dokładny cutoff i sekundę
po nim na syntetycznej historii z udokumentowanymi znacznikami.

Walk-forward sprawdza dostępność Fix1 przed odczytaniem iteratorów
danej paczki. Test potwierdza, że ceny i snapshoty doby dostępnej
sekundę po czasie oceny pozostają nieodczytane, a wcześniejsza doba
zachowuje identyczne prognozy, scoring i ranking.

Walk-forward odrzuca metadane Fix1 datowane na czas wydania prognozy
lub wcześniej przed odczytem cen i snapshotów. Test z syntetyczną
paczką potwierdza brak odczytu zarówno przy ocenie przed D-1 09:50,
jak i po tym czasie.

Po pojawieniu się cen dla dostawy 09.11.2026 zapisano oficjalny HTML
sesji TGE z 08.11.2026, referencję CSV i manifest. Test porównuje
wszystkie 24 ceny Fixing I z osobną ekstrakcją lxml z tego samego HTML,
sprawdza oba SHA-256 i potwierdza komplet w importerze w trybie bez
zapisu. Manifest zawiera czas pobrania, bez ustalania czasu publikacji.

Test integracyjny importuje manifest rzeczywistej sesji dla dostawy
09.11.2026 do izolowanego PostgreSQL. Pierwszy import zapisuje 24 ceny,
ponowienie dopisuje zero. Oba przebiegi sprawdzają ceny i okresy UTC
względem CSV, jeden zachowany komplet dowodów i pustą kwarantannę.

Test scoringu po imporcie manifestu 09.11.2026 używa oficjalnych
24 cen oraz syntetycznej prognozy 1500 PLN/MWh. Sprawdza MAE, MAPE,
WAPE i bias względem obliczeń na ułamkach z CSV. Sekundę przed
pobraniem TGE wynik nie powstaje. W chwili pobrania wynik zapisuje
się raz, a powtórzenie zwraca ten sam wynik.

Scoring SHADOW bez referencji CSV sprawdza teraz także zapisany adres
źródła. Test na izolowanym PostgreSQL potwierdza odrzucenie obcej domeny
i niewłaściwej daty sesji przy ponowieniu oceny. Zapisany wynik pozostaje
nienaruszony. Poprawny adres jest warunkiem walidacji pochodzenia,
a sam adres nie potwierdza treści odpowiedzi.

Importer manifestu SHADOW wymaga czasu pobrania Fix1 ze strefą, ściśle
po D-1 09:50 Europe/Warsaw. Na kopii oficjalnej sesji dla 09.11.2026
test odrzuca brak strefy oraz czas przed granicą lub równy granicy
przed inicjalizacją bazy. Zmieniony znacznik sekundę po granicy
przechodzi suchy przebieg. Jest to test chronologii metadanych,
a nie ustalenie rzeczywistej chwili publikacji.

Scoring z izolowanego archiwum sprawdza, czy Fix1 pobrano ściśle po
D-1 09:50, również gdy prognozę wydano wcześniej. Test na PostgreSQL
odrzuca pobranie sekundę przed granicą i w chwili granicznej, a sekundę
później zapisuje wynik. Kontrola dotyczy czasu pobrania.

Bezpośredni zapis paczki Fix1 do izolowanego PostgreSQL stosuje ten
sam warunek czasu pobrania co importer manifestu. Test modyfikuje
wyłącznie znacznik paczki opartej na oficjalnym HTML. Przed D-1 09:50
i dokładnie o 09:50 nie powstaje paczka, cena ani wpis kwarantanny.
Znacznik sekundę po granicy pozwala zapisać komplet 24 cen.

Archiwum SHADOW odrzuca jako konflikt dobę z cenami przypisanymi do
różnych paczek dowodowych lub do paczki z innej daty dostawy.
Test na oficjalnych cenach w izolowanym PostgreSQL podmienia
powiązanie jednej godziny bez zmiany ceny. Dwa ponowienia zostawiają
jeden wpis kwarantanny i zachowują istniejące wiersze.

Narzędzie pobierania referencji sprawdza końcowy URL odpowiedzi przed
odczytem HTML. Przekierowanie na stronę bez daty, inną sesję lub inną
domenę nie tworzy plików HTML ani CSV. Ponowny odczyt oficjalnej
sesji TGE z 08.11.2026 przeszedł kontrolę i zwrócił 24 ceny dla
dostawy 09.11.2026.

Importer ogranicza również nieskompresowany HTML do 8 MiB i sprawdza
deklarowaną długość, jeśli manifest ją podaje. Test na oficjalnej sesji
potwierdza akceptację poprawnego pliku oraz odrzucenie błędnej długości
i przekroczenia limitu przed inicjalizacją bazy, również przy zgodnym
SHA-256 nadmiarowego pliku.

Importer odrzuca powielone pola JSON w manifeście. Test na kopii
oficjalnej sesji dla 09.11.2026 dodaje wcześniejszy `retrieved_at_utc`
przed poprawnym znacznikiem. Oba wpisy powodują odrzucenie manifestu
przed inicjalizacją bazy, zamiast cichego przyjęcia ostatniej wartości.

Pobieranie referencji ogranicza odpowiedź HTTP TGE do 8 MiB, zgodnie
z importerem. Test odrzuca HTTP 206 przed odczytem treści i zatrzymuje
nadmiarową odpowiedź przed parserem oraz zapisem. Kontrolny odczyt
oficjalnej sesji 08.11.2026 zwrócił ten sam CSV z 24 cenami dla dostawy
09.11.2026, a manifest przeszedł import w trybie bez zapisu.

Porównanie archiwum z referencją Fix1 zachowuje wiersze odczytane z
jednorazowego iteratora na potrzeby sprawdzenia kompletności i cen.
Test używa oficjalnego CSV dla 09.11.2026. Potwierdza porównanie 24
okresów i wskazanie tylko ostatniej godziny UTC po zmianie jednej ceny
w kopii historii.

Test współbieżności archiwum i scoringu wykorzystuje oficjalny HTML
Fixing I oraz testową zmianę ceny H01. PostgreSQL potwierdza, że ponowna
ocena czeka na blokadzie doby, gdy zapis konfliktu jest w toku. Po
zatwierdzeniu kwarantanny scoring odrzuca ponowienie i zachowuje
wcześniejszy wynik bez zmian.

Scoring SHADOW sprawdza poziom izolacji aktywnej transakcji przed
blokadą doby. Wymaga `READ COMMITTED`, aby po oczekiwaniu odczytać
kwarantannę zatwierdzoną przez archiwum. Test integracyjny obejmuje
domyślne `REPEATABLE READ` sesji i aktywną transakcję `SERIALIZABLE`
przy domyślnym `READ COMMITTED`. Po odrzuceniu obu przypadków
potwierdza poprawny zapis i ponowienie w `READ COMMITTED`.

Archiwizacja i scoring SHADOW używają tej samej blokady doby i sprawdzają
`READ COMMITTED` w aktywnej transakcji. Test na dwóch połączeniach
izolowanego PostgreSQL otwiera migawkę `REPEATABLE READ` przed zapisem
pełnej doby na drugim połączeniu. Próba archiwizacji z tej migawki jest
odrzucana. Ponowienie w `READ COMMITTED` zwraca `REPLAY` i pozostawia
jedną partię, 24 ceny oraz pustą kwarantannę.

Audyt SQL tylko do odczytu klasyfikuje braki względem pierwszej i ostatniej
doby Fix1 w audytowanym zakresie, także gdy żadna data referencji nie ma
wierszy w bazie. Test na izolowanym PostgreSQL z oficjalnymi CSV TGE
rozpoznaje 09.11.2026 jako lukę między wpisami 08 i 10.11.2026, a
07.11.2026 jako datę poza zakresem. Oba rozpoznania korzystają z tej samej
migawki audytu.

Scoring walk-forward weryfikuje chronologię JWCD zwróconą przez model
względem D-1 09:50 Europe/Warsaw osobno dla każdej doby treningowej.
Test dopuszcza pobranie zakończone dokładnie o granicy. Publikacja,
początek lub koniec pobrania sekundę po granicy treningowej wykluczają
dobę ze wspólnego rankingu, nawet jeśli mieszczą się przed granicą
doby docelowej. Raport wskazuje model i dobę treningową.

Audyt PostgreSQL tylko do odczytu odrzuca nieskończone znaczniki
`retrieved_at_utc` i `created_at` snapshotów PK5 przed odczytem
wyników zapytania. Tak jak w audycie Fix1, uznaje wyłącznie skończone
wartości `timestamptz`. Test izolowanej bazy potwierdza, że taki
snapshot nie jest kandydatem czasowym, a raport nadal zwraca 25 wierszy
PK5 i dane Fix1 dla tej samej doby.

Audyt CSV i ZIP rozróżnia brak czasu dostępności ceny Fix1, niepoprawny
znacznik oraz czas równy D-1 09:50 Europe/Warsaw lub wcześniejszy.
Test CSV sprawdza osobno brak wartości, datę bez strefy oraz czasy
sekundę przed granicą, dokładnie na granicy i sekundę po niej.
Liczniki są dostępne dla każdej doby w audycie CSV i w podsumowaniach.

Importer SHADOW sprawdza zgodność opcjonalnej kolumny
`period_start_utc` z etykietą godziny referencji Fix1. Test na kopii
oficjalnej sesji dla 09.11.2026 przyjmuje 24 prawidłowe okresy, a przed
inicjalizacją bazy odrzuca przesuniętą godzinę, znacznik bez strefy
i pustą wartość. Każdy wariant ma aktualny SHA-256 w manifeście.

Eksport walidacyjny SHADOW dopisuje początek okresu UTC do referencji
Fix1 dla standardowej doby 24h. Test odtwarza oficjalny HTML sesji dla
dostawy 09.11.2026, porównuje wszystkie 24 ceny z zapisaną referencją
i sprawdza import bez zapisu. Ścieżka standardowa odrzuca doby 23h i 25h
przed pobraniem. Osobny eksport jesienny zachowuje same etykiety TGE.

Eksport walidacyjny tworzy pliki dowodowe wyłącznie pod wolnymi nazwami.
Jeśli CSV już istnieje lub jego zapis się nie powiedzie, wycofuje nowy
HTML tej próby. Test na zapisanym oficjalnym HTML dla 02.11.2026 sprawdza
kolizję zarówno z istniejącym HTML, jak i CSV oraz zachowanie ich treści.

Pobrano oficjalną sesję TGE z 25.10.2026 dla dostawy 26.10.2026,
pierwszej standardowej doby po jesiennej zmianie czasu. Zapisano HTML,
CSV i manifest z rzeczywistym czasem pobrania 09.11.2026. Test porównuje
24 ceny parsera z osobną ekstrakcją lxml z tego samego HTML, sprawdza
okresy UTC od 25.10 23:00 i potwierdza przyjęcie w imporcie bez zapisu.

Pobrano oficjalną sesję TGE z 09.11.2026 dla dostawy 10.11.2026.
Zapisano HTML, CSV z okresami UTC i manifest z rzeczywistym czasem
pobrania. Test porównuje wszystkie 24 ceny parsera z osobną ekstrakcją
lxml z tego samego HTML. Test na izolowanym PostgreSQL sprawdza zapis
24 cen, ponowienie bez dopisania wierszy, zachowanie dowodów i pustą
kwarantannę.

Test scoringu dla oficjalnej dostawy Fix1 10.11.2026 porównuje MAE,
MAPE, WAPE i bias syntetycznej prognozy z obliczeniem na ułamkach
z CSV. Następnie zmienia tylko okres UTC H01 i uaktualnia hash
referencji w izolowanej bazie. Scoring odrzuca niespójny okres mimo
zgodnego hasha i pozostawia poprzedni wynik.

Walk-forward przed scoringiem potwierdza, że każdy z sześciu modeli
wskazuje ten sam docelowy snapshot JWCD wybrany na D-1 09:50
Europe/Warsaw. Test wstrzykuje inny identyfikator w prognozie dni
podobnych według poziomu. Taka doba odpada dla wszystkich modeli
przed liczeniem metryk, a druga doba zachowuje wspólne 24 okresy.

Audyt SQL Fix1 zlicza niepoprawne jawne okresy UTC i zachowuje raport,
gdy baza zwraca nieskończone znaczniki lub kolumnę bez strefy.
Pełne etykiety legacy nie zastępują błędnych okresów. Test na izolowanym
PostgreSQL porównuje 24 ceny z oficjalną referencją, a po zmianie
okresów wyklucza dobę z porównania.

Audyt SQL Fix1 wskazuje konkretne brakujące, nadmiarowe i powtórzone
okresy UTC według tej samej reguły co audyt PK5. Dla danych opartych
wyłącznie na etykietach godzin pole diagnostyki UTC ma wartość null.
Test na izolowanym PostgreSQL sprawdza brak godziny w dobie 23h oraz
duplikat i okres spoza doby 25h przy zachowanej liczbie wierszy.
Sesja bazy pracuje w Europe/Warsaw, a raport podaje okresy w UTC.

Scoring walk-forward odrzuca metadane treningowe wskazujące dobę
docelową lub późniejszą. Dla wcześniejszej doby wymaga dostępności
Fix1 ściśle po jej własnej granicy D-1 09:50 Europe/Warsaw.
Test regresji sprawdza obie błędne daty oraz czas równy granicy
i sekundę po niej. Odrzucona doba nie trafia do metryk żadnego modelu.

Selekcja JWCD odrzuca całą listę snapshotów, jeśli jej odczyt
przerwie się w połowie. Test zwraca najpierw kompletny snapshot
sprzed D-1 09:50, a potem zgłasza błąd odczytu. Selekcja nie używa
pierwszego snapshotu, ponieważ dalsze rewizje pozostają nieznane.

Walk-forward odczytuje wiersze leniwie ładowanego snapshotu JWCD tylko
raz po weryfikacji czasu dostępności. Test podstawia źródło, które przy
kolejnym odczycie zmieniłoby wartości pod tym samym identyfikatorem.
Modele zachowują identyczne wyniki i ranking jak przy stałej liście
wierszy, również gdy ta sama doba służy później do treningu.

Walk-forward utrwala po pierwszym odczycie leniwie ładowane listy
snapshotów JWCD i cen Fix1 dla danej doby. Test symuluje źródła, które
przy kolejnym odczycie zwróciłyby pustą listę. Każde źródło jest czytane
raz, a wynik scoringu, treningu i wspólnego rankingu zgadza się ze stałą
kopią danych. Dotychczasowa kontrola dostępności poprzedza odczyt.

Raport walk-forward z CSV pokazuje oddzielnie doby docelowe odrzucone
przez audyt danych oraz daty całkowicie nieobecne w źródle. Test używa
oficjalnych cen TGE dla 09 i 10.11.2026. Zakres od 10.11 pokazuje brak
JWCD dla tej doby i lukę 11–12.11. Doba 09.11 pozostaje w audycie źródła
i jest poza zakresem raportu docelowego.

Walk-forward używa tych samych znormalizowanych cen prognozy w metrykach
doby i rankingu łącznym. Test podaje ceny modelu dni podobnych według
poziomu jako jednorazowy iterator. Wyniki dobowe i ranking dla 48
wspólnych okresów są identyczne jak dla zwykłej kolekcji.

Walk-forward utrwala ceny czterech modeli składowych przed liczeniem
mediany, aby scoring użył tych samych wartości. Rozszerzony test
podaje prognozy wszystkich sześciu modeli jako jednorazowe iteratory.
Mediana, wyniki dobowe i wspólny ranking dla 48 okresów pozostają
identyczne jak przy stałych kolekcjach.

Wspólna ścieżka treningu JWCD sprawdza czas dostępności historycznej
ceny Fix1 względem D-1 09:50 danej doby przed selekcją jej snapshotów.
Test bezpośredniego wywołania OLS potwierdza, że dla znacznika równego
granicy lub wcześniejszego nie odczytuje ani snapshotów, ani cen.
Komunikat wskazuje datę odrzuconej doby.

Audyt SQL Fix1 uznaje komplet etykiet godzin bez znaczników UTC
tylko dla standardowej doby 24h. Test na izolowanym PostgreSQL
sprawdza, że zestawy 23 i 25 ponumerowanych wierszy zachowują
licznik pokrycia, ale są raportowane jako niekompletne. Zestaw
24h nadal przechodzi kontrolę struktury.

Audyt SQL Fix1 wskazuje oczekiwaną i zapisaną etykietę godziny
dla każdego niezgodnego okresu UTC w standardowej dobie 24h.
Test na izolowanym PostgreSQL zamienia H01 i H02 w kopii oficjalnej
referencji dla 10.11.2026. Audyt wskazuje oba okresy i wyklucza dobę
z porównania mimo kompletnej listy UTC i zgodnych cen.

Importer SHADOW ogranicza odczyt referencji CSV Fix1 do 64 KiB,
a wspólny parser CSV stosuje ten sam limit przy scoringu. Test na
kopii oficjalnego manifestu dla 10.11.2026 przyjmuje oryginalny plik
i odrzuca większy przed inicjalizacją bazy, mimo zgodnego SHA-256.

Izolowane archiwum nie przywraca do cen partii będącej już w
kwarantannie, nawet gdy brakuje wszystkich wierszy tej doby. Test
na oficjalnej sesji 10.11.2026 symuluje ubytek wierszy wyłącznie
w bazie testowej. Dwa ponowienia nie dopisują cen i zachowują
pierwotny wpis kwarantanny.

Kwarantanna doby blokuje uzupełnienie pustej tabeli cen również
nową partią z innym SHA-256. Test na sesji 10.11.2026 zmienia tylko
zakończenia linii referencji CSV. Izolowany PostgreSQL zapisuje
nową partię dowodową w kwarantannie raz, zachowuje pierwotny wpis
i pozostawia tabelę cen pustą.

Archiwum SHADOW wykrywa całkowity ubytek cen, jeśli zachowała się
partia źródłowa dla tej doby. Test na oficjalnej sesji 10.11.2026
usuwa 24 wiersze wyłącznie w izolowanym PostgreSQL i ponawia import.
Pierwsza próba zapisuje kwarantannę, druga zachowuje ten sam wpis.
Obie pozostawiają ceny puste i zachowują metadane partii źródłowej.

Audyt read-only Fix1 raportuje najwcześniejszy czas pobrania cen
i wyklucza z pokrycia porównania doby, w których część cen pobrano
do D-1 09:50 Europe/Warsaw włącznie, a część po tej granicy.
Testy na izolowanym PostgreSQL sprawdzają granicę dla doby 25h
oraz wykluczenie doby 24h mimo pełnej zgodności cen z referencją TGE.

Walk-forward raportuje osobne rankingi MAE dla dób 23, 24 i 25h.
Każda grupa pokazuje też MAPE, WAPE, bias oraz liczbę wspólnych
okresów. Testy porównują metryki z wynikami dobowymi i potwierdzają,
że odrzucona doba 23h nie trafia do rankingu żadnego modelu.

Walk-forward rozdziela ranking na dni od poniedziałku do piątku
oraz weekendy według lokalnej daty dostawy w Europe/Warsaw.
Testy obejmują 49 wspólnych okresów w weekend zmiany czasu
i 24 w poniedziałek. Cena zerowa z poniedziałku pomniejsza
licznik okresów MAPE wyłącznie w grupie dni od poniedziałku do piątku.

Walk-forward pokazuje ranking według lokalnej godziny początku okresu
w Europe/Warsaw. Drugie wystąpienie 02:00 jesienią ma osobną grupę
`02:00[fold=1]`, a brak 02:00 wiosną nie zwiększa pokrycia.
Test dla dób 23/24/25h potwierdza sumę 72 wspólnych okresów oraz
osobne rozliczenie ceny zerowej w powtórzonej godzinie.

Walk-forward przyjmuje opcjonalne archiwalne prognozy FROZEN JWCD 90D
i zestawia ich wyniki dobowe z OLS90 na tych samych cenach i okresach
UTC. Benchmark jest oddzielny od rankingu sześciu modeli SHADOW.
Test z danymi syntetycznymi potwierdza zgodność okresów i odrzuca
prognozę FROZEN wystawioną sekundę po D-1 09:50 Europe/Warsaw.
Znaczniki publikacji, pobrania i wystawienia muszą pochodzić z archiwum.

Benchmark FROZEN JWCD 90D / OLS90 raportuje łączne MAE, MAPE,
WAPE i bias wyłącznie dla wspólnych okresów dopuszczonych do obu ocen.
Test syntetyczny liczy metryki z 49 okresów dób 24h i 25h,
odrzuca spóźnioną trzecią prognozę i sprawdza ranking SHADOW osobno.

Dla dostawy 11.11.2026 zapisano odpowiedź HTTP 200 TGE z sesji
10.11.2026, manifest SHA-256 i 24 ceny z okresami UTC.
CSV pochodzi z osobnej ekstrakcji XPath tej samej odpowiedzi HTML.
Test porównuje wszystkie ceny oraz sprawdza H07, gdzie Fixing I
to 950,67 PLN/MWh, a notowania ciągłe 953,00 PLN/MWh.
Drugi test sprawdza zapis i ponowienie na izolowanym PostgreSQL.

Polecenie `python shadow_walkforward_csv.py --evaluated-at CZAS --frozen-90d-json PLIK historia.csv`
uruchamia benchmark z lokalnego eksportu JSON. Plik zawiera tablicę
rekordów z polami `delivery_date`, `forecast_id`, `forecast_status`,
`model_version`, `issued_at`, `publication_ts`, `retrieved_at`
oraz `prices` jako listę par [start UTC, cena]. Test CLI sprawdza
25 okresów doby zmiany czasu oraz odrzucenie duplikatu daty dostawy.

Narzędzie walidacyjne TGE ponawia odczyt po timeoutach oraz HTTP
429, 500, 502, 503 i 504, maksymalnie do trzech prób. Uwzględnia
Retry-After, a przy żądanym oczekiwaniu ponad 10 s kończy pobieranie.
Testy symulują błędy HTTP i sieci, sprawdzają brak plików po porażce
oraz zgodne SHA-256 HTML i CSV dostawy 11.11.2026 po udanym ponowieniu.

Walidacyjny odczyt TGE ponawia pobranie uciętej odpowiedzi HTTP,
także gdy sam parser widzi wszystkie 24 ceny Fixing I. Test na HTML
dostawy 11.11.2026 usuwa końcówkę po tabeli, zachowuje deklarowaną
długość i sprawdza brak zapisu po trzech niepełnych próbach.
Gdy trzecia próba jest pełna, zapisany HTML i CSV mają zgodne SHA-256.

Importer SHADOW wymaga jawnej pustej tablicy `errors` i niepustej
tablicy obiektów dowodowych `entries`. Test na kopii manifestu dostawy
11.11.2026 potwierdza poprawny import bez zapisu i odrzuca brak pola,
błędne typy oraz zgłoszone błędy przed odczytem HTML lub otwarciem
transakcji.

Audyt CSV wymaga jawnego potwierdzenia weryfikacji Fix1 oraz
tekstowej wersji danych. Test z 24 oficjalnymi cenami dostawy
11.11.2026 i syntetycznymi metadanymi JWCD potwierdza, że wartości
liczbowe w tych polach wykluczają dobę z treningu mimo kompletu cen
i dostępności JWCD do D-1 09:50 Europe/Warsaw.

Importer JSON benchmarku FROZEN odrzuca powtórzone klucze
i wymaga, by każda para [okres UTC, cena] była tablicą.
Test CLI podaje sprzeczne znaczniki wystawienia, publikacji
i pobrania względem D-1 09:50 Europe/Warsaw. Każdy duplikat
blokuje benchmark przed oceną, a poprawny eksport nadal obejmuje 25 okresów.

Archiwum SHADOW ogranicza oczekiwanie na blokady PostgreSQL do 5 s
w transakcji i respektuje krótszy limit połączenia testowego.
Test blokuje drugą dobę siedmiodniowego manifestu TGE i potwierdza
wycofanie całego zapisu po timeout. Po zwolnieniu blokady ponowienie
zapisuje 168 okresów, a kolejny import nie dopisuje rekordów.

Importer SHADOW przyjmuje opcjonalne `--retry-lock` dla zapisu
w izolowanej bazie. Po błędzie blokady ponawia cały manifest
po 250 i 500 ms, za każdym razem po wycofaniu transakcji.
Test potwierdza limit trzech prób, pustą bazę przed ponowieniem,
zapis 168 okresów po zwolnieniu blokady i brak duplikatów.

Scoring PostgreSQL SHADOW normalizuje wejściowe ceny prognozy
raz dla metryk i skrótu wejścia. Test na oficjalnej sesji TGE
dla dostawy 02.11.2026 zapisuje wynik z jednorazowych iteratorów,
a potem potwierdza idempotencję przy odwróconej kolejności i
równoważnym przesunięciu strefy czasowej. Test starszego skrótu
nadal sprawdza odczyt zapisanego wyniku.

Audyt SQL read-only pokazuje dla wybranego kandydata PK5/JWCD
czas pobrania, najpóźniejszej publikacji, zapisu w archiwum i granicę
D-1 09:50 Europe/Warsaw. Czas zapisu jest granicą pomocniczą,
a kandydat nadal wymaga weryfikacji przed treningiem.
Testy obejmują dobę 25h z zapisem na granicy i dobę 24h,
gdzie spóźniony zapis oraz konflikt wersji nie dostają metadanych wyboru.

Eksport JSON walk-forward zapisuje wyniki dobowe jako obiekty
z metrykami i identyfikatorami prognozy, modelu i actual.
Test CLI dla doby 25h odczytuje te pola bez parsowania tekstowej
reprezentacji Pythona i potwierdza identyczny wynik OLS90 w dobie
oraz w parze benchmarku FROZEN. Decimal zachowuje zapis tekstowy.

Benchmark FROZEN sprawdza chronologię przed odczytaniem cen.
Wspólna walidacja ze scoringiem wymaga publikacji i pobrania
najpóźniej w chwili wystawienia, do D-1 09:50 Europe/Warsaw włącznie.
Test udostępnia spóźnioną prognozę jako iterator zgłaszający błąd
przy odczycie. Doba zostaje wykluczona, a 49 wcześniejszych
wspólnych okresów zachowuje wynik benchmarku.

Wspólna walidacja cen SHADOW odrzuca słowniki podane jako kolekcja
cen lub jako para [okres UTC, cena]. Klucze słownika nie mogą udawać
danych cenowych. Testy obejmują prognozę i actual oraz zapis wyniku
w izolowanym PostgreSQL przed i po poprawnym scoringu. Jednorazowe
iteratory par pozostają obsługiwane.

Walk-forward i wspólny trening JWCD sprawdzają tekstową wersję
zweryfikowanych cen Fix1 przed odczytem cen i snapshotów.
Test z trzema dobami potwierdza, że liczbowa wersja wyklucza
dobę z oceny oraz z treningu następnej prognozy. Wcześniejsza
doba zachowuje 24 wspólne okresy w rankingu.

Audyt SQL read-only inwentaryzuje teraz starszą tabelę `history.hourly`
według zakresu dostaw. Raport podaje luki, oczekiwane 23/24/25 okresów
i liczbę poprawnych wartości JWCD oraz Fix1. Numery godzin i komplet cen
nie potwierdzają dostępności względem D-1 09:50 Europe/Warsaw.
Test na izolowanym PostgreSQL łączy oficjalną dobę 11.11.2026
z przypadkami zmiany czasu oraz sprawdza wartości NaN i nieskończone.

Audyt read-only porównuje ceny Fix1 z `history.hourly` z przekazaną
referencją TGE w ramach tego samego odczytu. Test na izolowanym
PostgreSQL potwierdza zgodność 24 cen dostawy 11.11.2026 i wskazuje
dokładny okres UTC po zmianie H07. Zgodność cen nie potwierdza
dostępności danych do treningu przed D-1 09:50 Europe/Warsaw.

Audyt read-only `history.hourly` liczy etykiety godzin z jednocześnie
skończonym JWCD i Fix1. Dla doby 24h podaje też etykiety bez pary.
Dla dób 23h i 25h lista braków ma wartość null, ponieważ etykiety
legacy nie identyfikują okresów UTC. Test na izolowanym PostgreSQL
sprawdza obie doby zmiany czasu oraz dobę TGE 11.11.2026 z błędnym
JWCD w H01 i Fix1 w H02. Po 23 poprawne wartości każdej serii
dają 22 wspólne godziny.

Audyt danych wejściowych walk-forward sprawdza identyfikator snapshotu
JWCD według tych samych reguł co wybór snapshotu. Dopuszcza niepusty
tekst lub liczbę całkowitą. Test na cenach TGE z 11.11.2026 i syntetycznym
JWCD potwierdza wykluczenie doby z błędnym typem identyfikatora, nawet
gdy wszystkie 24 okresy spełniają granicę D-1 09:50 Europe/Warsaw.

Odczyt CSV pomija komentarze przed nagłówkiem i między rekordami.
Wielowierszowe pola w cudzysłowie zachowują linie zaczynające się od `#`.
Test na 24 cenach TGE z 11.11.2026 i syntetycznych metadanych sprawdza,
że zachowana linia rozróżnia wersje Fix1, a konflikt wyklucza dobę
z walk-forward mimo terminowej dostępności wszystkich wartości JWCD.

Audyt read-only ZIP historii PK5/Fix1 odrzuca powtórzone klucze
manifestu, również wewnątrz mapy SHA-256. Test tworzy izolowane ZIP-y,
w których druga wartość liczby dób lub skrótu CSV/XLSB zgadza się
z zawartością, a pierwsza jest sprzeczna. Każdy taki manifest jest
odrzucany, a bajty archiwum pozostają identyczne.
Zaostrzony audyt przeszedł również na oryginalnym ZIP z 04.11.2026
(50 928 wierszy, 216/216 cen zgodnych z referencją TGE, identyczny
SHA-256 ZIP po audycie). Brak znaczników dostępności nadal wyklucza
te dane z treningu względem D-1 09:50 Europe/Warsaw.

Opcja `python shadow_history_bundle.py historia.zip --verify-workbook`
porównuje w trybie read-only zapisane wartości komórek arkusza
`KSEdo10` (B, C, J, V) z parami JWCD/Fix1 eksportu CSV dla dób 24h.
Wymaga opcjonalnego `pyxlsb` w środowisku audytu. Raport podaje liczbę
zgodnych par, braków, duplikatów i różnic dla każdej serii. Kontrola
sprawdza wyłącznie wiersze objęte eksportem i korzysta z wartości
zapisanych w XLSB, bez przeliczania formuł.

Na oryginalnym ZIP z 04.11.2026 zgodnych było 50 928 z 50 928 par,
bez braków, duplikatów lub różnic. SHA-256 ZIP przed audytem i po nim
był identyczny. Zgodność komórek nie dokumentuje publikacji ani pobrania
JWCD do D-1 09:50 Europe/Warsaw, więc audyt nadal dopuszcza 0 dób do treningu.

Audyt `--verify-workbook` pokazuje także wiersze i etykiety godzin
z XLSB dla dób zmiany czasu pominiętych przez CSV. W oryginalnym
archiwum z 04.11.2026 wszystkie sześć dób 23h i pięć z sześciu dób
25h ma po 24 wiersze. Tylko 30.10.2022 ma 25 wierszy, w tym liczbową
etykietę 25. Każdy z tych wierszy ma skończone JWCD i Fix1.
Same etykiety nie potwierdzają mapowania na UTC ani dostępności do
D-1 09:50 Europe/Warsaw. Wynik standardowych dób nadal wynosi
50 928 zgodnych par, a SHA-256 źródłowego ZIP był identyczny po audycie.

Audyt read-only PK5/JWCD pokazuje najpóźniejszą odczytaną publikację
dla każdego snapshotu oraz przyczyny wykluczenia kandydata czasowego.
Test izolowanego PostgreSQL dla doby 25h odróżnia publikację po zapisie
archiwalnym, ale przed D-1 09:50 Europe/Warsaw, od spóźnionego zapisu,
braku pełnych znaczników i nieobsługiwanego źródła. Zapis dokładnie
o 09:50 nadal przechodzi kontrolę pomocniczą i wymaga weryfikacji pochodzenia.
Znacznik spoza zakresu po konwersji na UTC jest liczony jako
niezweryfikowany. Test potwierdza, że odczyt raportu trwa dalej.

Dla dostawy 12.11.2026 zapisano oficjalny HTML TGE z sesji 11.11,
pobrany o 12:20:00 Europe/Warsaw, oraz CSV i manifest SHA-256.
Parser zgadza się w 24 okresach UTC z osobną ekstrakcją XPath
tej samej odpowiedzi. H10 zachowuje 2 700,83 PLN/MWh z Fixing I
przy 2 700,70 PLN/MWh z notowań ciągłych. Test integracyjny sprawdza
zapis 24 okresów oraz zero nowych wierszy przy ponowieniu manifestu
na izolowanym PostgreSQL.

Scoring archiwalny sprawdza wystawienie, publikację i pobranie względem
D-1 09:50 Europe/Warsaw przed odczytem prognozy lub blokadą doby.
Test izolowanego PostgreSQL używa oficjalnych 24 cen TGE z 12.11.2026
i syntetycznej prognozy 1 000 PLN/MWh. Sprawdza MAE, MAPE, WAPE i bias
względem obliczeń na ułamkach dokładnych, a następnie odrzuca każdy
z trzech znaczników przesunięty o sekundę po granicy i zachowuje
wcześniejszy wynik przy ponowieniu.

Wspólna konwersja timestampów do UTC zgłasza błąd walidacji przy
wyjściu poza zakres dat. Test doby 25h odrzuca dwa wadliwe snapshoty
JWCD po błędnej publikacji lub pobraniu i zachowuje poprawne 25 okresów.
Test scoringu potwierdza odrzucenie wadliwych metadanych przed odczytem cen.

Selekcja as-of JWCD sprawdza datę dostawy i dostępność pobrania do
D-1 09:50 Europe/Warsaw przed kontrolą duplikatów identyfikatora.
Test dób 23h i 25h potwierdza, że spóźniony snapshot lub snapshot
innej doby z tym samym identyfikatorem nie zmienia poprawnego wyboru.
Dwa duplikaty dostępne przed granicą nadal są odrzucane przed odczytem danych.

Selekcja as-of traktuje przerwany odczyt niestandardowego źródła
wierszy JWCD jako wadę pojedynczego snapshotu. Test dla doby 25h
sprawdza przerwanie po pierwszym wierszu, odrzucenie częściowej
wersji i zachowanie pełnej wersji dostępnej do D-1 09:50 Europe/Warsaw.

Walk-forward nie odczytuje cen ani snapshotów paczki używanej wyłącznie
do treningu, jeśli ceny Fix1 pojawiły się po D-1 09:50 Europe/Warsaw
dla wszystkich prognoz, których okna obejmują tę paczkę. Test z dwoma
oknami zachowuje diagnostykę spóźnionych cen. Dodatkowa prognoza poza
oknem 90 dni nie powoduje odczytu tej paczki.
Przy dostępności dokładnie o 09:50 do oceny przechodzi późniejsza
prognoza i jej 24 wspólne okresy.

Walk-forward ogranicza odczyt paczek treningowych także do prognoz,
których granica D-1 09:50 Europe/Warsaw już minęła lub przypada
dokładnie w chwili oceny. Test potwierdza brak odczytu paczki potrzebnej
wyłącznie przyszłej prognozie i zachowanie wcześniejszych 24 okresów
w rankingu wszystkich sześciu modeli.

Porównanie offline z archiwalnym FROZEN 90D wyklucza dobę po
przerwanym odczycie prognozy, jeśli metadane przeszły kontrolę
D-1 09:50 Europe/Warsaw. Test syntetyczny przerywa odczyt doby
25h po pierwszym okresie i zachowuje 24 okresy wcześniejszej doby
w benchmarku. Ranking sześciu modeli SHADOW nadal obejmuje 49 okresów.

Benchmark offline FROZEN 90D pokazuje pokrycie osobno dla dób 23h,
24h i 25h. Liczy doby ocenione w SHADOW, doby sparowane z FROZEN,
doby bez pary oraz okresy wspólne. Testy sprawdzają odrzuconą dobę
25h przy zachowanej dobie 24h oraz eksport JSON z pełną parą 25h.

Raport importu SHADOW wskazuje blokadę scoringu przez kwarantannę
także przy ponowieniu oryginalnego zapisu po odrzuceniu sprzecznej
rewizji. Test na oficjalnym HTML TGE i syntetycznej zmianie jednej
ceny zachowuje status REPLAY, wcześniej zapisany wynik i blokadę.
Dwie kolejne doby tego manifestu zapisują się bez blokady.
Dry-run podaje null, ponieważ nie odczytuje stanu bazy.

Import manifestu SHADOW inicjalizuje schemat testowy w tej samej
transakcji co ceny i kwarantannę. Test pustej bazy potwierdza, że
błąd na drugiej dobie wycofuje zapis pierwszej doby wraz z utworzeniem
schematu. Po ustąpieniu błędu ten sam manifest zapisuje 168 okresów.
Próba dwóch równoczesnych pierwszych importów na lokalnym PostgreSQL
16.2 dała siedem zapisanych dób i siedem ponowień. Blokada inicjalizacji
jest odrębna od blokad dób i obowiązuje tylko przy brakującym schemacie
lub tabeli.

Parser SHADOW sprawdza fizyczną grupę Fixing I z colspan=2 i pojedyncze
komórki nagłówka kolumn. Test na HTML TGE dla dostawy 12.11.2026
odczytuje 24 ceny z oryginału. Podział nagłówka na dwie identycznie
opisane komórki przy niezmienionych danych powoduje odrzucenie tabeli.

Parser SHADOW wymaga nagłówków Fixing I w sekcji thead i odczytuje
ceny wyłącznie z tbody. Test na oficjalnym HTML TGE dla dostawy
02.11.2026 potwierdza komplet 24 cen w oryginale. Przeniesienie
nagłówków lub cen do tfoot blokuje przyjęcie doby.

Bezpośredni zapis dowodu Fix1 stosuje wspólną walidację czasu UTC
dla pobrania, tak jak import manifestu. Testy na HTML TGE dla dostawy
02.11.2026 zachowują identyczny wynik dla równoważnych stref czasowych.
Znacznik poza zakresem UTC jest odrzucany przed blokadą doby.
Test PostgreSQL potwierdza zachowanie 24 cen, jednej paczki i pustej
kwarantanny oraz poprawne ponowienie.

Walidowany parser RDN odrzuca wiersz godzinowy dla wybranej dostawy,
jeśli typ instrumentu różni się od 60 minut. Test na oficjalnym HTML
dla 12.11.2026 potwierdza komplet 24 cen i odrzucenie doby po dodaniu
drugiego wiersza H10 z typem 15 minut. Wiersze kwadransowe z własnymi
etykietami pozostają poza odczytem Fix1.

Parser RDN wymaga komórek td w tbody oraz zgodnych znaczników
otwarcia i zamknięcia komórek. Test na oficjalnym HTML dla dostaw
02.11.2026 i 25.10.2026 potwierdza odczyt odpowiednio 24 i 25 cen.
Zamiana komórki ceny na th lub domknięcie td znacznikiem </th>
powoduje odrzucenie tabeli, także dla powtórzonego okresu H02a.

Audyt CSV rozróżnia pominięte i puste pola czasu pobrania od
nieprawidłowych wartości. Nieprawidłowa wartość logiczna lub zero w retrieved_at albo
retrieved_at_utc wyklucza dobę z walk-forward, nawet gdy drugie pole
zawiera poprawny czas. Test syntetyczny dla dób 23h i 25h potwierdza
przyjęcie zgodnych znaczników dokładnie o D-1 09:50 Europe/Warsaw
oraz wykluczenie każdej doby po zmianie jednego pola.

Scoring dobowy liczy MAE, MAPE, WAPE i bias z precyzją 34 cyfr
i zaokrągleniem ROUND_HALF_EVEN niezależnie od ustawień arytmetyki
wywołującego. Test na izolowanym PostgreSQL używa cen z archiwum TGE
dla 02.11.2026 i syntetycznej prognozy 1 000 PLN/MWh. Zmiana
zaokrąglenia i włączenie wyjątku dla wyniku niedokładnego zachowuje
status REPLAY, zapisany odcisk wejścia i zapisane metryki.

Odcisk i metryki nowego scoringu PostgreSQL zapisują wykładniki z E
niezależnie od ustawień Decimal wywołującego. Ponowienie rozpoznaje
także wcześniejszy zapis z małym e bez zmiany jego odcisku lub metryk.
Testy na cenach TGE z 02.11.2026 i syntetycznej prognozie 1 000 PLN/MWh
sprawdzają obie ścieżki oraz zgodność odcisku z kanonicznym zapisem.

Walk-forward SHADOW korzysta z tych samych ustawień Decimal co
scoring dobowy. Test syntetyczny dla dób 24h i 25h potwierdza te same
prognozy sześciu modeli, cztery metryki i ranking na 49 wspólnych
okresach po zmianie ustawień wywołującego. Porównanie offline
z syntetyczną prognozą oznaczoną FROZEN zachowuje 25 okresów.

Audyt SQL read-only pomija przy odczycie znaczniki PostgreSQL spoza
zakresu dat Pythona w UTC. Błędne czasy PK5 wykluczają kandydata,
błędne czasy dostępności Fix1 trafiają do braków, a jawny wadliwy okres
Fix1 blokuje uznanie doby za kompletną. Testy izolowanego PostgreSQL
obejmują daty p.n.e. i rok 10000 oraz potwierdzają zachowanie strefy
sesji po zakończeniu audytu.

Pierwsze utworzenie tabeli scoringu korzysta z tej samej blokady
transakcyjnej co inicjalizacja archiwum SHADOW. Test PostgreSQL
na 24 cenach TGE z 02.11.2026 potwierdza, że przy zajętej blokadzie
tabela nie powstaje. Po jej zwolnieniu cztery równoległe wywołania
dają jeden INSERT i trzy REPLAY, zachowując 24 ceny i jeden wynik.

Bezpośrednie wywołania OLS 30/60/90D, dni podobnych i mediany
korzystają z tych samych ustawień Decimal co walk-forward.
Test syntetyczny dla doby 25h potwierdza identyczne wyniki wszystkich
sześciu wariantów po zmianie trybu zaokrąglania i włączeniu wyjątku
dla wyniku niedokładnego u wywołującego.

Dla dostawy 13.11.2026 dodano oficjalny HTML TGE z sesji 12.11,
pobrany o 11:17:05 Europe/Warsaw, oraz referencję XPath i manifest
SHA-256. Parser i ekstrakcja XPath zgadzają się dla wszystkich
24 okresów UTC. H08 zachowuje cenę Fixing I 711,58 PLN/MWh mimo
braku wolumenu w tej kolumnie i ceny 712,00 PLN/MWh w notowaniach
ciągłych. Test PostgreSQL sprawdza import i ponowienie tej paczki.

Scoring archiwalny sprawdza komplet okresów UTC prognozy przed
blokadą doby i odczytem cen Fix1. Test PostgreSQL na cenach TGE
z 13.11.2026 odrzuca brak jednego okresu oraz okres z innej doby przy
zachowanej liczbie 24 wierszy. Wynik zapisany dla kompletnej
syntetycznej prognozy pozostaje dostępny przez REPLAY.

Selekcja snapshotów JWCD i audyt CSV tak samo normalizują
identyfikatory przed kontrolą duplikatów. Test dla dób 23h i 25h
potwierdza odrzucenie pary identyfikatorów 17 i „ 17 ” bez odczytu
wierszy, gdy oba snapshoty były dostępne do D-1 09:50 Europe/Warsaw.
Duplikat dostępny sekundę po granicy nie wpływa na wybór.

Ekstraktor XPath sprawdza format cen Fixing I przed zapisem HTML i CSV.
Test na kopii oficjalnej odpowiedzi dla dostawy 13.11.2026 zachowuje
24 ceny z oryginału i poprawnie czyta grupowanie tysięcy spacją
nierozdzielającą. Błędne grupowanie lub więcej niż cztery cyfry po
przecinku w H08 przerywa zapis przed utworzeniem plików.

Ekstraktor XPath sprawdza także jednostkę wolumenu w nagłówku
Fixing I. Test na oficjalnym HTML dla dostawy 13.11.2026 potwierdza
odczyt 24 cen z oryginału. Zmiana nagłówka wolumenu z MW na MWh
blokuje utworzenie plików HTML i CSV.

Audyt CSV liczy skrajne daty dostawy, dla których nie da się ustalić
granic doby lub D-1 09:50 Europe/Warsaw, jako błędne rekordy.
Test dodaje osobno 01.01.0001 i 31.12.9999 do referencji TGE
z 13.11.2026. W obu przypadkach raport zachowuje wszystkie 24 ceny
i oznacza dokładnie jeden błędny wiersz daty.

Audyt PostgreSQL w trybie tylko do odczytu pomija nieobsługiwane daty
dostawy w raw.snapshots, actual.tge_fix1 i history.hourly. Raport
liczy takie wiersze osobno dla każdej tabeli, niezależnie od filtra dat.
Test na izolowanej bazie dodaje po pięć wadliwych rekordów do każdego
źródła i potwierdza, że raport prawidłowych dostaw nadal się generuje.

Nowe manifesty standardowych dób deklarują EXPLICIT_UTC.
Import wymaga wtedy kolumny period_start_utc i jej zgodności z H01–H24.
Test na kopii paczki TGE z 13.11.2026 potwierdza odrzucenie brakującej
kolumny mimo zgodnego SHA-256. Starsza paczka bez deklaracji przechodzi
dotychczasową walidację. Nieznana deklaracja jest odrzucana przed
odczytem HTML.

Walk-forward raportuje wszystkie brakujące daty w niepełnym oknie
treningowym modelu, który zablokował ocenę. Test syntetycznej doby
25h potwierdza, że dwie luki poza oknami 30D i 60D są wskazane
chronologicznie przy OLS 90D. Doba z niepełnym oknem nie trafia
do rankingu na wspólnych okresach.

Raport walk-forward dla CSV rozróżnia brakujące daty treningowe
od dostaw obecnych w pliku, które odrzucił audyt. Dla odrzuconych
dat zwraca przyczyny. Test z cenami TGE z 12–13.11.2026 i syntetycznym
JWCD dla daty ocenianej wykazuje 29 nieobecnych dat oraz jedną dobę
z cenami, lecz bez JWCD. OLS 30D blokuje wspólny ranking.

Audyt CSV raportuje osobno, ile rekordów JWCD przekroczyło D-1 09:50
Europe/Warsaw w polach `publication_ts`, `retrieved_at` i
`retrieval_completed_at`, dla każdej doby i dla całego pliku. Liczniki
mogą się nakładać i obejmują tylko rekordy z poprawną chronologią.
Test obejmuje doby 23h, 24h i 25h oraz znaczniki dokładnie na granicy
i sekundę po niej. Doba 24h używa cen TGE z 13.11.2026 i syntetycznych
znaczników JWCD.

Walk-forward rozróżnia nieobecną dobę treningową od obecnej paczki,
której nie udało się odczytać. Dla wadliwej paczki podaje datę i pole
(`actual` lub `snapshots`). Test syntetycznej doby 25h potwierdza,
że brak jednej doby i błąd odczytu drugiej są raportowane osobno
w oknie OLS 90D. Taka doba nie wchodzi do wspólnego rankingu.

Dla kompletnego okna treningowego walk-forward raportuje wszystkie
daty, których ceny Fix1 stały się dostępne po D-1 09:50 Europe/Warsaw
dla ocenianej dostawy. Test syntetycznej doby 25h wykazuje dwa
spóźnione zestawy cen w oknie OLS 90D. Zestaw dostępny dokładnie
o 09:50 mieści się w limicie, a doba z dwoma spóźnionymi zestawami
nie trafia do wspólnego rankingu.

Scoring PostgreSQL odrzuca pusty identyfikator, nieprawidłową wersję
modelu lub niedozwolony status prognozy przed transakcją, blokadą doby
i odczytem cen. Test integracyjny potwierdza brak próby blokady
i ponowne odtworzenie zapisanego wyniku dla poprawnej prognozy.

Przed potwierdzeniem ponownego importu zgodnych cen Fix1 archiwum
testowe sprawdza SHA-256 zapisanego HTML paczki, z której pochodzą ceny.
Test PostgreSQL na paczce TGE z 02.11.2026 potwierdza, że zmieniony HTML
skutkuje jedną kwarantanną także przy powtórzeniu importu. Zachowane
zostają ceny i zastane bajty HTML.

Ponowny import zgodnych cen zwraca QUARANTINE, gdy inna paczka
dla tej samej doby jest już w kwarantannie. Test na cenach TGE
z 13.11.2026 potwierdza zachowanie 24 zapisanych cen i brak duplikatu
wpisu kwarantanny po dwóch powtórzeniach importu.

Import manifestu wymaga daty dostawy w zapisie YYYY-MM-DD oraz
obsługiwanych granic doby. Test na manifeście TGE z 13.11.2026
potwierdza odczyt 24 cen z oryginału i odrzucenie m.in. daty
tygodniowej ISO i dat skrajnych przed odczytem plików dowodowych.

Audyt porównujący historię z cenami referencyjnymi Fix1 rozróżnia
nieobecną dobę od doby z niekompletnymi lub nieprawidłowymi cenami.
Test na cenach TGE z 11–13.11.2026 potwierdza porównanie 24 cen
z pełnej doby i osobne wykazanie luki oraz doby bez jednej godziny.

Audyt PostgreSQL zachowuje informację o obecności doby z błędnymi
okresami UTC, również gdy pomija odczyt jej cen do porównania z TGE.
Test integracyjny sprawdza, że taka doba trafia do listy nieprawidłowych
cen i nie jest liczona jako brak całej doby.

Przy ponownym imporcie zgodnych cen Fix1 archiwum testowe porównuje
je z cenami z zapisanego HTML, jeśli jego SHA-256 różni się od SHA-256
importowanej paczki. Test na danych TGE z 13.11.2026 zmienia cenę H01
w zapisanym HTML i przelicza jego hash. Dwie próby importu pozostawiają
24 ceny i zmienione bajty HTML oraz zapisują jeden wpis kwarantanny.

Ponowny import zgodnych cen Fix1 sprawdza czas pobrania zapisanej
paczki. W izolowanym PostgreSQL test z cenami TGE dla 13.11.2026
obejmuje czas sekundę przed D-1 09:50 Europe/Warsaw, dokładnie o 09:50
oraz nieskończony znacznik PostgreSQL. Każdy przypadek daje jeden
wpis kwarantanny po dwóch próbach i zachowuje ceny, HTML oraz metadane.

Scoring z archiwum PostgreSQL sygnalizuje brak obsługi dób 23h i 25h
przed blokadą doby. Test integracyjny podaje komplet prognoz dla obu
zmian czasu i potwierdza, że zachowany wynik doby 24h nadal daje REPLAY.
Obsługa 23h i 25h przez scoring walk-forward na jawnych okresach UTC
pozostaje dostępna.

Scoring archiwalny odrzuca nieskończone czasy pobrania Fix1 i daty
spoza zakresu UTC obsługiwanego przez Pythona. Test na cenach TGE
z 02.11.2026 sprawdza zachowanie zapisanego wyniku przy czterech
błędnych znacznikach i poprawny REPLAY w sesji Europe/Warsaw.

Scoring archiwalny sprawdza okresy dostawy Fix1 w UTC. Odrzuca
zapisane okresy poza dobą, nieskończone i przesunięte o minutę.
Test na cenach TGE z 02.11.2026 sprawdza zachowanie wyniku oraz
poprawny REPLAY w sesji Europe/Warsaw po wycofaniu wadliwych danych.

Ponowny import paczki Fix1 kwalifikuje okresy poza granicami doby
do kwarantanny przed przekazaniem znaczników do Pythona. Test na
paczce TGE z 13.11.2026 potwierdza jeden wpis po dwóch próbach dla
czterech błędnych znaczników, w tym wartości nieskończonych. Zapisane
wiersze są zachowane. Po wycofaniu zmian testowych import daje REPLAY
w sesji Europe/Warsaw.

Sesję TGE z 13.11.2026 dla dostawy 14.11.2026 pobrano o 11:15
Europe/Warsaw. Zapisano oryginalny HTML w gzip, CSV z 24 okresami
UTC i manifest SHA-256. Wszystkie ceny Fixing I zgadzają się
z niezależną ekstrakcją XPath z tej samej odpowiedzi, w tym H17
z ceną 1105,60 PLN/MWh. Test PostgreSQL sprawdza INSERT i REPLAY
bez powielenia cen.

Scoring archiwalny odrzuca próbę oceny przed czasem wydania prognozy
jeszcze przed blokadą doby. Test na cenach TGE z 14.11.2026 używa
syntetycznej prognozy o 09:50 Europe/Warsaw i potwierdza, że próba
sekundę wcześniej nie zapisuje wyniku. Ocena w chwili pobrania
cen daje INSERT, a powtórzenie REPLAY bez duplikatu.

Walk-forward podaje wszystkie daty treningowe bez dopuszczonego
snapshotu JWCD oraz przyczyny odrzucenia poszczególnych snapshotów.
Test syntetycznej doby 25h sprawdza dwie daty w oknie OLS 90D.
Obejmuje publikację po zakończeniu pobrania i pobranie po D-1 09:50
Europe/Warsaw. Zawartość spóźnionego snapshotu nie jest odczytywana.

Raport JWCD wskazuje osobno publikację po D-1 09:50 Europe/Warsaw,
spóźnione rozpoczęcie lub zakończenie pobrania oraz odwróconą
kolejność znaczników pobrania. Testy syntetyczne dla dób 23h i 25h
potwierdzają te przyczyny oraz dopuszczenie kompletu danych
dostępnego dokładnie o 09:50.

Audyt CSV zlicza osobno rozpoczęcie pobrania i publikację JWCD
po zakończeniu pobrania. Liczniki mogą się nakładać. Test na 24
cenach TGE z 14.11.2026 i syntetycznych znacznikach JWCD potwierdza
dopuszczenie danych na granicy 09:50 i odrzucenie doby z trzema
wierszami o nieprawidłowej chronologii.

Audyt CSV wskazuje osobno brakujące wiersze godzinowe i obecne okresy
UTC bez prawidłowej ceny Fix1 lub wartości JWCD. Test na cenach TGE
z 14.11.2026 i syntetycznym JWCD wykazuje trzy różne okresy dla
brakującego wiersza, pustej ceny oraz wartości JWCD NaN.

Audyt PK5 PostgreSQL w trybie tylko do odczytu wskazuje obecne
okresy UTC bez prawidłowej wartości JWCD. Test doby 25h rozróżnia
dwa wystąpienia lokalnej godziny 02:00: brak wiersza w pierwszym
i wartość NaN w drugim. Test integracyjny potwierdza, że snapshot
z kompletem okresów i brakującym JWCD nie przechodzi wyboru.

Audyt Fix1 w PostgreSQL wskazuje okresy z jawnym UTC, dla których
istnieje wiersz bez skończonej ceny. Test na cenach TGE z 14.11.2026
rozróżnia NULL, NaN oraz brak całego wiersza. Ocena kompletności
okresów i ocena kompletności cen pozostają odrębnymi wynikami.

Audyt PK5 w PostgreSQL zlicza osobno brakujące i błędne znaczniki
publikacji. Test obejmuje brak pola, NULL, pusty tekst, błędne typy
JSON oraz publikację dokładnie o D-1 09:50 Europe/Warsaw i minutę
później. Niepełne znaczniki nadal wykluczają snapshot z wyboru.

Audyt PK5 w PostgreSQL zlicza publikacje po zapisaniu snapshotu
osobno od publikacji po D-1 09:50 Europe/Warsaw. Brak czasu zapisu
daje wartość nieustaloną. Test doby 25h sprawdza oba liczniki,
równość na obu granicach oraz odrzucenie snapshotu.

Audyt PK5 w PostgreSQL wskazuje jawne okresy UTC z publikacją po
D-1 09:50 Europe/Warsaw. Test doby 25h ustawia publikację dokładnie
o 09:50 dla pierwszego wystąpienia 02:00 i sekundę później dla
drugiego. Raport wskazuje tylko drugi okres UTC.

Audyt PK5 w PostgreSQL wskazuje okresy UTC z istniejącym wierszem
i brakującym lub błędnym znacznikiem publikacji. Test doby 25h
rozróżnia brak całego wiersza, brak znacznika i błędny typ JSON.
Dwa wystąpienia 02:00 mają osobne klucze, a prawidłowa publikacja
dokładnie o D-1 09:50 Europe/Warsaw nie trafia na listę błędów.

Audyt Fix1 w PostgreSQL wskazuje jawne okresy UTC z pobraniem po
09:50 Europe/Warsaw w dniu dostawy, czyli po granicy wydania
prognozy na następną dobę. Test doby 25h odróżnia dwa wystąpienia
02:00 i potwierdza, że lista wykrytych spóźnień pozostaje dostępna,
gdy inny wiersz nie ma czasu pobrania.

Audyt Fix1 w PostgreSQL wskazuje okresy UTC, dla których brakuje
wiarygodnego czasu pierwszej obserwacji lub pobrania albo zapisano
pobranie przed pierwszą obserwacją. Test doby 25h rozróżnia
odwróconą chronologię pierwszego 02:00, spóźnione pobranie drugiego
02:00 i brak czasu pobrania w ostatnim okresie.

Audyt history.hourly w PostgreSQL podaje powtórzone etykiety,
etykiety poza zakresem liczby okresów danej doby oraz liczbę
wierszy bez etykiety. Test doby 25h obejmuje powtórzoną etykietę
2, etykiety 25 i 26 oraz NULL. Etykiety nie ustalają kluczy UTC.

Audyt history.hourly liczy odrębne etykiety oraz etykiety z parą
skończonych wartości JWCD i Fix1 według długości doby. Na
izolowanym PostgreSQL test sprawdza uwzględnienie etykiety 25
jesienią oraz pominięcie etykiety 24 w dobie 23h. Obie doby
pozostają bez potwierdzonego mapowania UTC.

Audyt history.hourly wskazuje etykiety z różnymi skończonymi
wartościami JWCD lub Fix1 w obrębie jednej doby. Test doby 25h
sprawdza konflikt JWCD przy etykiecie 2, konflikt Fix1 przy
etykiecie 3 oraz zgodne powtórzenie etykiety 25.

Audyt PostgreSQL podaje zakresy brakujących dat osobno dla PK5
i Fix1 we wspólnym przedziale kalendarzowym. Test obejmuje daty
obecne tylko w jednym źródle, odrzucone snapshoty PK5 oraz pusty
przedział z podanymi granicami. Odrzucony snapshot liczy się jako
obecny w inwentaryzacji.

Audyt PostgreSQL rozróżnia brak klucza pk5l-wp, nieprawidłowy
typ danych i zapisaną pustą tablicę PK5. Test doby 25h sprawdza
wszystkie przypadki i potwierdza, że istniejące rekordy pozostają
widoczne w inwentaryzacji, lecz nie tworzą kandydata JWCD.

Audyt Fix1 wskazuje okresy UTC, dla których zapisano pobranie
w chwili wydania prognozy na tę samą dobę (D-1 09:50 Europe/Warsaw)
lub wcześniej. Test doby 25h sprawdza pojedynczy okres na granicy
oraz przypadek z pobraniem wszystkich okresów o 09:50.

Audyt Fix1 w PostgreSQL wskazuje okresy UTC z różnymi skończonymi
cenami. Test na cenach TGE dla 14.11.2026 rozróżnia zgodne
powtórzenie od konfliktu ceny dla tego samego okresu UTC.

Audyt PK5 wskazuje okresy UTC z różnymi skończonymi wartościami
JWCD w jednym snapshotcie. Test doby 25h wykrywa konflikt w drugim
wystąpieniu 02:00. Pierwsze zawiera równoważne zapisy liczbowe
i NaN, a raport wskazuje je jako duplikat bez konfliktu wartości.

Audyt PK5 podaje powody odrzucenia godzinowego kandydata JWCD.
Raport uwzględnia chronologię, kompletność okresów UTC i poprawność
wartości JWCD. Test doby 25h sprawdza osobno brak kluczy UTC
i brak wartości JWCD oraz pustą listę powodów dla pełnej doby.

Audyt CSV uwzględnia w licznikach znaczników po D-1 09:50
Europe/Warsaw także wiersze z odwróconą chronologią JWCD.
Klasyfikacja wierszy nadal rozróżnia złą chronologię i spóźnioną
kompletną sekwencję. Test na cenach TGE z 14.11.2026 sprawdza
oba liczniki przy syntetycznych znacznikach JWCD.

Pobrano oficjalny HTML sesji TGE z 14.11.2026 dla dostawy
15.11.2026 o 11:19 Europe/Warsaw. Zapisano HTML w gzip, CSV
i manifest SHA-256. Parser Fixing I daje 24 ceny zgodne z niezależną
ekstrakcją XPath z tej samej odpowiedzi, w tym H01 763,14 PLN/MWh.
Test izolowanego PostgreSQL sprawdza INSERT i REPLAY bez duplikatów.

Ponowny import w izolowanym archiwum waliduje URL źródłowy zapisanej
paczki Fix1. Test na cenach TGE dla 15.11.2026 podmienia URL zapisanej
paczki na adres innej sesji. Dwie próby importu dają jeden wpis
kwarantanny i zachowują 24 zapisane ceny wraz z ich powiązaniami.

Ponowny import w izolowanym archiwum sprawdza format SHA-256
referencyjnego CSV zapisanej paczki Fix1. Test na cenach TGE dla
15.11.2026 używa 64 znaków spoza alfabetu szesnastkowego. Dwa
ponowienia tworzą jeden wpis kwarantanny i zachowują 24 ceny
z pierwotnymi powiązaniami.

Ponowny import w izolowanym archiwum sprawdza zgodność wersji
parsera zapisanej paczki z wersją zweryfikowanej paczki wejściowej.
Test na cenach TGE dla 15.11.2026 potwierdza REPLAY przy zgodnej
wersji i jeden wpis kwarantanny po dwóch próbach z obcą wersją.
Zapisane ceny i ich powiązania są zachowane.

Ponowny import w izolowanym archiwum zapisuje osobny powód
kwarantanny dla nieskończonych cen lub NaN. Test na cenach TGE dla
15.11.2026 sprawdza NaN, +Infinity i -Infinity, dwie próby bez
powielenia kwarantanny oraz REPLAY po wycofaniu zmian testowych.

Scoring w izolowanym PostgreSQL odrzuca NaN i obie nieskończoności
w zapisanych cenach Fix1 przed ponownym odczytem HTML. Test na cenach
TGE dla 15.11.2026 i syntetycznej prognozie potwierdza zachowanie
zapisanego wyniku i REPLAY po wycofaniu zmian testowych.

Scoring w izolowanym PostgreSQL porównuje zapisane okresy UTC Fix1
z pełnym kalendarzem dostawy przed odczytem HTML. Test potwierdza
odrzucenie nieskończonych znaczników, daty spoza zakresu i przesunięcia
o minutę oraz zachowanie wyniku i REPLAY po wycofaniu zmian testowych.

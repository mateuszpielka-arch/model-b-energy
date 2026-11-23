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

Importer manifestu Fix1 sprawdza nazwy obu plików dowodowych przed
odczytem HTML lub inicjalizacją bazy. Test na kopii manifestu TGE
dla 15.11.2026 obejmuje brak pola, błędne typy, pustą nazwę
i niedozwolony znak. Oryginalny manifest daje 24 okresy w dry-run.

Importer manifestu Fix1 wymaga kodu HTTP 200 jako liczby całkowitej
przed otwarciem plików. Test na kopii manifestu TGE dla 15.11.2026
obejmuje brak pola, błędne typy oraz kody 204, 429 i 503.
Oryginalny manifest daje 24 okresy w dry-run.

Importer manifestu sprawdza format obu sum SHA-256 przed otwarciem
plików HTML i CSV. Test na manifeście TGE dla 15.11.2026 obejmuje
brak pola, zły typ, długość i alfabet zapisu. Oryginalny manifest
przechodzi walidację 24 okresów z porównaniem obu sum do plików.

Importer Fix1 sprawdza adres sesji TGE przed otwarciem plików
dowodowych. Test na manifeście dostawy 08.11.2026 obejmuje
inną domenę, błędną ścieżkę, datę innej sesji i powtórzony
parametr daty. Oryginalny manifest przechodzi dry-run dla 24 okresów.

Importer manifestu dla standardowej doby sprawdza deklarację
24 okresów przed otwarciem plików. Test na manifeście TGE dla
08.11.2026 obejmuje brak pola, wartości 23 i 25 oraz błędne typy.
Parser nadal niezależnie sprawdza pełny zestaw cen godzinowych.

Importer manifestu wyznacza długość doby dostawy w Europe/Warsaw
przed odczytem HTML i CSV. Dla ścieżki archiwizacji 24h odrzuca
29.03.2026 (23 okresy) i 25.10.2026 (25 okresów), nawet gdy manifest
deklaruje 24. Test potwierdza brak próby otwarcia obu plików.

Parser Fixing I odrzuca cenę, której zapis liczbowy po konwersji
daje nieskończoność. Test na kopii oficjalnego HTML dla 02.11.2026
sprawdza oba znaki na zmodyfikowanej cenie H01. Oryginalna sesja
nadal zwraca 24 okresy, z ceną H01 równą 687,34 PLN/MWh.

Archiwum i scoring SHADOW porównują ceny z HTML jako Decimal
bez zaokrąglenia przez float. Test podmienia cenę H01 w kopii
oficjalnego HTML z 02.11.2026 i sprawdza odrzucenie referencji
różniącej się na czwartej pozycji po przecinku. Domyślne wywołania
parsera nadal zwracają float.

Parser Fixing I przekazuje wiersze zweryfikowanej tabeli RDN
bezpośrednio do walidacji cen, bez ponownego składania i parsowania
HTML. Test na zmodyfikowanej kopii oficjalnej sesji dla 15.11.2026
sprawdza zachowanie czwartego miejsca po przecinku po zapisie
w PostgreSQL i przy scoringu z referencyjnym CSV oraz bez niego.

Zapisano oficjalny HTML sesji TGE z 15.11.2026 dla dostawy
16.11.2026, pobrany o 22:19 Europe/Warsaw. Parser Fixing I zwraca
24 ceny zgodne z niezależną ekstrakcją XPath z tego HTML. Cena H01
to 471,34 PLN/MWh. Manifest wiąże HTML i CSV sumami SHA-256.
Test izolowanego PostgreSQL sprawdza INSERT i REPLAY bez duplikatów.

Scoring dla oficjalnych cen TGE z dostawy 16.11.2026 porównano
z niezależnym rachunkiem na ułamkach dla syntetycznej prognozy
650 PLN/MWh. Test izolowanego PostgreSQL sprawdza wszystkie cztery
metryki, brak zapisu mikrosekundę przed pobraniem cen oraz INSERT
i REPLAY dokładnie w chwili pobrania.

Audyt CSV zlicza poprawne znaczniki JWCD po D−1 09:50 Europe/Warsaw
także wtedy, gdy inne pole w tym samym wierszu jest puste lub błędne.
Sprzeczne wartości początku pobrania nie są uwzględniane w liczniku
`retrieved_at`. Test na cenach TGE z 16.11.2026 i syntetycznych
znacznikach JWCD potwierdza, że takie wiersze nie potwierdzają dostępności.

Audyt CSV podaje liczbę niepoprawnych znaczników JWCD osobno dla
publikacji, początku i końca pobrania. Pole jest liczone również
w wierszu z brakującym innym znacznikiem. Testy na syntetycznych
dobach 23h i 25h sprawdzają m.in. sprzeczne aliasy początku pobrania
i zachowanie dotychczasowej kwalifikacji całego wiersza.

Raport audytu ZIP pokazuje liczniki błędnych i spóźnionych znaczników
JWCD według pól oraz liczbę publikacji i początków pobrania po
zakończeniu pobrania. Test wykorzystuje ceny TGE z 16.11.2026
i syntetyczne znaczniki JWCD wokół D−1 09:50 Europe/Warsaw.
Ponowny odczyt archiwum z 04.11.2026 potwierdził brak wszystkich
wymaganych znaczników w 50 928 wierszach i 0 dób gotowych do backtestu.
Sprawdzono sumę SHA-256 ZIP przed audytem i po nim.

Audyt PostgreSQL wskazuje okresy UTC PK5 z publikacją po zapisaniu
snapshotu. Test doby 25h rozróżnia oba wystąpienia lokalnej godziny
02:00, odróżnia zapis snapshotu od granicy D−1 09:50 Europe/Warsaw
i sprawdza przypadki równości oraz braku czasu zapisu.

Audyt PostgreSQL wskazuje okresy UTC Fix1, w których
`retrieved_at_utc` jest późniejszy niż `first_seen_at_utc`.
Test doby 25h przypisuje ponowne pobranie do drugiego wystąpienia
02:00 oraz rozróżnia brak kolumny czasu od braku ponownych pobrań.

Zapis manifestu walidacyjnego publikuje gotowy JSON atomowo
i odrzuca zapis, gdy manifest już istnieje. Test dwóch równoczesnych
zapisów z manifestem sesji dla 16.11.2026 potwierdza jeden kompletny
wynik i usunięcie obu plików tymczasowych.

Importer walidacyjny odczytuje manifest w UTF-8 i odrzuca plik
większy niż 1 MiB przed odczytem HTML i CSV oraz przed pracą z bazą.
Test na kopii manifestu dostawy 16.11.2026 potwierdza poprawny tryb
dry-run przy dokładnie 1 MiB i odrzucenie po dodaniu jednego bajtu.

Archiwizacja testowa sprawdza zgodność HTML również wtedy, gdy
klucz wskazuje zapisany wcześniej pakiet referencyjny, z którego
nie pochodzą przyjęte ceny. Test na dostawie 16.11.2026 potwierdza
kwarantannę po zmianie HTML w takim pakiecie, bez nadpisania dowodów
lub cen i bez duplikowania wpisu w kwarantannie.

Ponowny import do izolowanej bazy sprawdza czas pobrania i URL
także dla dodatkowego pakietu dowodowego, z którego nie pochodzą
przyjęte ceny. Test na dostawie 16.11.2026 potwierdza kwarantannę
dla czasu równego D−1 09:50 Europe/Warsaw, nieskończonego czasu
i URL innej sesji, z zachowaniem cen i zapisanych metadanych.

Odpowiedź HTTP 200 z [sesji TGE 16.11.2026](https://tge.pl/energia-elektryczna-rdn?dateShow=16-11-2026)
z godz. 10:43:18 Europe/Warsaw zawierała `-` w polach ceny Fixing I
dla wszystkich 24 okresów dostawy 17.11.2026. Surowy HTML zachowano
jako fixture regresyjne. Ekstraktor referencji wskazuje teraz brak ceny
i konkretny okres. Test potwierdza odrzucenie odpowiedzi przez parser
oraz brak zapisu HTML i CSV przez ścieżkę pobierania cen.

Drugie pobranie [tej samej sesji TGE](https://tge.pl/energia-elektryczna-rdn?dateShow=16-11-2026)
o 11:34:27 Europe/Warsaw potwierdziło 24 ceny Fixing I dla 17.11.2026.
Zapisano HTML, CSV z okresami UTC i manifest z sumami SHA-256. Parser
zgadza się z niezależną ekstrakcją XPath z tej odpowiedzi. Cena H01 to
738,93 PLN/MWh. Test w izolowanym PostgreSQL sprawdza INSERT i REPLAY
oraz zachowanie czasu drugiego pobrania w zapisanym pakiecie.

Nowe wyniki scoringu SHADOW zapisują czas pobrania cen Fix1 w UTC
i wiążą go z sumą kontrolną wejścia. Test na sesji dla 17.11.2026
przesuwa czas pobrania o sekundę w izolowanym PostgreSQL i potwierdza
odrzucenie ponownego scoringu oraz zachowanie zapisanego wyniku.
Starsze rekordy można nadal odtworzyć bez zmiany ich formatu.

Scoring wskazuje, które znaczniki prognozy przekroczyły D−1 09:50
Europe/Warsaw. Test cen TGE dla 17.11.2026 potwierdza odrzucenie
publikacji i pobrania spóźnionych o mikrosekundę oraz przyjęcie obu
znaczników na granicy. MAE, MAPE, WAPE i bias dla syntetycznej
prognozy 650 PLN/MWh porównano z niezależnym rachunkiem na ułamkach.

Audyt CSV wskazuje zaobserwowane okresy UTC bez terminowych
znaczników JWCD oraz bez znacznika Fix1 po D−1 09:50 Europe/Warsaw.
Test odróżnia brak dowodu czasowego od braku całego wiersza.
Obejmuje ceny TGE dla 17.11.2026 i syntetyczne doby 23h i 25h,
w tym oba wystąpienia 02:00 przy jesiennej zmianie czasu.

Audyt CSV wskazuje osobno okresy UTC, w których powtórzone wiersze
mają różne skończone ceny Fix1 lub różne JWCD. Kontrola obejmuje ceny
TGE dla 17.11.2026 i dobę 25h z syntetycznymi danymi. Równe wartości
o różnym zapisie pozostają duplikatami bez konfliktu wartości.

Raport walk-forward z CSV podaje liczbę wszystkich dni w badanym
zakresie oraz rozkład na dni bez rekordu, odrzucone w audycie,
dopuszczone bez wyniku i ocenione przez wszystkie modele. Test
na cenach TGE dla 17.11.2026 bez JWCD odróżnia dzień odrzucony
od dwóch brakujących dni. Test syntetyczny sprawdza też wspólną
próbę z dobą 25h i zakres ograniczony do tej doby.

Ponowne pobranie [sesji TGE z 16.11.2026](https://tge.pl/energia-elektryczna-rdn?dateShow=16-11-2026)
o 17:26:30 Europe/Warsaw dało inny surowy HTML i identyczny CSV
24 cen Fixing I dla 17.11.2026. Zachowano oba HTML i ich czasy pobrania.
Test izolowanego PostgreSQL sprawdza dwa ponowne importy późniejszego
HTML, dwa pakiety dowodowe i jeden zestaw 24 cen powiązany z pierwszym
pobraniem, bez kwarantanny.

Raport importu do izolowanego PostgreSQL podaje sumę SHA-256 i czas UTC
pobrania przypisanego do przyjętych cen. Przy ponownym imporcie późniejszego
HTML z 17.11.2026 raport zachowuje skrót złożonego pakietu i wskazuje
wcześniejsze pobranie jako źródło cen. Pole pozostaje puste w trybie
dry-run oraz przy kwarantannie.

Test scoringu dla 17.11.2026 importuje dwa rzeczywiste pobrania TGE
w obu kolejnościach. W każdej kolejności ocena jest możliwa dokładnie
od czasu pobrania pakietu, z którego zapisano ceny. Mikrosekundę
wcześniej baza pozostaje bez wyniku. Ponowne obliczenie zachowuje
pierwotny czas pobrania i sumę kontrolną zapisanego wyniku.

Walk-forward sprawdza przed scoringiem, czy każdy model udokumentował
czas dostępności danych dla wszystkich dni swojego okna 30D, 60D lub
90D. Brak dnia, duplikat albo data spoza okna wykluczają dobę docelową
ze wspólnego rankingu. Test usuwa, powiela lub przesuwa poza okno dowód
czasowy OLS 60D i potwierdza, że scoring tego dnia się nie rozpoczyna.

Walk-forward odrzuca dobę docelową, gdy model zwróci wadliwy dowód
czasowy treningu. Raport wskazuje model oraz błędne pole, jeśli data
dowodu jest poprawna. Test OLS 60D obejmuje błędną datę, brak czasu Fix1
i znacznik pobrania JWCD bez strefy. Druga doba zachowuje komplet
ocen wszystkich modeli.

Raport CSV walk-forward pokazuje pokrycie danych w badanym zakresie
osobno dla zaobserwowanych dób 23h, 24h i 25h. Test zawiera ocenioną
dobę 25h i doby 24h dopuszczone bez wyniku. Osobny przypadek z cenami
TGE z 17.11.2026 sprawdza liczenie doby odrzuconej przez brak JWCD.

Przy braku dni treningowych raport walk-forward podaje pokrycie CSV
oddzielnie dla całych okien 30D, 60D i 90D, także gdy wykonanie kończy
się na pierwszym modelu. Liczniki pokazują dni nieobecne, odrzucone
przez audyt i przyjęte przez audyt. Test wykorzystuje dwa rzeczywiste
dni Fix1, z których jeden nie ma JWCD, i sprawdza wszystkie trzy okna.

Raport luk treningowych podaje, ile dni przyjętych przez audyt CSV
miało ceny Fix1 dostępne dopiero po D−1 09:50 Europe/Warsaw dla
doby ocenianej. Test z cenami TGE i syntetycznymi znacznikami
odróżnia czas równy granicy od spóźnienia o mikrosekundę.

Raport okien treningowych pokazuje datę i znacznik UTC dla każdej
doby z cenami Fix1 dostępnymi po granicy prognozy. Test doby 25h
z kompletną syntetyczną historią sprawdza odrzucenie rankingu oraz
wskazanie spóźnionej doby sprzed 31 dni tylko w oknach 60D i 90D.

Audyt CSV i importer referencji Fix1 wskazują linię, w której zaczyna
się wadliwy rekord, również gdy pole w cudzysłowie zajmuje kilka linii.
Test na kopii referencji TGE z 17.11.2026 potwierdza odrzucenie
uszkodzonego CSV mimo zgodnej sumy SHA-256 w manifeście.

Audyt ZIP sprawdza typ liczby wierszy, liczby dni i mapy sum
SHA-256 przed odczytem arkusza. Test odrzuca m.in. wartość
logiczną zamiast liczby dni i liczbę zmiennoprzecinkową zamiast
liczby wierszy. Bajty badanego ZIP pozostają identyczne.

Parser odrzuca tabelę RDN z dwoma atrybutami id, niezależnie
od ich kolejności. Test na kopii HTML TGE z cenami dla 17.11.2026
sprawdza, że taki układ blokuje przygotowanie pakietu do archiwizacji.
Oryginalna odpowiedź nadal daje komplet 24 cen Fixing I.

Parser blokuje archiwizację, gdy komórka tabeli RDN ma powtórzony
colspan lub rowspan albo colspan bez wartości. Test na kopii HTML
TGE dla 17.11.2026 sprawdza odrzucenie wszystkich trzech wariantów
i poprawny odczyt 24 cen z oryginalnej odpowiedzi.

Parser odrzuca wiersze tabeli RDN osadzone poza thead, tbody lub tfoot.
Test na kopii HTML TGE dla 17.11.2026 dodaje przed thead wiersz H01
z inną ceną i potwierdza odrzucenie pakietu. Oryginał daje 24 ceny.

Parser odrzuca godzinowe wiersze RDN umieszczone w stopce tabeli.
Test na kopii HTML TGE dla 17.11.2026 sprawdza, że stopka z wierszem
podsumowania zachowuje 24 ceny, a wpis H01 w stopce blokuje pakiet.

Parser odrzuca komórki RDN umieszczone bezpośrednio w thead, tbody
lub tfoot. Test na kopii oficjalnego HTML TGE dla 17.11.2026
potwierdza blokadę przygotowania pakietu w każdym z tych przypadków.
Oryginalna odpowiedź daje komplet 24 cen Fixing I.

Parser odrzuca samozamykające się znaczniki sekcji, wierszy i komórek
w tabeli RDN. Test na kopii oficjalnego HTML TGE dla 17.11.2026
sprawdza cztery warianty w stopce i potwierdza zgodność kompletu 24 cen
po dodaniu zwykłego podsumowania z łamaniem linii.

Parser odrzuca znaczniki script i style wewnątrz komórek tabeli RDN.
Test na oficjalnym HTML TGE dla 17.11.2026 sprawdza, że tekst ukryty
w tych znacznikach nie może zostać przyjęty jako cena H01. Cena w
zwykłym znaczniku span nadal daje komplet tych samych 24 cen.

Niezależny ekstraktor XPath odrzuca script i style w komórkach wiersza
godzinowego Fixing I. Test na kopiach HTML TGE dla 17.11.2026 potwierdza,
że narzędzie pobierające nie zapisuje HTML ani CSV dla obu wariantów.
Referencja z ceną w zwykłym span nadal zgadza się z oryginałem.

Audyt CSV zachowuje prawidłowy okres UTC w inwentaryzacji, gdy jego
etykieta godzinowa jest błędna. Taka doba nie kwalifikuje się do
backtestu. Test na cenach TGE dla 17.11.2026 i syntetycznych danych
JWCD rozróżnia błędną etykietę od brakującego okresu UTC.

Audyt CSV liczy publikacje i początki pobrania JWCD późniejsze niż
koniec pobrania także wtedy, gdy trzeci znacznik jest pusty lub błędny.
Test na cenach TGE z 17.11.2026 i syntetycznych znacznikach sprawdza
obie pary oraz brak rozstrzygnięcia przy nieznanym końcu pobrania.
Klasyfikacja dostępności całego wiersza pozostaje taka sama.

Audyt CSV wskazuje okresy UTC, w których publikacja lub początek
pobrania JWCD nastąpiły po końcu pobrania. Test doby 25h rozróżnia oba
wystąpienia 02:00 i potwierdza, że wiersz z błędnym kluczem UTC liczy się
w statystyce błędów bez przypisania do okresu dostawy.

Audyt CSV pokazuje okresy UTC osobno dla publikacji, początku i końca
pobrania JWCD po D−1 09:50 Europe/Warsaw. Testy obejmują doby 23h
i 25h oraz ceny TGE z 13.11 i 16.11.2026. Rozróżniają znaczniki
dokładnie na granicy, brakujące znaczniki czasu i sprzeczne aliasy.

Audyt CSV wskazuje okresy UTC z błędnym znacznikiem JWCD osobno
dla publikacji, początku i końca pobrania. Testy dób 23h i 25h
sprawdzają publikację bez strefy, sprzeczne aliasy początku i błędny
koniec pobrania w wierszu z brakującym znacznikiem publikacji.

Test izolowanego PostgreSQL uruchamia równocześnie import oficjalnego
pobrania dla 17.11.2026 i jego syntetycznej rewizji ceny H01. Sprawdza
jeden pełny zestaw 24 cen, dwa zachowane HTML i kwarantannę przegranego
importu. Ponowienie obu importów zachowuje ceny i jeden wpis kwarantanny.

Test scoringu w izolowanym PostgreSQL uruchamia równocześnie dwie
różne prognozy o tej samej tożsamości. Sprawdza jeden zapis wyniku,
odrzucenie konkurencyjnego wejścia, REPLAY zwycięskiej prognozy
i zachowanie skrótu oraz metryk po ponowieniu przegranej prognozy.
Prognozy mają syntetyczne błędy +5 i +6 PLN/MWh względem cen fixture.

Walk-forward sprawdza dostępność wszystkich cen treningowych przed
uruchomieniem scoringu któregokolwiek modelu. Rozszerzony test OLS 60D
potwierdza, że dowód dostępności po D−1 09:50 blokuje scoring całej doby,
również wcześniejszego w kolejności OLS 30D. Ranking pozostaje pusty.

Walk-forward porównuje okresy UTC prognoz wszystkich sześciu modeli
z kalendarzem dostawy przed pierwszym scoringiem. Test usuwa jeden okres
z prognozy dni podobnych level i potwierdza brak scoringu całej doby oraz
pusty wspólny ranking. Dane i prognoza w tym teście są syntetyczne.

Test walk-forward sprawdza granicę dostępności treningowego Fix1
dla każdego okna OLS 30D, 60D i 90D. Dowód dokładnie o D−1 09:50
dopuszcza wspólną ocenę sześciu modeli. Spóźnienie o mikrosekundę
w dowolnym oknie blokuje wszystkie wywołania scoringu tej doby.
Test wykorzystuje syntetyczną historię i znaczniki czasu.

Pobrano oficjalny HTML sesji TGE z 17.11.2026 dla dostawy 18.11.2026
o 22:11:47 Europe/Warsaw. Zapisano HTML gzip, CSV z okresami UTC i
manifest SHA-256. Test porównuje 24 ceny parsera z ekstrakcją XPath
tej samej odpowiedzi oraz sprawdza INSERT i REPLAY w izolowanym PostgreSQL.
H01 wynosi 619,88, a H24 589,64 PLN/MWh.

Scoring oficjalnych cen dla 18.11.2026 sprawdzono względem niezależnego
rachunku na ułamkach dla syntetycznej prognozy 650 PLN/MWh. Test PostgreSQL
odrzuca ocenę mikrosekundę przed pobraniem Fix1 i przyjmuje ją dokładnie
w chwili pobrania. Ścieżki z referencyjnym CSV i bez niego odtwarzają
ten sam zapis MAE, MAPE, WAPE i bias bez powielenia wyniku.

Walk-forward sprawdza wersje wszystkich modeli przed scoringiem doby.
Brak wersji w prognozie dni podobnych level wyklucza tę dobę z porównania
i pozwala kontynuować ocenę następnej. Test na syntetycznej historii
potwierdza sześć wywołań scoringu wyłącznie dla poprawnej doby.

Walk-forward przekazuje brak pola cen prognozy do istniejącej walidacji
kolekcji cen. Wadliwa doba jest odrzucana z powodem w raporcie, a kolejna
może zostać oceniona. Test usuwa prices z prognozy dni podobnych level
i potwierdza wspólny ranking sześciu modeli tylko dla poprawnej doby.
Historia i prognozy w teście są syntetyczne.

Walk-forward sprawdza datę dostawy zadeklarowaną przez każdy model przed
scoringiem. Test zmienia wyłącznie datę prognozy dni podobnych level przy
poprawnych okresach UTC i identyfikatorze snapshotu. Wadliwa doba jest
wykluczana, a następna otrzymuje wspólny ranking sześciu modeli.
Test wykorzystuje syntetyczną historię.

Test walk-forward sprawdza znaczniki publikacji, początku i zakończenia
pobrania treningowego JWCD w obu wariantach dni podobnych, shape i level.
Znaczniki dokładnie na granicy D−1 09:50 doby treningowej dopuszczają
wspólną ocenę. Spóźnienie o mikrosekundę blokuje scoring wszystkich modeli.
Test wykorzystuje syntetyczną historię i dowody czasowe.

Walk-forward odrzuca wynik modelu, który nie jest obiektem z polami
prognozy. Test zwraca None z dni podobnych shape i z mediany w pierwszej
dobie. Każdy przypadek daje powód odrzucenia tej doby oraz wspólną ocenę
sześciu modeli w następnej. Historia w teście jest syntetyczna.

Test OLS 30D/60D/90D sprawdza publikację treningowego JWCD niezależnie
od znaczników pobrania. Publikacja dokładnie o D−1 09:50 doby treningowej
dopuszcza wspólny ranking. Publikacja mikrosekundę późniejsza blokuje
scoring całej doby także przy terminowych znacznikach pobrania.
Historia i zmieniane dowody czasowe w teście są syntetyczne.

Raport walk-forward wskazuje brakujące i nadmiarowe okresy UTC prognozy
wykluczonej ze wspólnego scoringu. Testy obejmują brak ostatniego okresu
w dobie 24h oraz przesunięty pierwszy okres doby 23h. Ranking uwzględnia
wyłącznie kompletne doby. Historia w testach jest syntetyczna.

Test wspólnego rankingu obejmuje błędny okres UTC w obu dobach zmiany
czasu, 23h i 25h. Jesienią zastępuje drugie lokalne 02:00 kluczem spoza
doby i sprawdza dokładne wskazanie brakującego oraz nadmiarowego okresu.
W obu przypadkach wszystkie modele są oceniane tylko na poprawnej dobie.
Historia i prognozy w teście są syntetyczne.

Walk-forward zamienia przerwanie odczytu iteratora cen prognozy przez
RuntimeError lub OSError na błąd walidacji. Test walk-forward przerywa strumień prognozy
po pierwszej cenie i potwierdza brak częściowego scoringu, odrzucenie
wadliwej doby oraz wspólny ranking następnej. Dane są syntetyczne.

Pobranie sesji TGE z 18.11.2026 o 09:11:38 Europe/Warsaw zwróciło
HTTP 200 i znak "-" we wszystkich 24 cenach Fixing I dla 19.11.2026.
Zachowano surowy HTML gzip oraz metadane URL, czasu i SHA-256.
Test sprawdza odrzucenie przez parser oraz brak zapisu paczki HTML/CSV
przez narzędzie pobierające ceny.

Raport walk-forward wskazuje daty bez dowodu czasowego treningu, gdy
okno modelu jest niekompletne. Rozszerzony test OLS 60D sprawdza listę
wszystkich 60 dat przy braku dowodów oraz pojedynczą brakującą datę.
Brak dowodu nadal blokuje scoring całej doby. Historia jest syntetyczna.

Drugie pobranie sesji TGE z 18.11.2026 o 11:18:01 Europe/Warsaw
zawiera 24 ceny Fixing I dla 19.11.2026. Zapisano HTML gzip, referencyjny
CSV z okresami UTC i manifest SHA-256. Test wiąże oba pobrania z tym samym
URL i sprawdza zgodność parsera z ekstrakcją XPath oraz INSERT/REPLAY
w izolowanym PostgreSQL. H01 wynosi 623,98, a H24 765,41 PLN/MWh.

Test scoringu oficjalnych cen dla 19.11.2026 porównuje MAE, MAPE, WAPE
i bias z niezależnym rachunkiem na ułamkach dla syntetycznej prognozy
600, 610, ... 830 PLN/MWh. Sprawdza brak wyniku przed pobraniem cen,
odrzucenie spóźnionych publikacji/pobrania prognozy oraz INSERT/REPLAY
na granicy dostępności Fix1, z CSV i bez niego.

Test granicy zapisu archiwum używa rzeczywistego HTML bez cen dla
19.11.2026 i zgodnej z nim sumy SHA-256, połączonych z cenami późniejszego
pobrania. Writer odrzuca taką spreparowaną paczkę przed blokadą doby.
Test na izolowanym PostgreSQL sprawdza pustą i zapełnioną bazę, zachowanie
wszystkich trzech tabel oraz REPLAY prawidłowej paczki.

Audyt CSV wskazuje okresy UTC z brakującym znacznikiem publikacji,
początku/końca pobrania JWCD oraz dostępności Fix1, osobno dla pól.
Test obejmuje doby 23h i 25h oraz rzeczywiste ceny TGE dla 19.11.2026
z syntetycznymi znacznikami JWCD. Odróżnia brak znacznika od braku
całego wiersza i zachowuje wykluczenie niegotowej doby z backtestu.

Audyt CSV wskazuje okresy UTC z czasem dostępności Fix1 równym
D−1 09:50 lub wcześniejszym. Testy rozróżniają granicę i czas wcześniejszy
w dobie 24h oraz lokalizują drugie jesienne 02:00 w dobie 25h.
Wiersz z błędnym kluczem UTC liczy się bez przypisania do okresu.
Dane w tych testach są syntetyczne.

Audyt CSV wskazuje okresy UTC z niepoprawnym czasem dostępności Fix1.
Testy dób 23h i 25h oraz cen TGE dla 19.11.2026 sprawdzają czas bez
strefy, błędny tekst i wiersz bez prawidłowego klucza UTC. Licznik
uwzględnia wszystkie wadliwe znaczniki, a lista okresów tylko znane klucze.
Znaczniki czasu i JWCD w testach są syntetyczne.

Test scoringu cen TGE dla 19.11.2026 wymusza błąd PostgreSQL po INSERT
wyniku. Sprawdza wycofanie wyniku i zachowanie cen oraz surowych dowodów.
Po usunięciu testowego triggera ta sama prognoza daje INSERT i REPLAY.
Całość działa w izolowanym PostgreSQL z syntetyczną prognozą 650 PLN/MWh.

Test kwarantanny na bazie oficjalnego HTML dla 19.11.2026 wymusza błąd
PostgreSQL po INSERT konfliktu. Potwierdza wycofanie nowych dowodów oraz
zachowanie zaakceptowanych cen i wcześniejszego scoringu. Ponowienie zapisuje
jedną kwarantannę z osobnym HTML konfliktu, bez zmiany cen ani wyniku.
Kolejne próby nie powielają zapisów, a kwarantanna blokuje REPLAY scoringu.
Konflikt ceny H01, prognoza 650 PLN/MWh i awaria triggera są syntetyczne;
wszystkie operacje testu dotyczą wyłącznie izolowanego PostgreSQL.

Walidator read-only shadow_tge_candidate zwraca teraz listę 24 okresów UTC
z etykietami TGE i dokładnymi cenami, zachowując dotychczasowy słownik prices.
Test porównuje cały raport z referencyjnym CSV oficjalnego pobrania dla
19.11.2026, sprawdza SHA-256 surowych bajtów i serializację JSON. Dodatkowe
testy odrzucają wcześniejsze rzeczywiste pobranie bez cen i obie doby DST
2026 (23/25h). Raport potwierdza wyłącznie układ i kompletność; nie stanowi
dowodu dostępności o 09:50 ani zgody na zapis archiwum lub scoring.

Raport walk-forward podaje requested_periods i excluded_periods oraz
coverage_by_hours: żądane, wspólne i wykluczone doby/okresy osobno dla
23/24/25h. Pokrycie liczy unikalne żądane daty również bez paczki danych,
bez dopisywania ich do rankingu. Test łączy brak paczki doby 25h, brak
kwalifikowanego JWCD doby 23h i poprawną dobę 24h: 72 żądane okresy,
24 wspólne, 48 wykluczonych. Sprawdza też powtórzoną datę, pełne pokrycie
i pusty backtest. Historia w tych testach jest syntetyczna.

Test scoringu oficjalnych cen dla 19.11.2026 zajmuje rzeczywistą blokadę
advisory doby w osobnym połączeniu izolowanego PostgreSQL. Zarówno przed
pierwszym INSERT wyniku, jak i przed REPLAY wymusza lock_timeout 100ms.
Sprawdza SQLSTATE 55P03, niezmienność czterech tabel, zachowanie krótszego
limitu sesji i przydatność tego samego połączenia po rollbacku. Po zwolnieniu
blokady ponowienie daje odpowiednio INSERT/REPLAY i jeden wynik.
Prognoza 650 PLN/MWh i zajęcie blokady są syntetyczne; produkcji nie dotyczy.

Opcjonalne porównanie XLSB z CSV wskazuje issue_periods_utc osobno dla
brakujących i powielonych kluczy skoroszytu oraz rozbieżności JWCD/Fix1.
Liczniki pozostają zachowane. Testy sprawdzają mapowanie do UTC przed i po
jesiennej zmianie czasu. Nowy test używa rzeczywistych cen TGE dla 19.11.2026
oraz syntetycznych wierszy skoroszytu i usterek; nie jest nowym audytem
rzeczywistego XLSB. Istniejący test ZIP potwierdza zachowanie pliku bajtowo.
Okresy pominiętych dób DST nadal nie są mapowane z niezweryfikowanych etykiet.

Późne pobranie oficjalnej sesji 18.11 dla dostawy 19.11.2026 o
23:11:43 Europe/Warsaw zwróciło nowy HTML (425987 bajtów), ale identyczny
referencyjny CSV wszystkich 24 cen względem pobrania o 11:18. Zapisano osobno
HTML gzip, CSV i manifest w tge_delivery_2026-11-19_late. SHA-256 HTML:
2ff5b758c36378a8cb48e314e7cc00bac26fbeb8c5d9e47d9616bf62a6e61c03.
Test porównuje parser i ekstrakcję XPath z referencją. Test PostgreSQL
sprawdza obie kolejności importu: INSERT/REPLAY, dwa niezmienne dowody,
24 ceny wskazujące pierwsze zaakceptowane pobranie i brak kwarantanny.
Jest to ponowne pobranie tej samej sesji, nie nowy niezależny dzień obserwacji.

Porównanie historii Fix1 z referencją pokazuje price_differences dla każdego
rozbieżnego okresu UTC: reference_price_pln_mwh, history_price_pln_mwh oraz
history_minus_reference_pln_mwh. Różnica ma znak historia minus referencja;
wartości są tekstami Decimal i nie zależą od precyzji ustawionej przez wywołującego.
Test na referencji TGE dla 19.11.2026 wprowadza syntetyczne różnice +0.01/-0.01,
sprawdza dokładne kwoty przy precyzji 2 i aktywnej pułapce Inexact oraz
niezmienność wejścia. Test ZIP sprawdza również różnicę przy cenach ujemnych.
Nie zmieniono istniejących cen ani dowodów; to rozszerzenie raportu read-only.

Raport CSV dodaje calendar_target_coverage_by_hours, obejmujące również
całkowicie brakujące daty w żądanym zakresie. Osobno dla 23/24/25h podaje
kalendarzowe doby i okresy, brakujące doby oraz okresy ocenione i nieocenione.
Dotychczasowe observed_target_coverage_by_hours pozostaje zachowane.
Testy pustych trzydniowych zakresów wokół obu zmian czasu potwierdzają
71 okresów wiosną i 73 jesienią. Test rzeczywistych cen TGE z 17.11 bez JWCD
rozróżnia jedną dobę wykluczoną przez audyt i dwie nieobecne, łącznie 72
nieocenione okresy. Braki nie są dopisywane do rankingu ani historii.

Samodzielna mediana sprawdza teraz obiekt kolekcji, obiekty wszystkich
czterech prognoz, daty dostawy, niepuste tekstowe model_version i wspólny
niepusty tekstowy target_snapshot_id przed odczytem cen któregokolwiek członka.
Nie przyjmuje już bool/liczb/list/słowników jako wersji lub identyfikatora.
Brak prices zgłasza błąd walidacji zamiast KeyError. Testy sprawdzają każdy
z czterech modeli i iteratory cen, których nie wolno odczytać przy wadliwych
metadanych. Dane są syntetyczne. Skład i obliczanie poprawnej mediany bez zmian.

Samodzielna mediana zamienia przerwanie iteratora cen przez RuntimeError lub
OSError na ValueError wskazujący konkretnego członka, z zachowaniem przyczyny.
Nie zwraca częściowej prognozy. Test przerywa strumień po pierwszej cenie
dla każdego z czterech modeli i dób 23/24/25h, potwierdza brak wejścia
w obliczanie mediany i poprawne ponowienie z kompletnymi danymi.
Wszystkie dane i przerwania strumieni w tym teście są syntetyczne.

Walk-forward izoluje wyjątki arytmetyki Decimal przy przygotowaniu prognoz
lub scoringu doby: raportuje numeric_error i etap, wyklucza dobę ze wspólnego
rankingu i kontynuuje kolejne. Test odtworzył wcześniejsze przerwanie całego
przebiegu przez Overflow w OLS 30D dla skończonego syntetycznego JWCD 9E999999.
Po poprawce doba 23h jest wykluczona przed scoringiem, a niezależna doba 25h
zachowuje identyczne prognozy i wyniki oraz sześć wywołań scoringu.
Nie zmieniono reguł dostępności danych, modeli ani parametrów obliczeń.

Opcjonalny benchmark FROZEN 90D w offline walk-forward izoluje również
błędy Decimal podczas oceny syntetycznego rekordu porównawczego.
Test odtworzył przerwanie całego raportu przez Overflow przy sumie błędów
prognozy 9E999999. Po poprawce tylko ta para benchmarku jest wykluczona
z podaniem przyczyny; pełne 49 okresów i wszystkie wyniki SHADOW pozostają
identyczne z przebiegiem bez benchmarku. Wcześniejsza poprawna para 24h
pozostaje w rankingu porównawczym. Test potwierdza niezmienność rekordów.
Historia i rekordy benchmarku są syntetyczne; produkcyjnego FROZEN nie zmieniano.

Pobranie URL sesji 19.11.2026 o 06:12:00 Europe/Warsaw dla dostawy 20.11
zwróciło HTTP 200, ale tabela RDN zawierała tylko 24 ceny dla 19.11.
Zapisano oryginalny HTML gzip i metadane w tge_delivery_2026-11-20_prepublication.
SHA-256 HTML: 15b2ae7452ffe23ac736d7eca7a0c26e950767859cbf47cf8b4034f9e632cdb6.
Test sprawdza zgodność poprzedniej doby z zapisaną referencją TGE, odrzucenie
żądanej doby przez parser oraz brak zapisu paczki HTML/CSV przez capture.
HTTP 200 i poprawny URL sesji nie wystarczają do przyjęcia cen dostawy.
Nie przypisano wcześniejszych cen do nowej doby ani nie wymyślono czasu publikacji.

Ekstraktor referencyjny przy braku godzin żądanej dostawy podaje daty
rzeczywiście znalezionych godzin w RDN. Test na oficjalnym HTML pobranym
19.11 o 06:12 wskazuje żądaną dostawę 20.11 i znalezioną 19.11.
Nowy test PostgreSQL próbuje przyjąć ten HTML z prawidłowym SHA-256,
syntetycznie późniejszym czasem pobrania i cenami przesuniętymi na UTC 20.11.
Writer odrzuca paczkę przed blokadą doby, zachowuje wszystkie trzy tabele
w pustej i zapełnionej bazie oraz umożliwia REPLAY prawidłowej paczki.
Zmodyfikowane metadane i klucze są syntetyczne; HTML pozostaje oryginalny.

## Checkpoint 2026-11-19 — rzeczywisty kandydat PK5 przed 09:50

Zapisano surowy JSON gzip i metadane publicznego PSE pk5l-wp dla dostawy
20.11.2026: 24 wiersze, publikacja 19.11 o 08:12:42.328 Europe/Warsaw,
początek pobrania 08:15:56.761896 i koniec 08:16:03.547940. SHA-256 JSON:
492b6aac88c8aafa77c767aecc2ffd4e02c8b97d513ea2a7a6a22f7fef4c3709.

Nowy czysty adapter shadow_pk5_snapshot.py przyjmuje wyłącznie kompletne
doby 24h z tego endpointu i filtrem business_date. Sprawdza etykiety godzin,
lokalne końce okresów i jawne plan_dtime_utc, zgodność lokalnej publikacji
z publication_ts_utc, kompletność i unikalność UTC, a następnie istniejącą
bramkę publication/retrieved/completed <= D−1 09:50. JWCD pochodzi wyłącznie
z fcst_gen_unit_stor_prov. Doby DST są blokowane do osobnej weryfikacji.

Cztery testy obejmują rzeczywisty zapis, dokładną granicę i mikrosekundę
spóźnienia oraz syntetyczne braki, konflikty dat/okresów, paginację i duplikaty
pól JSON. Adapter nie czyta bazy ani sieci i nie zapisuje prognoz. To jeden
rzeczywisty kandydat JWCD, nie komplet historycznych okien 30/60/90D.

Drugie rzeczywiste pobranie PK5 dla dostawy 20.11.2026 zakończono 19.11
o 09:15:43.960574 Europe/Warsaw; publikacja 09:12:42.315, nadal przed 09:50.
Zachowano osobno JSON gzip i metadane (SHA-256:
dc2a104bc21897d58fff74b60cf7f5e59546b911ec3b75fbacc13762289aab3b).
Test obu kolejności wybiera nową publikację i nie modyfikuje obserwacji.

Poprawiono identyfikator adaptera PK5: obejmuje teraz hash bajtów oraz początek
i koniec pobrania w UTC. Sam hash treści zlewał dwie różne obserwacje tych samych
bajtów, co uruchamiało blokadę powielonych ID w selektorze. Test odtworzył
kolizję na rzeczywistych bajtach z syntetycznie zmienionym czasem, a po poprawce
potwierdza wybór późniejszego pobrania i stabilne ID dla tej samej obserwacji,
także przy równoważnym zapisie czasu w strefie Warsaw. Reguły cutoff i konfliktów
publikacji nie zostały poluzowane. To dwie obserwacje jednej doby, nie nowe okna treningu.

Adapter PK5 sprawdza teraz kolejność i termin rozpoczęcia/zakończenia pobrania
przed dekodowaniem JSON. Testy potwierdzają brak wywołania dekodera dla początku
lub końca pobrania mikrosekundę po 09:50 oraz odwróconej kolejności czasów.
Zachowano też rzeczywiste pobranie dla 20.11 zakończone 19.11 o 10:12:58.490960
Europe/Warsaw (publikacja 10:03:21.316), SHA-256:
98d6dca193c83caeb70caf44325671b04936a4800abbda87ec86520f1cd22c7c.
Test tego zapisu potwierdza odrzucenie po czasie pobrania bez dekodowania cen.
Plik jest wyłącznie negatywnym dowodem walidacyjnym, nie kandydatem do prognozy
09:50. Dwa wcześniejsze rzeczywiste snapshoty pozostają niezmienione.

Pobranie oficjalnej sesji TGE 19.11 dla dostawy 20.11.2026 zakończono
o 11:17:54.057511 Europe/Warsaw. Zapisano HTML gzip, CSV z UTC i manifest;
SHA-256 HTML: 6ab99a8520bc0414f84c6be6f3a6fc71536b28e4640d680a26ddc231b34f3b66.
H01: 716.72, H24: 655.39 PLN/MWh. Test porównuje wszystkie 24 ceny parsera
z ekstrakcją XPath i zachowanym CSV oraz wiąże URL z wcześniejszą odpowiedzią
bez wierszy żądanej dostawy. Wspólny test potwierdza identyczne klucze UTC
rzeczywistego PK5 pobranego o 09:15 i Fix1 pobranego po cutoff, bez użycia
późnego PK5. Test PostgreSQL sprawdza INSERT/REPLAY, niezmienne dowody i 24 ceny.
To walidacja źródeł i ich osi czasu, nie wynik prognozy ani historyczny ranking.

Test scoringu dla 20.11.2026 łączy rzeczywisty PK5 pobrany o 09:15 z
rzeczywistym Fix1 pobranym o 11:17. Prognoza kontrolna to wyłącznie syntetyczny
stały wzór JWCD / 20, nie model uczony ani rzeczywiście wydana prognoza.
Niezależny rachunek Fraction według etykiet PSE i referencyjnego CSV daje:
MAE 331.522083333333; MAPE 35.005439476079%; WAPE 35.172766527800%;
bias -331.522083333333 PLN/MWh. Test PostgreSQL sprawdza te metryki,
brak zapisu mikrosekundę przed pobraniem Fix1, INSERT dokładnie w chwili
jego dostępności oraz REPLAY z CSV i bez niego przy jednym trwałym wyniku.
To dowód poprawności scoringu i czasu dostępności, nie ocena skuteczności modeli.

Nowy czysty moduł shadow_source_history.py przygotowuje kanoniczne wiersze
historii CSV z oryginalnych bajtów PK5 i Fix1 oraz referencji CSV z UTC.
Ponownie waliduje paczkę actual, hash i zgodność referencji z surowym HTML,
a następnie bramkę PK5. Zachowuje rzeczywiste publication_ts i czasy pobrania
JWCD oraz konserwatywny czas dostępności Fix1 z zakończenia pobrania.
Nie czyta plików/bazy/sieci i nie dopisuje prognoz ani brakujących dat.

Cztery testy obejmują rzeczywistą dobę 20.11, round-trip przez CSV i audyt,
odrzucenie zmienionych bajtów oraz zmian ceny/UTC nawet przy dopasowanym hashu
referencji i spóźnionego PK5. Audyt dopuszcza 24 pary jako jedną dobę, ale
walk-forward zachowuje pusty ranking i jawnie wykazuje brak 30/60/90 dób CSV.
To przygotowanie danych do backtestu, nie dowód gotowości historycznych okien.

Dodano jawny lokalny eksport CLI:
`python shadow_source_history.py --pk5-capture katalog/capture.json katalog/manifest.json`.
Weryfikuje metadane HTTP/URL, rozmiar i SHA-256 PK5 oraz istniejący manifest
Fix1, po czym używa sprawdzonego połączenia źródeł. Wypisuje kompletny CSV
na stdout dopiero po walidacji całości. Nie łączy się z siecią lub bazą,
nie odczytuje DATABASE_URL i nie zmienia plików wejściowych.
Test subprocess na rzeczywistych źródłach 20.11 potwierdza zgodność eksportu
z funkcją i niezmienność plików. Błędny hash, HTTP, URL odpowiedzi i powielony
znacznik pobrania kończą się kodem 2 i pustym stdout, bez częściowego CSV.

Naprawiono obcinanie precyzji czasu w adapterze PK5: datetime.fromisoformat
przyjmował syntetyczną publikację 09:50:00.0000001 jako 09:50:00 i dopuszczał
ją na granicy cutoff. Adapter wymaga teraz pełnej daty i czasu z sekundami
oraz najwyżej sześcioma cyframi ułamkowymi, zarówno w polach UTC, jak i
lokalnej publikacji. Znaczniki date-only nie są uzupełniane domyślną północą.
Test odtworzył wcześniejsze błędne przyjęcie, potwierdza odrzucenie nadmiarowej
precyzji i niepełnego czasu oraz akceptację dokładnego 09:50:00.000000.
Rzeczywiste zapisane formaty PSE nadal przechodzą walidację. Zmiany dotyczą SHADOW.

Naprawiono analogiczne obcinanie precyzji w CLI odczytującym metadane pobrania
PK5. Nowy parse_capture_timestamp wymaga pełnego czasu z jawną strefą i najwyżej
sześcioma cyframi ułamkowymi. CLI używa go dla retrieved_at_utc i
retrieval_completed_at_utc, zamiast bezpośredniego datetime.fromisoformat.
Test subprocess odtworzył wcześniejszy eksport CSV po 09:50:00.0000001 dla
obu pól. Po poprawce nadmiarowa precyzja daje kod 2 i pusty stdout, natomiast
dokładne 09:50:00.000000 pozostaje dopuszczalne. Zmienione czasy są syntetyczne;
rzeczywiste pliki źródłowe i granica 09:50 pozostają zachowane.


## Checkpoint 2026-11-19 — precyzja dostępności Fix1 w manifeście

Loader manifestu wymaga teraz pełnego czasu pobrania Fix1 z sekundami, jawną
strefą i najwyżej sześcioma cyframi ułamkowymi. Nie pozwala datetime.fromisoformat
obciąć dostępności actual ani dopisać brakujących sekund. Reguła sprawdzana jest
przed odczytem HTML/CSV i przed inicjalizacją lub zapisem do bazy.

Dwa nowe testy na kopii rzeczywistej paczki TGE 20.11 odtworzyły wcześniejsze
przyjęcie syntetycznego czasu 10:17:54.0575119 UTC oraz eksport CSV z obciętym
znacznikiem. Po poprawce loader odrzuca także dziewięć cyfr ułamka i czas bez
sekund przed I/O dowodów/zapisem; subprocess CLI zwraca kod 2 i pusty stdout.
Oryginalny czas 10:17:54.057511+00:00 nadal dopuszcza 24 okresy. Lokalnie 9/9
testów test_shadow_source_history przeszło. Zmienione znaczniki są syntetyczne;
oryginalne pliki, archiwa, prognozy i konfiguracja produkcji pozostają bez zmian.


## Checkpoint 2026-11-19 — wybór kompletnego pobrania PK5 do historii

Dodano czyste API prepare_history_rows_from_captures w shadow_source_history.
Przyjmuje jawną niepustą listę/krotkę źródłowych pobrań jednej doby, ponownie
waliduje każdy JSON, UTC, publikację i początek/koniec pobrania względem 09:50,
a następnie korzysta z istniejącego selektora publikacji i konfliktów. Eksportuje
całą wybraną obserwację z jej ID i czasami, bez mieszania okresów różnych pobrań.
Dotychczasowe API pojedynczego pobrania deleguje do tej samej ścieżki.
CLI nadal przyjmuje jedno capture.json; obsługa wielu plików nie jest tu wdrożona.

Cztery nowe testy potwierdzają wybór rzeczywistej publikacji PK5 z 09:12,
pobranej o 09:15 dla 20.11, w obu kolejnościach z wcześniejszą obserwacją.
Syntetyczne późniejsze pobranie starszych oryginalnych bajtów nie cofa publikacji.
Konflikt wartości przy tej samej publikacji i powielony identyfikator blokują
wynik; spóźniony kandydat nie jest po cichu pomijany. Nieprawidłowa kolekcja
lub zestaw pól jest odrzucany. Dane wejściowe pozostają niezmienione.
Lokalnie 13/13 testów test_shadow_source_history przeszło. Konflikt i przesunięcia
czasów są syntetyczne; to nadal jedna doba danych, bez okien treningowych ani rankingu.


## Checkpoint 2026-11-19 — wiele plików PK5 w lokalnym eksporcie CLI

CLI shadow_source_history.py przyjmuje teraz powtarzalny --pk5-capture:
`python shadow_source_history.py --pk5-capture pierwsze/capture.json --pk5-capture drugie/capture.json fix1/manifest.json`.
Każdy plik przechodzi walidację metadanych, SHA-256, daty i źródłowego JSON,
a wybór pełnego pobrania odbywa się przez prepare_history_rows_from_captures.
Nie ma domyślnego pomijania wcześniejszych argumentów ani wadliwych kandydatów.
CSV trafia na stdout dopiero po walidacji całej paczki; pojedynczy plik nadal działa.

Dwa nowe testy subprocess obejmują oba rzeczywiste pobrania PK5 dla 20.11,
obie kolejności argumentów i bajtową niezmienność wejścia. Drugi test sprawdza
obie kolejności dla złego hashu, HTTP, daty, spóźnienia, duplikatu i konfliktu
tej samej publikacji: kod 2, pusty stdout i właściwa przyczyna. Przed poprawką
odtworzono ignorowanie pierwszego argumentu i eksport starszej publikacji,
gdy jej plik był ostatni. Usterki i konflikt są syntetyczne; oryginalne źródła
pozostają zachowane. Lokalnie 15/15 testów test_shadow_source_history przeszło.
To obsługa jednej doby i jej rewizji, nie nowe historyczne okna ani ranking.


## Checkpoint 2026-11-19 — precyzja czasu w audycie CSV i dopuszczeniu walk-forward

Parser czasu _stamp w read-only shadow_history_audit wymaga teraz pełnego
tekstu daty/czasu z sekundami, jawną strefą i najwyżej sześcioma cyframi ułamka.
Zachowuje wsparcie aware datetime oraz równoważnych zapisów UTC/Warsaw.
Nie obcina ułamków ani nie dopisuje brakujących sekund. To bramka offline;
produkcja i istniejące archiwa nie są modyfikowane.

Trzy nowe testy odtworzyły wcześniejsze przyjęcie syntetycznego czasu
09:50:00.0000001 jako dokładnego cutoff. Osobno dla publication_ts, retrieved_at
i retrieval_completed_at w dobach 23/24/25h audyt wskazuje wadliwy znacznik
i właściwy okres UTC, a evaluate_csv_rows wyklucza dobę bez dopisania rankingu.
Testy odrzucają również nadmiarową precyzję actual_available_at i klucza UTC
oraz czas bez sekund; poprawny dokładny cutoff nadal przechodzi.
Sprawdzono niezmienność wejścia. Lokalnie 3/3 testy nowego modułu
test_shadow_history_timestamp_precision przeszły. Dane są syntetyczne,
nie stanowią nowej walidacji historycznej dostępności ani mapowania źródłowego DST.


## Checkpoint 2026-11-19 — raport dowodów wyboru źródeł historii

Dodano czyste prepare_history_report_from_captures oraz opcję --format json
lokalnego CLI shadow_source_history.py. Domyślny CSV i API zwracające wiersze
zachowują dotychczasowy wynik. JSON zawiera te same wiersze oraz cutoff,
wybrane snapshot ID, metadane/hash obu pobrań PK5, oznaczenie wybranego,
powody odrzucenia starszych kandydatów i hashe/referencję/parser/czas pobrania
Fix1. Kolejność kandydatów w raporcie jest deterministyczna.

Dwa nowe testy na zapisanych rzeczywistych pobraniach 20.11 porównują cały
raport w obu kolejnościach wejścia, SHA-256 z oryginalnymi bajtami, wybraną
publikację 09:12 i pominiętą starszą obserwację. Potwierdzają JSON round-trip,
niezmienność wejścia oraz subprocess --format json, który przy duplikacie
zwraca kod 2 i pusty stdout. Lokalnie 17/17 testów test_shadow_source_history
przeszło. Raport jest read-only i nie zapisuje plików ani bazy; hashe i spójność
nie uwierzytelniają niezależnie podanych czasów pobrania. Nie dodano dób
treningowych, prognoz ani rankingu.


## Checkpoint 2026-11-19 — atomowe dodawanie doby do rankingu SHADOW

Walk-forward przygotowuje teraz sumy wspólnego rankingu na odłączonej kopii:
ogółem, według 23/24/25h, typu dnia oraz lokalnej godziny/fold. Dopiero po
powodzeniu wszystkich okresów i sześciu modeli publikuje sumy oraz liczniki.
DecimalException na tym etapie wyklucza całą dobę ze stage=aggregation i
numeric_error, bez częściowego rankingu, po czym przebieg kontynuuje kolejne dni.
Parowanie opcjonalnego benchmarku odbywa się dopiero po udanej agregacji SHADOW.

Nowa regresja odtworzyła wcześniejsze przerwanie całego przebiegu przez Overflow
wstrzyknięty po częściowej aktualizacji ostatniego modelu doby 25h. Po poprawce
wcześniejsza i następna doba 24h zachowują dokładnie te same accepted oraz
rankingi ogólny, 23/24/25h, weekday/weekend i lokalnej godziny jak przebieg bez
wadliwej doby. Wykluczono dokładnie 25 okresów; historia pozostaje niezmieniona.
Lokalnie 67/67 testów test_shadow_walkforward przeszło. Historia i wstrzyknięta
awaria są syntetyczne; produkcyjnych prognoz i FROZEN nie zmieniano.


## Checkpoint 2026-11-19 — atomowe sumowanie par benchmarku offline

Opcjonalne porównanie z dostarczonymi lokalnie rekordami FROZEN przygotowuje
teraz sumy obu modeli na odłączonej kopii. Sumy i paired_days aktualizowane są
dopiero po powodzeniu całej pary. Błąd Decimal w agregacji trafia do excluded_days
tylko tej pary; wcześniejsze sumy oraz pełny ranking SHADOW zostają zachowane,
a przebieg ocenia kolejne doby. Zmiana dotyczy wyłącznie read-only benchmarku.

Nowa regresja odtworzyła wcześniejsze przerwanie całego przebiegu przez Overflow
wstrzyknięty po częściowym sumowaniu drugiego modelu pary w dobie 25h.
Po poprawce raport SHADOW jest dokładnie identyczny z przebiegiem bez benchmarku:
trzy doby, 73 okresy. Ranking par ma wyłącznie wcześniejszą i następną dobę 24h,
łącznie 48 okresów, i odpowiada referencji bez wadliwej pary. Rekordy i historia
pozostają niezmienione. Lokalnie 68/68 testów test_shadow_walkforward przeszło.
Historia, rekordy porównawcze i awaria są syntetyczne. Produkcyjnych modeli,
FROZEN i istniejących archiwów nie zmieniano.


## Checkpoint 2026-11-20 — procenty rankingu bez przepełnienia wyniku pośredniego

Ranking oblicza MAPE/WAPE z dwoma dodatkowymi pozycjami wykładnika wyłącznie
na czas mnożenia przez 100 i dzielenia. Precyzja pozostaje bez zmian, a wynik
końcowy ponownie podlega pierwotnemu kontekstowi Decimal. Usuwa to fałszywy
Overflow dla skończonych sum i procentów mieszczących się w dotychczasowym
zakresie. Nie zmieniono definicji MAE/MAPE/WAPE/bias ani wspólnej próby.

Trzy nowe testy obejmują naturalny Overflow przy syntetycznych dużych sumach,
który przed poprawką przerywał _rank mimo końcowego WAPE=50 oraz skończonego
MAPE. Potwierdzają dokładne metryki, niezmienność sum i kontekstu wywołującego,
zachowanie Overflow dla rzeczywiście zbyt dużego procentu oraz None przy
zerowych mianownikach i jawne zero_actual_periods. Lokalnie 3/3 nowe testy
i 68/68 testów test_shadow_walkforward przeszło. To testy granic arytmetyki
na syntetycznych sumach, nie wyniki modeli ani nowe dane historyczne.


## Checkpoint 2026-11-20 — gotowość historii w raporcie źródłowym JSON

Raport prepare_history_report_from_captures i CLI --format json zawiera teraz
history_audit dla dokładnie tych wierszy, które eksportuje. Używa istniejącego
audytora read-only: pokazuje kompletność par, bramki dostępności, najdłuższy
ciąg gotowych dób oraz braki do pierwszego porównania 30/60/90D. Wynik CSV
pozostaje taki sam. Podsumowanie dotyczy wyłącznie tej paczki, nie zewnętrznych
archiwów ani całego projektu.

Rozszerzony test na rzeczywistych źródłach 20.11 potwierdza 24 poprawne pary
Fix1/JWCD, jedną dobę bez problemów audytu, ciąg długości 1, brak kandydatów do
porównania i deficyt 30/60/90 dób. Test subprocess porównuje pełny JSON wraz
z audytem w obu kolejnościach pobrań PK5. Lokalnie 17/17 testów
test_shadow_source_history przeszło. Nie dopisano brakujących dat, prognoz
ani rankingu; sprawdzenie spójności metadanych nie zastępuje ich uwierzytelnienia.


## Checkpoint 2026-11-20 — wspólne obliczanie procentów scoringu i rankingu

Przeniesiono pomocnicze obliczanie procentu do shadow_scoring._percentage
i użyto go również w score_day dla MAPE/WAPE. Ranking korzysta z tej samej
funkcji. Rozszerzenie wykładnika dotyczy tylko mnożenia przez 100 i dzielenia;
precyzja oraz ograniczenie końcowego wyniku pozostają takie same.

Nowa regresja odtworzyła naturalny Overflow scoringu pełnych dób 23/24/25h
dla syntetycznych cen forecast=1E999997, actual=2E999997, mimo że końcowe
MAPE/WAPE wynoszą 50%. Po poprawce dokładne MAE=1E999997 i bias=-1E999997
oraz wszystkie liczniki okresów są poprawne. Test zachowuje wejście i kontekst
wywołującego; drugi potwierdza błąd dla rzeczywiście niemieszczącego się procentu.
Lokalnie przeszło 18/18 testów scoringu, 3/3 procentów rankingu i 68/68 walk-forward.
To graniczne testy arytmetyki, nie wyniki skuteczności ani nowe dane historyczne.
Produkcyjnych prognoz, konfiguracji i archiwów nie zmieniano.


## Checkpoint 2026-11-20 — ścisły czas dowodów treningowych przed scoringiem

Walk-forward używa teraz wspólnego parsera shadow_scoring._evidence_timestamp
przy ponownym sprawdzaniu training_timing. Wymaga pełnego czasu z sekundami,
jawną strefą i najwyżej sześcioma cyframi ułamka, bez obcinania precision.
Istniejący audyt CSV korzysta z tego samego parsera pod dotychczasową nazwą _stamp.
Reguły cutoff, modeli i ich okien pozostają bez zmian.

Nowa regresja odtworzyła błędne dopuszczenie 0.1 mikrosekundy po cutoff dla
OLS 30/60/90D i similar shape/level. Każdy model sprawdzono osobno dla czasu
dostępności treningowego Fix1 oraz publikacji, rozpoczęcia i końca pobrania JWCD:
łącznie 20 wariantów syntetycznie zmienionego training_timing. Po poprawce
wadliwa doba jest wykluczona przed scoringiem ze wskazaniem modelu, daty i pola,
a następna zachowuje identyczny pełny wynik. Historia pozostaje niezmieniona.
Lokalnie przeszło 69/69 testów walk-forward i 3/3 testy precyzji czasu audytu.
To testy dowodów treningowych na syntetycznej historii, nie nowe źródła ani ranking modeli.


## Checkpoint 2026-11-20 — rzeczywiste późne pobranie Fix1 dla 20.11

Ponowne pobranie oficjalnego URL sesji 19.11 zakończono 20.11.2026 o
04:20:41.999315 Europe/Warsaw (HTTP 200). Zachowano osobno surowy HTML gzip,
CSV z jawnymi UTC i manifest w tge_delivery_2026-11-20_late. HTML ma 424909 bajtów,
SHA-256 5cc0193cd1969f6bc447a8d6ae3837955f2f761c00ca6f8cca1d740b7c6d04e6.
CSV wszystkich 24 cen jest bajtowo identyczny z pobraniem 19.11 o 11:17:
SHA-256 e336b1d4a87d6385d62e97c0e1478fb837a33efbe81e7128753a6008e00ad72e.
H01=716.72, H24=655.39 PLN/MWh.

Nowy test porównuje parser, niezależny XPath, referencyjny CSV, hashe i przyjęcie
paczki. Lokalnie przeszedł 1/1. Nowy test PostgreSQL obejmuje obie kolejności
importu: INSERT/REPLAY, dwa niezmienne dowody, 24 ceny wskazujące pierwsze
zaakceptowane pobranie, ponowny REPLAY obu i brak kwarantanny. Ten test działa
wyłącznie na izolowanym PostgreSQL CI. To nowe rzeczywiste pobranie tej samej
sesji, nie dodatkowa doba ani dowód dostępności JWCD przed cutoff. Wcześniejsze
pliki, archiwa i produkcja pozostają zachowane.


## Checkpoint 2026-11-20 — rzeczywisty PK5 przed cutoff dla dostawy 21.11

Pobrano publiczne PSE pk5l-wp z filtrem business_date=2026-11-21: HTTP 200,
24 wiersze, publikacja 20.11 o 05:13:06.844 Europe/Warsaw, rozpoczęcie pobrania
05:14:53.205545 i zakończenie 05:14:59.091799, przed D−1 09:50. Zachowano
oryginalny JSON gzip oraz capture.json w pse_pk5_delivery_2026-11-21.
JSON ma 18624 bajty, SHA-256:
58bf3545c5c2aaec8a3c0a9355efa9bec5c36c6e67870f7e975824963b314935.

Dwa nowe testy sprawdzają pełny adapter i selekcję tej doby, publikację/czasy,
początek i koniec UTC oraz wartości JWCD z fcst_gen_unit_stor_prov.
H01=8655.00000, H24=6983.00000 MW. Obserwacje rzeczywistych dostaw 20.11 i 21.11
pozostają oddzielone w obu kolejnościach: obca doba ma WRONG_DELIVERY_DATE.
Wejście pozostaje niezmienione. Lokalnie 12/12 testów test_shadow_pk5_snapshot
przeszło. To druga rzeczywista doba kandydata JWCD; nie dodano ceny Fix1 dla
21.11, prognozy, rankingu ani okien treningowych. Późniejsze rewizje PK5 mogą
być osobnymi obserwacjami; tego pobrania nie nadpisano.


## Checkpoint 2026-11-20 — odpowiedź TGE przed publikacją dostawy 21.11

Oficjalny URL sesji 20.11 pobrany o 06:16:45.733107 Europe/Warsaw zwrócił
HTTP 200, ale tabela RDN zawierała wyłącznie 24 ceny dostawy 20.11 i zero
wierszy 21.11. Zachowano oryginalny HTML gzip i capture.json osobno w
tge_delivery_2026-11-21_prepublication, bez tworzenia referencji cen dla 21.11.
HTML: 387471 bajtów, SHA-256:
d58c035787c5db16a5b114846b4d357d5b82b743aa86082a5a1aea15a1a67975.

Nowy test sprawdza hashe, daty wszystkich godzin RDN, zgodność cen poprzedniej
doby z referencją 20.11 oraz odrzucenie dostawy 21.11 przez parser i read-only
validate_candidate. Narzędzie capture nie zapisuje częściowej paczki i podaje
żądaną/znalezioną datę. Drugi nowy test subprocess łączy rzeczywisty PK5 dla
21.11 z manifestem Fix1 20.11: oba formaty CSV/JSON zwracają kod 2 i pusty stdout.
Lokalnie przeszedł 1 test nowego pobrania oraz 18/18 testów eksportu historii.
To rzeczywisty negatywny dowód układu przed publikacją, nie nowe ceny ani prognoza.
Poprzednich dowodów i produkcji nie zmieniano.


## Checkpoint 2026-11-20 — druga rzeczywista rewizja PK5 dla dostawy 21.11

Zachowano osobno nowszy JSON gzip i capture.json w
pse_pk5_delivery_2026-11-21_second. Publikacja 20.11 o 07:03:47.124
Europe/Warsaw, początek pobrania 07:17:50.784033, koniec 07:17:57.987761,
HTTP 200, 24 wiersze, przed cutoff D−1 09:50. Surowy JSON ma 18624 bajty,
SHA-256 c4b87119b2cb2c82d1d3a9cd985d79710c8222c0108e770b4dd083f6902248eb.

Względem wcześniejszej rzeczywistej publikacji zmieniły się wszystkie 24
wartości JWCD; H01=8735.00000, H24=6970.00000 MW. Nowy test wybiera całe
nowsze pobranie w obu kolejnościach, wskazuje starsze jako
SUPERSEDED_JWCD_PUBLICATIONS, sprawdza cutoff, hashe oraz niezmienność obu
obserwacji. Lokalnie 13/13 testów test_shadow_pk5_snapshot przeszło.
To dwie rewizje jednej doby, nie dodatkowy dzień treningowy. Nie zapisano
prognozy ani ceny Fix1 21.11; wcześniejsze źródła pozostają zachowane.


## Checkpoint 2026-11-20 — jawne narzędzie walidacyjnego pobrania PK5

Dodano tools/capture_pk5_validation.py:
`python -m tools.capture_pk5_validation --day YYYY-MM-DD --output NOWY_KATALOG`.
Narzędzie wykonuje jeden jawny GET publicznego pk5l-wp z filtrem business_date.
Sprawdza zwykłą dobę 24h i cutoff przed siecią, HTTP 200 bez przekierowania,
limit 8 MiB i pełny Content-Length, a następnie istniejący adapter PK5.
Dopiero po walidacji zapisuje gzip oryginalnych bajtów i capture.json z SHA-256,
rzeczywistym początkiem/końcem pobrania oraz snapshot ID. Nie pobiera sekretów,
nie zapisuje bazy ani prognoz i nie uruchamia harmonogramów.

Zapis jest wyłączny: ponowienie nie nadpisuje dowodów, a błąd zapisu metadanych
usuwa właśnie utworzony raw, zachowując wcześniejszy capture.json. Trzy nowe
testy z emulowanym HTTP i zegarem na oryginalnych bajtach drugiego pobrania PK5
21.11 potwierdzają zgodność wszystkich metadanych i treści, brak plików przy
spóźnieniu, złym HTTP/URL, uciętej lub nadmiernej odpowiedzi i niepełnym JSON,
brak żądania przy późnym starcie, ochronę ponowienia i wycofanie raw.
Lokalnie 3/3 testy oraz uruchomienie CLI --help przeszły. W tym kroku nie
wykonano nowego pobrania źródła ani nie zmieniono produkcyjnych collectorów.


## Checkpoint 2026-11-20 — rzeczywiste uruchomienie narzędzia PK5 przed 09:50

Uruchomiono nowe CLI capture_pk5_validation dla dostawy 21.11 na publicznym
PSE. HTTP 200; publikacja 20.11 o 09:12:56.224 Europe/Warsaw, początek pobrania
09:13:37.534340, koniec 09:13:47.559972, przed cutoff 09:50. CLI samo zwalidowało
i zapisało 24 wiersze, gzip oraz capture.json ze snapshot ID w
pse_pk5_delivery_2026-11-21_third. Surowy JSON: 18624 bajty, SHA-256
4f0202160c8035e2b7d43095cf807a20bfa357ae8ad4965a466eab607209cb5a.
H01=8571.00000, H24=6846.00000 MW.

Nowy test sprawdza zgodność ID z metadanymi CLI, dokładną publikację,
chronologię, 24 wartości i wybór tego całego pobrania we wszystkich sześciu
kolejnościach trzech rzeczywistych obserwacji. Obie starsze publikacje są
wskazane jako SUPERSEDED_JWCD_PUBLICATIONS, bez zmiany wejścia.
Lokalnie 14/14 testów test_shadow_pk5_snapshot przeszło. To rzeczywiste
jednorazowe pobranie walidacyjne, nie uruchomienie produkcyjnego collectora
ani prognozy 09:50. Nadal nie dodano Fix1 dla 21.11 ani kolejnej doby treningu.


## Checkpoint 2026-11-20 — pełne etykiety 21.11 bez opublikowanych cen Fix1

Oficjalny URL sesji 20.11 pobrany o 10:19:00.593702 Europe/Warsaw zwrócił
HTTP 200 i już 24 etykiety godzin dostawy 21.11, ale wszystkie ceny Fixing I
były oznaczone '-'. Zachowano oryginalny HTML gzip i capture.json w
tge_delivery_2026-11-21_unpriced. HTML ma 258957 bajtów, SHA-256:
9e5f092b2dcebda28c1290fd248335c40dab005a5aad2203136802ee2024b9c7.
Nie utworzono referencji cen ani manifestu gotowej paczki.

Nowy test porównuje hash, liczbę bajtów i wszystkie 24 brakujące ceny,
potwierdza odrzucenie przez parser i brak plików z narzędzia capture.
Lokalnie przeszedł 1/1. Drugi test na izolowanym PostgreSQL próbuje związać
prawdziwy nieopublikowany HTML z syntetycznie przesuniętymi cenami 20.11:
sprawdza odrzucenie przed blokadą, niezmienność trzech tabel w pustej i
zapełnionej bazie oraz REPLAY prawidłowej wcześniejszej paczki.
To kolejny rzeczywisty stan strony przed cenami, a nie ceny Fix1 21.11,
prognoza ani powód zmiany istniejących archiwów.


## Checkpoint 2026-11-20 — rzeczywisty kompletny Fix1 dostawy 21.11

Narzędzie capture_tge_validation pobrało oficjalny URL sesji 20.11 z kompletem
24 cen Fixing I o 11:14:16.158611 Europe/Warsaw. Zapisano osobno oryginalny HTML
gzip, CSV z UTC i manifest w tge_delivery_2026-11-21. HTML: 280139 bajtów,
SHA-256 a2eb6068abbca961fcba56f17a9cca5f183b1d890fd0066aa7831e2109faa9e5.
SHA-256 CSV: 5c19435fcfe1e883b16b860bf2beec71a3a17f06e5cb9833595ade98097fb708.
H01=783.16, H24=552.65 PLN/MWh. Czas pobrania jest konserwatywną dostępnością
actual, nie domniemanym czasem publikacji TGE.

Trzy nowe testy sprawdzają parser vs niezależny XPath i referencję 24 cen/UTC,
połączenie rzeczywistych źródeł z PK5 21.11 pobranym o 09:13 oraz archiwizację.
Dwie sparowane doby 20/21.11 dają 48 unikalnych okresów i ciąg gotowości 2,
ale brakuje 29/59/89 dób do pierwszego porównania 30/60/90D: ranking nadal pusty.
Lokalnie dwa testy źródeł przeszły; test PostgreSQL jest przeznaczony dla CI
i sprawdza INSERT/REPLAY, 24 niezmienne ceny, oryginalny HTML i brak kwarantanny.
Nie zmieniono wcześniejszych dowodów braku cen ani produkcji. Nie wydano
prognozy i nie zastąpiono porannego raportu.


## Checkpoint 2026-11-20 — scoring kontrolny rzeczywistych źródeł 21.11

Dodano niezależny rachunek Fraction łączący etykiety okresów surowego PK5
pobranego o 09:13 z oficjalnym referencyjnym CSV Fix1 21.11. Prognoza kontrolna
to wyłącznie syntetyczny stały wzór JWCD/20, bez uczenia i bez rzeczywistego wydania.
Wynik kontrolny: MAE 209.985416666667 PLN/MWh; MAPE 34.502647436342%;
WAPE 35.376391638237%; bias -209.985416666667 PLN/MWh; 24 okresy niezerowe.

Nowy test czystego scoringu sprawdza zgodność czterech metryk z oraclem
Fraction i utrwalonymi wartościami kontrolnymi. Nowy test izolowanego PostgreSQL
sprawdza brak wyniku 1 mikrosekundę przed pobraniem actual, INSERT dokładnie
w chwili dostępności, te same metryki oraz REPLAY z referencją i bez niej:
jeden trwały wynik, niezmienione ceny i brak kwarantanny. Lokalnie przeszły
3 testy źródeł/scoringu; dwa testy PostgreSQL wymagają CI.
To walidacja mechanizmu scoringu na realnych źródłach, nie ocena skuteczności
OLS/dni podobnych ani zamiennik porannej prognozy.


## Checkpoint 2026-11-20 — kwarantanna blokuje także wcześniej zapisany scoring

Dodano regresję izolowanego PostgreSQL dla pełnego cyklu rzeczywistych źródeł
21.11: przyjęcie oryginalnego Fix1, zapis kontrolnego scoringu JWCD/20, następnie
konflikt jednej ceny H01 (+1 PLN/MWh). Konflikt jest jawnie syntetyczny:
zmienione kopie HTML/CSV przechodzą normalną walidację paczki; nie przedstawiono
ich jako kolejnej publikacji TGE i nie zmieniono oryginalnych fixture.

Test wymaga zachowania oryginalnych cen, dowodu i całego zapisanego wyniku,
utrwalenia osobnego HTML/hash referencji konfliktu oraz jednej kwarantanny.
Po konflikcie ponowny scoring jest odrzucany zarówno dla starego, jak i nowego
forecast_id, z referencją CSV i bez niej. Ponowienia obu paczek pozostają
QUARANTINE i nie zmieniają żadnej z czterech tabel; prawidłowa paczka nie usuwa
kwarantanny. Nie zmieniono kodu produkcyjnego ani reguł archiwizacji/scoringu.

Lokalnie 3 testy źródeł/scoringu przeszły; 3 testy PostgreSQL pominięto z braku
lokalnej bazy. Dodatkowo wykonano przygotowanie syntetycznego konfliktu aż do
granicy połączenia: walidacja i dokładna różnica jednego okresu przeszły.
Pełną regresję bazy uruchamia istniejący CI na izolowanym PostgreSQL 16.
To test niezmienności i blokady wyniku po konflikcie, nie nowa doba historii
ani ocena skuteczności modeli.


## Checkpoint 2026-11-20 — zgodność deklarowanego snapshot ID przy eksporcie historii

CLI shadow_source_history sprawdza teraz opcjonalne snapshot_id z capture.json
względem ID wyliczonego przez istniejący adapter PK5 z oryginalnych bajtów oraz
dokładnego początku/końca pobrania. Wcześniej deklarowane ID było pomijane.
Błędne, puste, null lub nienapisowe ID blokuje cały eksport przed stdout.
Starsze metadane bez tego pola pozostają obsługiwane; zasady publication_ts,
retrieved_at, końca pobrania i cutoff D−1 09:50 pozostają bez zmian.

Nowa regresja używa rzeczywistego pobrania PK5 21.11 o 09:13 i pełnego Fix1
tej dostawy. Sprawdza poprawne ID oraz cztery błędne wartości w obu formatach:
CSV i JSON. Poprawne wejście eksportuje 24 wiersze z identycznym ID, błędne
zwraca kod 2 i pusty stdout; oryginalne źródła pozostają niezmienione.
Lokalnie przeszło 19/19 testów test_shadow_source_history, również starsze
pobrania bez snapshot_id. Sprawdzenie wiąże deklarację z treścią, nie uwierzytelnia
niezależnie czasów HTTP. Nie dodano danych historycznych, prognoz ani wdrożenia.


## Checkpoint 2026-11-20 — wielodniowa kolekcja zweryfikowanych źródeł

Dodano czystą funkcję prepare_history_collection(days=...) przyjmującą listę
lub krotkę kompletnych paczek: kandydaci PK5, batch Fix1, raw i referencja.
Każda paczka przechodzi istniejące pełne sprawdzenie źródeł oraz selekcję
publication_ts/retrieved_at/końca pobrania względem własnego D−1 09:50.
Wynik porządkuje doby, zachowuje osobne raporty dowodów, łączy wiersze i liczy
wspólny history_audit. Duplikat dostawy lub błędna paczka odrzuca całą kolekcję.
Funkcja nie wykonuje I/O, nie zwraca częściowego wyniku i nie zmienia wejścia.

Trzy nowe testy na rzeczywistych źródłach 20/21.11 sprawdzają obie kolejności,
48 unikalnych UTC, identyczne raporty dzienne, serializację JSON, ciąg gotowości 2,
braki 29/59/89 dób do pierwszego porównania oraz przyjęcie obu dni przez
walk-forward z zerem wspólnych okresów i pustym rankingiem. Spóźniona paczka
jest odrzucana w obu kolejnościach, duplikaty i błędny kształt wejścia również;
oryginalne dane pozostają niezmienione. Lokalnie przeszło 3/3 nowych testów.
To API do składania już zweryfikowanych dób; nie dodano historii, CLI ani prognozy.
Produkcja, FROZEN i istniejące archiwa pozostają bez zmian.


## Checkpoint 2026-11-20 — jawny wielodniowy eksport CLI bez częściowych wyników

Dodano shadow_source_collection.py. Wywołanie:
`python shadow_source_collection.py --day MANIFEST_20 CAPTURE_20 [CAPTURE_20B] --day MANIFEST_21 CAPTURE_21 --format json`.
Domyślnie eksportuje CSV; JSON zachowuje raporty dobowych dowodów i wspólny
audyt. Kolejność grup nie zmienia chronologii wierszy. Dla jednej doby można
podać kilka kandydatów PK5; selekcja nadal wybiera całe pobranie po publikacji.

Wydzielono load_history_day ze starszego CLI, zachowując dotychczasowe kontrole
hashy, rozmiarów, HTTP/URL, dat, pełnych czasów, opcjonalnego snapshot_id oraz
referencji Fix1. Oba CLI używają tego samego loadera. Nowe CLI odczytuje wyłącznie
jawnie wskazane lokalne pliki i przygotowuje całą kolekcję oraz tekst wyjściowy
przed stdout. Nie czyta bazy, sekretów, sieci ani harmonogramów.

Trzy nowe testy subprocess obejmują rzeczywiste źródła 20/21.11: oba porządki,
CSV/JSON, 48 wierszy, selekcję nowszej publikacji PK5 20.11 spośród dwóch,
niezmienność źródeł i zgodność audytu. Błędny hash, snapshot_id lub poprawnie
uporządkowane pobranie po cutoff w dowolnej grupie zwracają kod 2 bez stdout.
Tak samo odrzucane są duplikaty dostawy i brak ścieżki capture. Lokalnie
przeszło 25/25 testów eksportu, w tym wszystkie 19 wcześniejszych regresji
jednodniowego CLI i 3 testy kolekcji. Nie dodano doby historii ani rankingu.
Produkcja, FROZEN i dotychczasowe archiwa pozostają bez zmian.


## Checkpoint 2026-11-20 — jawna ocena walk-forward z kolekcji źródeł

CLI shadow_source_collection otrzymało opcję
`--format json --walk-forward-at PEŁNY_CZAS_ZE_STREFĄ`.
Po pełnej walidacji kolekcji wywołuje istniejące evaluate_csv_rows na dokładnie
eksportowanych wierszach i zakresie od pierwszej do ostatniej dostawy. JSON
zawiera evaluation z jawnym evaluated_at_utc, audytem, przyjęciem CSV oraz
wynikiem istniejącego walk-forward OLS 30/60/90D, podobnych dób i mediany.
Nie dodano modelu ani zastępczych danych. Bez opcji wynik eksportu pozostaje
bez zmian; opcja z CSV jest odrzucana, aby nie zgubić raportu oceny.

Czas oceny używa wspólnego ścisłego parsera: sekundy, jawna strefa, maksymalnie
6 cyfr ułamka. Wynik budowany jest w całości przed stdout; serializacja korzysta
z dotychczasowego formatera raportów walk-forward, obsługującego Score/Decimal.

Dwie nowe regresje subprocess sprawdzają oba porządki rzeczywistych źródeł
20/21.11, normalizację +01:00 do UTC, pełną zgodność oceny z istniejącym API
i identyczny wcześniejszy eksport po usunięciu pola evaluation. Dwie doby są
przyjęte, ale 0 dni/0 okresów wspólnego scoringu, 2 pominięcia i pusty ranking:
nadal brakuje 29/59/89 dób do pierwszego porównania. CSV z opcją oraz czas bez
strefy, bez sekund lub o nadmiernej precyzji zwracają kod 2 bez częściowego
wyjścia. Lokalnie przeszło 27/27 testów eksportu i kolekcji.
Nie wydano prognozy, nie odczytano FROZEN i nie zmieniono produkcji ani archiwów.


## Checkpoint 2026-11-20 — ponowne rzeczywiste pobranie jesiennej doby 25h

Oficjalny URL sesji 24.10 pobrano ponownie 20.11.2026: HTTP 200 bez zmiany URL,
rozpoczęcie 18:17:34.502900 Europe/Warsaw, zakończenie 18:17:43.329801.
Zachowano osobno gzip oryginalnego HTML, CSV etykiet i capture.json w
`tests/fixtures/tge_delivery_2026-10-25_recaptured`. HTML ma 442277 bajtów,
SHA-256 b7dafaa12ad87c2934f5fdf6d7a1f6bf554eeb13c1b0ecf7cd55a08599c07126.
CSV SHA-256 0202bf39d6790d50a7ca76710ebecfda8bd067d92d96effd0fdf30cfc8a8c5fa.

Nowy HTML różni się bajtowo od dowodu pobranego 2.11, ale wszystkie 25 cen
Fixing I i etykiety H01/H02/H02a/H03..H24 są identyczne. Potwierdzono je przez
parser etykiet, niezależną ekstrakcję XPath i CSV. H02a=380.01,
H10=-13.46, H24=370.01 PLN/MWh; ceny ujemne nie są obcinane.

Dwa nowe testy sprawdzają hashe, kompletność i niezmienność cen między
pobraniami oraz blokadę parsera integer, standardowego przyjęcia 24h i zwykłej
referencji dla doby DST. Lokalnie 2/2 przeszły. Dowód jest TGE_LABEL_ONLY,
bez period_start_utc i bez manifestu gotowej paczki. Późne pobranie nie dowodzi
historycznej dostępności PK5 przed D−1 09:50; nie dodano go do treningu,
scoringu PostgreSQL ani rankingu. Wcześniejsze pliki i produkcja bez zmian.


## Checkpoint 2026-11-20 — rzeczywiste przekierowanie zamiast wiosennej sesji 23h

Sprawdzono oficjalny historyczny URL sesji 28.03 dla dostawy 29.03.2026.
20.11 pobranie rozpoczęto o 19:17:17.940181 Europe/Warsaw i zakończono
19:17:35.179365. Odpowiedź HTTP 200 pochodziła jednak po przekierowaniu z
https://tge.pl/ i nie zawierała tabeli RDN. To nie są ceny żądanej dostawy.
Zachowano oryginalny HTML gzip oraz metadane obu URL w osobnym katalogu
`tests/fixtures/tge_delivery_2026-03-29_redirected`, bez CSV i manifestu
przyjęcia. HTML: 257560 bajtów; SHA-256
8354293db9974600ee050478336fc809675c90fb245d149512fea52cf24e517b.

Dwa nowe testy sprawdzają hash/rozmiar, faktyczny brak tabeli i odrzucenie
niezależnej ekstrakcji. Odtworzenie zaobserwowanego statusu/końcowego URL
potwierdza odrzucenie przez _fetch_session przed odczytem body i bez retry.
Drugi test potwierdza zachowaną blokadę wiosennej konwencji etykiet oraz brak
żądań i częściowych plików przy próbie standardowego capture doby 23h.
Lokalnie 2/2 testy przeszły. Nie poszerzono obsługi parsera na podstawie
nieistniejących danych. Wiosenne etykiety i mapowanie UTC nadal niezweryfikowane;
nie dodano cen, historii treningowej, wyniku scoringu ani rankingu.
Produkcja i dotychczasowe archiwa pozostają bez zmian.


## Checkpoint 2026-11-20 — zakres celów oceny bez usuwania historii źródeł

CLI shadow_source_collection obsługuje teraz --target-from / --target-to
wymagające jawnego --walk-forward-at. Zakres ogranicza wyłącznie cele oceny;
wszystkie przyjęte wiersze i dowody źródeł pozostają w raporcie oraz historii
przekazywanej do istniejącego evaluate_csv_rows. Pominięta granica domyślnie
odpowiada pierwszej/ostatniej dostawie kolekcji. Odwrócony zakres, niepoprawna
data lub zakres bez oceny są odrzucane bez częściowego stdout.

Dwie nowe regresje subprocess na rzeczywistych danych 20/21.11 sprawdzają
obie kolejności paczek i identyczny pełny eksport poza polem evaluation.
Dla celu 21.11 pomijana jest tylko ta doba: 20.11 nadal figuruje jako 1 dzień
przyjęty do historii okna 30D, a brakujących jest 29. Ocena wyłącznie 22.11
pokazuje 1 brakującą dobę kalendarzową i pusty ranking, bez tworzenia danych
lub sztucznego skipped dla nieobecnego źródła. Pełne wyniki porównano z API
walk-forward wywołanym na wszystkich 48 wierszach i tym samym zakresie.
Lokalnie 29/29 testów eksportu/kolekcji przeszło. Dwie doby nadal nie wystarczają
do treningu wymaganych okien. Produkcja, FROZEN i archiwa pozostają bez zmian.


## Checkpoint 2026-11-20 — rzeczywisty wczesny PK5 weekendowej dostawy 22.11

Uruchomiono istniejące capture_pk5_validation dla dostawy 22.11. Publiczny
PSE pk5l-wp zwrócił HTTP 200 i komplet 24 okresów. Publikacja: 20.11 o
21:02:37.114 Europe/Warsaw; początek pobrania 21:20:32.063933, koniec
21:20:40.007491. To pobranie dzień wcześniej niż własny cutoff tej dostawy:
21.11 o 09:50 Europe/Warsaw. Zachowano oryginalny JSON gzip i capture.json
ze snapshot ID w tests/fixtures/pse_pk5_delivery_2026-11-22.
Surowy JSON: 18621 bajtów; SHA-256
ab47fe9dd17f9f13df01963097c616227f305f3578de291f8793722633ac8892.
H01 JWCD=6192.00000, H24=9701.00000 MW; zmienna wyłącznie fcst_gen_unit_stor_prov.

Dwa nowe testy sprawdzają rozmiar/hash/ID, pełne 24 wartości względem surowego
JSON, publikację, chronologię, własny cutoff oraz skrajne UTC okresów. Rzeczywiste
kandydaty dostaw 21/22.11 w obu kolejnościach wybierają wyłącznie własną dobę,
a obca ma WRONG_DELIVERY_DATE. Wejście jest niezmienione; syntetyczne przesunięcie
pobrania 22.11 o 1 mikrosekundę po jej cutoff jest odrzucane. Lokalnie 2/2 testy
przeszły. To wczesna obserwacja PK5; późniejsze rewizje mogą być nowsze.
Nie dodano Fix1 dla 22.11, sparowanej doby treningu, prognozy ani rankingu.
Produkcja, poranne wydanie i istniejące archiwa pozostają bez zmian.


## Checkpoint 2026-11-20 — atomowy wyłączny zapis eksportu kolekcji

CLI shadow_source_collection ma opcjonalne --output NOWY_PLIK. Najpierw
waliduje całą kolekcję i przygotowuje pełny CSV/JSON, następnie zapisuje plik
tymczasowy w katalogu docelowym, flush/fsync i publikuje go atomowym link.
Istniejąca ścieżka docelowa nie jest nadpisywana, a staging jest sprzątany
również przy błędzie zapisu/publikacji. Katalog docelowy musi już istnieć.
Przy --output stdout pozostaje pusty; bez opcji dotychczasowy eksport stdout
pozostaje bez zmian. Nie zmieniono archiwizacji PostgreSQL ani produkcji.

Trzy nowe testy sprawdzają bajtową zgodność zapisanych CSV/JSON na rzeczywistych
źródłach 20/21.11 z dotychczasowym stdout, odmowę ponownego zapisu i zachowanie
pliku. Nieprawidłowa kolekcja nie tworzy pliku ani nie narusza już istniejącego.
Wstrzyknięte błędy fsync/link nie zostawiają stagingu; dwóch równoczesnych
lokalnych autorów publikuje dokładnie jeden kompletny wynik, drugi otrzymuje
FileExistsError, bez pozostałych plików. Lokalnie 32/32 testy eksportu/kolekcji
przeszły. Gwarancja dotyczy atomowej publikacji bez nadpisania; nie testowano
awarii zasilania ani trwałości wpisu katalogu po takiej awarii. Nie dodano danych,
prognozy ani rankingu; produkcyjne archiwa pozostają nietknięte.


## Checkpoint 2026-11-20 — read-only zgodność cen między manifestami Fix1

Dodano shadow_manifest_audit.py: jawny audyt jednego lub wielu lokalnych
manifestów bez bazy, sieci i zapisów. Każda paczka przechodzi istniejące
load_batches, zanim powstanie raport. Dowody grupowane są po dostawie, a dokładne
powtórzenia identity/czasu/URL są liczone osobno od odrębnych pobrań i dób.
Raport zawiera oryginalne hashe/czasy/URL, liczbę unikalnych okresów dostaw,
CONSISTENT albo CONFLICT i dokładne UTC okresów z różnymi cenami.
Nie wybiera zwycięskiego pobrania ani nie zmienia kwarantanny/archiwum.
first_observed_retrieved_at_utc oznacza najwcześniejsze pobranie w podanej
próbie, nie czas publikacji TGE ani historyczną dostępność JWCD.

Trzy nowe testy: rzeczywiste Fix1 20.11, jego późne pobranie i 21.11 oraz
powtórzone wejście dają 4 wpisy, 3 odrębne dowody, 2 doby i 48 unikalnych
okresów, bez konfliktów. Kolejność nie zmienia raportu i źródła pozostają
bajtowo niezmienione. Jawnie syntetyczna zmiana H01 21.11 o +1 PLN/MWh
we własnej kopii HTML/CSV jest przyjmowana przez normalną walidację, ale audyt
wskazuje konflikt dokładnie 2026-11-20T23:00:00+00:00 i zachowuje oba dowody.
Błędny manifest w dowolnej kolejności odrzuca całe CLI bez częściowego stdout.
Lokalnie 3/3 testy przeszły. Audyt dotyczy wyłącznie wskazanych paczek;
nie dodano ceny, doby treningu, prognozy ani rankingu. Produkcja bez zmian.


## Checkpoint 2026-11-21 — odtworzony audyt całego zachowanego korpusu manifestów

Uruchomiono nowy read-only audit_manifests dla wszystkich 20 manifestów
Fix1 obecnych w commicie 40cd54cbe8abec5feceb13335a0f853dd926599d.
Zapisano pełny, odtwarzalny wynik wraz z listą ścieżek i hashem commitu źródła
w docs/validation/fix1_capture_consensus_2026-11-21.json.

Wynik: 28 odrębnych dowodów pobrania, 26 unikalnych dób i 624 okresy,
0 powtórzonych wpisów wejścia oraz 0 dób z konfliktem cen. Zakres próby
26.10–21.11; nie obejmuje manifestu dostawy 3.11, więc nie jest pełnym ciągiem
kalendarzowym. Dostawy 19/20.11 mają po dwa różne pobrania zgodnych cen.
Każda paczka przeszła kontrolę oryginalnych hashy, referencji i parsera.

Dwie nowe regresje odtwarzają cały zapisany raport, również przy odwróconej
kolejności manifestów, oraz sprawdzają hashe wszystkich plików próby przed/po.
Podwojenie listy wejścia daje 56 wpisów i 28 duplikatów, ale nadal te same
28 dowodów, 26 dób, 624 okresy i identyczne raporty dobowe. Lokalnie 2/2 testy
przeszły. To spójność zachowanych źródeł Fix1, nie 26 nowych dni operacyjnej
obserwacji ani potwierdzenie PK5 przed D−1 09:50; nie zwiększono gotowości
treningu i nie wygenerowano rankingu. Produkcja i archiwa pozostają bez zmian.


## Checkpoint 2026-11-21 — kalendarzowe luki audytu manifestów z podziałem 23/24/25h

Read-only audit_manifests i jego CLI obsługują opcjonalne start_day/end_day
oraz --start-day/--end-day. W tym trybie raport dodaje calendar_coverage:
zakres, liczbę obserwowanych/brakujących dób, zwarte missing_ranges oraz
oczekiwane i brakujące okresy osobno dla kalendarzowych dób 23/24/25h.
Brakująca granica odpowiada skrajnej dacie źródeł. Pełne dane dowodów i zgodności
cen pozostają bez zmian; bez podania granic format wcześniejszego raportu
jest identyczny, więc utrwalony checkpoint 28 pobrań pozostaje odtwarzalny.

Dwie nowe regresje sprawdzają CLI i API w obu kolejnościach źródeł. Dla próby
rzeczywistych manifestów 20/21.11 i kalendarza 25.10–21.11: 28 dób kalendarzowych,
2 obserwowane, 26 brakujących; brakuje 25 okresów doby jesiennej oraz 600 okresów
zwykłych dób. Zakres wyłącznie 29.03 pokazuje brak jednej doby 23h, bez dopisania
jej danych lub mapowania źródłowego. Błędne typy/granice API i odwrócony zakres
są odrzucane przed odczytem dowodów; CLI zwraca kod 2 bez częściowego stdout.
Lokalnie 5/5 testów audytu manifestów przeszło. Zakres dotyczy wyłącznie
podanych paczek, nie zewnętrznej bazy ani całej historii projektu.
Nie poszerzono przyjęcia źródeł DST, gotowości treningu lub produkcji.


## Checkpoint 2026-11-21 — rzeczywisty PK5 z dwoma czasami publikacji w jednej dobie

Narzędzie capture_pk5_validation pobrało kolejną obserwację dostawy 22.11:
HTTP 200, 24 okresy, początek 21.11 o 02:14:07.280190 Europe/Warsaw,
koniec 02:14:14.746482, przed własnym cutoff 09:50. Zachowano oryginalny JSON
gzip i capture.json osobno w pse_pk5_delivery_2026-11-22_second.
JSON: 18622 bajty; SHA-256
a73dd820bfd6f81b077c9f344ba16c2a47a13bab66c0f8477131df3dae33898a.

To rzeczywisty przypadek mieszanych czasów publikacji w jednym kompletnym
pobraniu: 14 okresów opublikowano o 02:12:31.438, a 10 o 02:03:06.832
Europe/Warsaw. Każdy okres zachowuje swój czas; metadata selekcji używa maksimum.
W porównaniu z pobraniem 20.11 o 21:20 zmieniły się 23 wartości JWCD;
H01=6170.00000, H24=9472.00000 MW.

Nowa regresja sprawdza hash/rozmiar/ID, oba dokładne czasy i ich liczności,
zgodność wszystkich wartości i publikacji z oryginalnym JSON. Obie kolejności
kandydatów wybierają całe nowsze pobranie, starsze jest SUPERSEDED_JWCD_PUBLICATIONS,
nie mieszając okresów. Publikacja i zakończenie pobrania przechodzą bramki
chronologii/cutoff, a wejście pozostaje niezmienione. Lokalnie 3/3 testy
PK5 22.11 przeszły. Nie dodano Fix1 22.11, sparowanej doby treningu ani prognozy.
Starszy dowód pozostaje zachowany; produkcja i FROZEN bez zmian.


## Checkpoint 2026-11-21 — ceny i dowody źródłowe przy konflikcie Fix1

Read-only audyt manifestów dodaje teraz price_conflicts wyłącznie dla dób
z rzeczywistą rozbieżnością wartości. Dla każdego konfliktowego okresu UTC
wymienia posortowane numerycznie ceny oraz dowody każdej z nich: hash raw,
hash referencji, parser, URL i czas pobrania. Nie wybiera ceny poprawnej,
nie zapisuje bazy i nie modyfikuje kwarantanny. Doby bez konfliktu zachowują
identyczny dotychczasowy raport, w tym zapisany checkpoint korpusu 28 pobrań.

Rozszerzony test jawnie syntetycznej kopii H01 21.11 sprawdza dwie ceny
783.16 / 784.16 oraz dokładne powiązanie z obiema referencjami i raw HTML,
zgodność CLI/API i niezależność od kolejności. Źródła pozostają niezmienione.
Nowa regresja używa niezmienionego rzeczywistego HTML, zmieniając jedynie
zapis 783.16 na 783.1600 we własnej kopii CSV i jej hash: dwa różne dowody
referencji nadal mają CONSISTENT i zero konfliktów. Grupowanie cen używa
wartości Decimal, a nie tekstowej reprezentacji. Lokalnie 6/6 testów audytu
manifestów przeszło. Nie znaleziono ani nie zadeklarowano nowego konfliktu
oficjalnych cen; zmiana dotyczy diagnostyki. Produkcja i archiwa bez zmian.


## Checkpoint 2026-11-21 — audyt bez kumulowania raw HTML całego korpusu

Audyt manifestów przetwarza teraz paczki kolejno i zachowuje tylko zweryfikowane
ArchiveBatch oraz liczniki. Usunięto globalną listę wszystkich par batch/raw;
surowe HTML nie są już utrzymywane przez cały audyt. load_batches nadal
waliduje całą bieżącą paczkę, a raport powstaje dopiero po poprawnym odczycie
wszystkich manifestów. Wyniki, deduplikacja, diagnostyka konfliktów i zakres
kalendarza pozostają bez zmian. Nie zmieniono kodu zapisu ani PostgreSQL.

Nowa regresja na kopii rzeczywistego HTML 21.11 zmienia jedną cenę, zachowując
prawidłowy gzip i liczbę bajtów, ale pozostawiając oryginalny hash w manifeście.
Błąd hash odrzuca całe API i CLI w obu kolejnościach z prawidłowym manifestem;
stdout pozostaje pusty, a wszystkie źródła i wadliwa kopia są niezmienione.
Lokalnie 7/7 testów audytu przeszło.

Wykonano porównanie tracemalloc na trzech rzeczywistych manifestach 20/20-late/21.11
powtórzonych po 10 razy: 30 argumentów, 3 odrębne dowody. Raporty starej i nowej
wersji są dokładnie identyczne. Zaobserwowany szczyt alokacji Pythona spadł
z 11096500 do 1777249 bajtów. To pomiar jednej lokalnej próby, nie RSS procesu
ani gwarancja stałej pamięci: paczka bieżącego manifestu i dane wynikowe nadal
zajmują pamięć. Produkcja, FROZEN i istniejące archiwa bez zmian.


## Checkpoint 2026-11-21 — opcjonalny kod zakończenia dla konfliktu cen

CLI shadow_manifest_audit obsługuje --fail-on-conflict. Z tą opcją poprawnie
zwalidowany audyt bez rozbieżności kończy się kodem 0; wykryte konflikty cen
zwracają kod 3 po wypisaniu kompletnego raportu JSON z cenami i dowodami.
Błędne/niepełne wejście nadal zwraca kod 2 i pusty stdout. Bez opcji zachowano
dotychczasowy tryb diagnostyczny, API oraz format raportu. Ułatwia to jawne
rozróżnienie konfliktu danych od błędu ich walidacji przez narzędzie wywołujące.
Nie dodano zadań, zapisów PostgreSQL ani automatycznego rozstrzygania konfliktu.

Rozszerzona regresja syntetycznej zmiany H01 21.11 potwierdza kod 3 i dokładnie
ten sam pełny raport w obu kolejnościach paczek, przy pustym stderr i bez zmian
źródeł. Nowy test dla rzeczywistych manifestów 20/20-late/21.11 i duplikatu
potwierdza kod 0 oraz zgodność raportu z API. Wadliwy manifest przed/po prawidłowym
zwraca kod 2 bez częściowego JSON również z nową opcją. Lokalnie 8/8 testów audytu
przeszło. Nie zmieniono reguł publication_ts/retrieved_at ani cutoff D−1 09:50.
Produkcja, FROZEN, istniejące archiwa i PR #2 pozostają bez zmian.


## Checkpoint 2026-11-21 — trzecia rzeczywista rewizja PK5 dostawy 22.11

Narzędzie capture_pk5_validation pobrało komplet 24 okresów: publikacja
21.11 o 06:13:06.353 Europe/Warsaw, początek pobrania 06:18:25.318220,
koniec 06:18:32.987931, HTTP 200, przed cutoff 09:50. Zachowano oryginalny
JSON gzip i capture.json w pse_pk5_delivery_2026-11-22_third.
JSON ma 18623 bajty; SHA-256
368fbb5c84305d6b7e4da4739b2b65ee68496658ec84633779bc5a90b8c05bdc.
H01 JWCD=6198.00000, H24=9553.00000 MW. Wszystkie 24 wartości zmieniły się
wobec pobrania o 02:14, a każda publikacja okresu jest teraz taka sama.

Nowa regresja sprawdza hash/rozmiar/ID, publikacje wszystkich okresów, chronologię,
cutoff i dokładne wartości surowego JSON. We wszystkich sześciu kolejnościach
trzech rzeczywistych obserwacji selekcja wybiera całe trzecie pobranie, obie
starsze publikacje są SUPERSEDED_JWCD_PUBLICATIONS, bez mieszania okresów
ani zmiany wejścia. Lokalnie 4/4 testy PK5 22.11 przeszły.
To kolejna rewizja jednej doby, nie nowa doba treningu. Nie dodano Fix1 22.11,
nie wydano prognozy i nie zmieniono produkcji, FROZEN ani starszych dowodów.


## Checkpoint 2026-11-21 — ograniczone ponowienia otwarcia żądania PK5 przed cutoff

Narzędzie capture_pk5_validation ponawia teraz wyłącznie przejściowe błędy
urlopen: timeout połączenia oraz HTTP 429/500/502/503/504. Maksymalnie 3 próby;
podstawowe oczekiwanie 1 i 2 sekundy, Retry-After w sekundach respektowane
do limitu 5 sekund na oczekiwanie. Dłuższy lub nieobsługiwany nagłówek daty
nie powoduje ponowienia. HTTPError jest zamykany. Stałe błędy, przekierowania,
niepełne body, błędny JSON i niezgodność dowodów nie są ponawiane.

Przed oczekiwaniem sprawdzane jest, czy następna próba zmieści się przed
D−1 09:50 Europe/Warsaw; przed jej otwarciem cutoff jest sprawdzany ponownie.
Metadane zachowują konserwatywny początek przed pierwszą próbą oraz rzeczywisty
koniec poprawnego odczytu. Istniejąca końcowa walidacja publikacji i pobrania
pozostaje obowiązkowa; pliki powstają dopiero po niej. Nie zmieniono collectora
produkcyjnego ani harmonogramu.

Trzy nowe testy na zapisanych oryginalnych bajtach PK5 21.11 z emulowanym HTTP
obejmują 503 → timeout → sukces, dokładne czasy i treść zapisanych dowodów,
zamknięcie błędu HTTP, limit prób i odmowę długiego Retry-After/DNS/HTTP400.
Sprawdzono brak następnego żądania, gdy planowane oczekiwanie lub rzeczywiste
wybudzenie przekroczy cutoff, bez plików dowodów. Dotychczasowe testy nadal
potwierdzają odrzucenie niepełnego body/JSON/URL bez retry oraz ochronę istniejących
plików. Lokalnie 6/6 testów przeszło. Nie wykonano nowego pobrania sieciowego,
nie wydano prognozy i nie zmieniono produkcji, FROZEN ani archiwów.


## Checkpoint 2026-11-21 — rzeczywiste pobranie PK5 nowym narzędziem z retry

Uruchomiono aktualne capture_pk5_validation po dodaniu ograniczonych ponowień.
Publiczny PSE zwrócił HTTP 200 i 24 okresy dostawy 22.11: publikacja 21.11
08:02:42.387 Europe/Warsaw, początek pobrania 08:14:34.192048,
koniec 08:14:41.928655, przed cutoff 09:50. Zapisano osobno JSON gzip i
capture.json w pse_pk5_delivery_2026-11-22_fourth. To poprawny przebieg live,
nie dowód wystąpienia przejściowego błędu sieciowego podczas tego pobrania.
JSON: 18621 bajtów; SHA-256
a34eb8ec73462006cc7c43e509de77c685605b3c4ea82307cb06df466ebe398f.
H01 JWCD=6326.00000, H24=9744.00000 MW. Wszystkie 24 wartości różnią się od
poprzedniego pobrania o 06:18.

Nowa regresja sprawdza hash/rozmiar/ID, 24 publikacje i wartości surowego JSON,
chronologię i cutoff oraz wybór całego czwartego pobrania we wszystkich 24
kolejnościach czterech zachowanych rzeczywistych obserwacji. Wszystkie starsze
są SUPERSEDED_JWCD_PUBLICATIONS; brak mieszania okresów i zmian wejścia.
Lokalnie 5/5 testów PK5 22.11 przeszło. Nie dodano Fix1 22.11, doby treningu,
prognozy ani rankingu. Starsze dowody, produkcja, FROZEN i harmonogramy bez zmian.


## Checkpoint 2026-11-21 — ścisła składnia Retry-After w pobieraniu PK5

Narzędzie capture_pk5_validation akceptuje teraz Retry-After wyłącznie jako
nieujemne sekundy zapisane cyframi ASCII (z dopuszczalnym zewnętrznym białym
znakiem nagłówka). Wcześniejsze int() dopuszczało również znak liczby,
podkreślenia i cyfry Unicode. Takie niepoprawne nagłówki kończą teraz żądanie
bez sleep, następnego otwarcia i plików dowodów. Limit 5 sekund, maksymalnie
3 próby oraz oba sprawdzenia cutoff D−1 09:50 pozostają obowiązkowe.

Dwie nowe regresje z emulowanym HTTP i zachowanymi oryginalnymi bajtami PK5
21.11 obejmują sześć niepoprawnych zapisów oraz poprawne 0 / 2 / 005 sekund:
minimalne oczekiwanie 1 s, rzeczywiste oczekiwanie 2 i 5 s, dwa otwarcia,
niezmienione konserwatywne retrieved_at i koniec odczytu, dokładne zapisane raw.
Lokalnie 8/8 testów narzędzia przeszło. Nie wykonano nowego pobrania live;
nie dodano nowej doby treningu ani prognozy. Produkcja, FROZEN, archiwa,
harmonogramy i PR #2 pozostają bez zmian.


## Checkpoint 2026-11-21 — odczyt dowodów PK5 niezależnie od obecności Fix1

Wydzielono load_pk5_capture(path, day=...) ze wspólnego loadera historii.
Pozwala odczytać i zweryfikować zachowane PK5 przed pojawieniem się cen Fix1
tej samej doby. load_history_day korzysta z tej samej funkcji; zachowano
kontrakt paczki wejściowej i reguły eksportu po połączeniu źródeł.

Loader wymaga jawnej daty, lokalnej ścieżki, ograniczonego rozmiaru metadata/raw,
HTTP 200, zgodnego URL, 24 wierszy, daty, liczby bajtów, hash i tożsamości
snapshot, jeśli zadeklarowano ją w metadata. Walidacja oryginalnego JSON,
publikacji, początku/końca pobrania i własnego cutoff D−1 09:50 odbywa się
zawsze, również dla starszych metadata bez snapshot_id. Nie ma odczytu
sieci, bazy, sekretów ani zapisu. Spójność dowodów nie uwierzytelnia niezależnie
historycznych czasów zadeklarowanych przez dostawcę paczki.

Trzy nowe testy przeszły lokalnie (3/3). Cztery rzeczywiste obserwacje dostawy
22.11 przechodzą niezależnie od manifestu Fix1; obie kolejności wybierają
całe czwarte pobranie. Testy starszego formatu bez ID odrzucają pobranie po
cutoff i zmieniony hash. Błędne ID, data, status HTTP, URL, liczba wierszy,
rozmiar i wyjście ścieżki poza katalog są odrzucane bez zmian plików.
Błędny typ daty jest odrzucany przed otwarciem pliku. Nie dodano Fix1 22.11,
nowych obserwacji live, doby treningowej ani rankingu. Produkcja, FROZEN,
istniejące archiwa, harmonogramy i PR #2 bez zmian.


Pełne CI pierwszej wersji wykonało 566 testów i wykazało dwa niepowodzenia
podtestów CSV/JSON wyłącznie przez zmianę treści komunikatu niezgodnej daty.
Przywrócono dotychczasowy komunikat „PK5 capture delivery differs from Fixing I”,
zachowując istniejący interfejs diagnostyczny; testów eksportu nie zmieniono.


## Checkpoint 2026-11-21 — rzeczywisty Fix1 dostawy 22.11 połączony z PK5

Publiczna sesja TGE z 21.11 zwróciła HTTP 200 i komplet 24 cen Fixing I
na 22.11. Odczyt zakończono 21.11 o 11:19:39.063666 Europe/Warsaw.
Zachowano oryginalny HTML w gzip (mtime=0), CSV referencji XPath z tego
samego HTML i manifest w tests/fixtures/tge_delivery_2026-11-22.
Nie jest to niezależny eksport oficjalny; XPath i parser porównują ten sam raw.
HTML: 280499 bajtów, SHA-256
6435b1a6171a4c65bf04a3fa6aaf944e5c492651869d6146c645a2703a63291b.
CSV SHA-256:
f8fa69544585150148c84cf9114afb16faf5246c6062ac424f91a5ee7c1b8847.
H01=356.76, H24=686.52 PLN/MWh. Wszystkie 24 etykiety i ceny parsera
zgadzają się z XPath, CSV i jawną siatką UTC standardowej doby.

Nowe regresje łączą rzeczywisty Fix1 z czterema zachowanymi obserwacjami PK5
22.11. Obie kolejności wejścia dają identyczny raport i wybierają całe
czwarte pobranie: publikacja 08:02:42.387, początek 08:14:34.192048,
koniec 08:14:41.928655 Europe/Warsaw, przed własnym D−1 09:50.
Każdy okres zachowuje publikację i oba czasy pobrania; trzy starsze
obserwacje są SUPERSEDED_JWCD_PUBLICATIONS. H01 JWCD=6326.00000,
H24=9744.00000 MW. Dostępność Fix1 przyjęto dopiero od końca tego
rzeczywistego pobrania, bez cofania czasu do publikacji lub cutoff.

Lokalnie 3/3 nowe testy przeszły. Ta paczka wnosi jedną rzeczywistą
sparowaną dobę (24 okresy); sama nie tworzy okien treningu 30/60/90D.
Walk-forward przed i w chwili dostępności Fix1 nadal ma zero wspólnych
okresów i pusty ranking dla tej jedno-dobowej próbki. Nie wydano prognozy
ani nie zmieniono produkcji, FROZEN, starszych archiwów czy PR #2.
Historyczny zamrożony raport korpusu pozostawiono bez zmian; nowa paczka
może być jawnie dodana w kolejnym audycie.


## Checkpoint 2026-11-21 — atomowość i kwarantanna rzeczywistej paczki Fix1 22.11

Dodano tests/test_tge_november22_archive.py dla zachowanego oryginalnego HTML
i referencji dostawy 22.11. Zmiana dotyczy wyłącznie testów; adapter archiwum,
produkcja, FROZEN, konfiguracja Railway i istniejące dowody pozostają bez zmian.

Pierwszy test wymusza CheckViolation dokładnie na ostatnim okresie UTC
(22.11 22:00Z) w izolowanej tabeli cen. Sprawdza brak cen, paczek i kwarantanny
po błędzie transakcji. Po usunięciu testowego ograniczenia ponowienie ma dać
INSERT całych 24 okresów z oryginalnym HTML/hash/czasem pobrania, a następne
wywołanie REPLAY z identycznym stanem wszystkich trzech tabel.

Drugi test tworzy wyłącznie w pamięci jawnie syntetyczną zmianę H01
356.76 → 357.76 wraz z poprawnym raw i hashem referencji. Nie jest to
oficjalna korekta TGE i nie zmienia fixture. Sprawdza QUARANTINE, zachowanie
oryginalnych 24 cen i starej paczki, zapis dowodu konfliktu oraz trwałość
kwarantanny i identyczny stan przy ponowieniach obu paczek.

Lokalnie zweryfikowano przygotowanie rzeczywistej paczki i syntetycznej kopii:
24 okresy, różnica +1 tylko w H01, pozostałe 23 identyczne. PostgreSQL nie jest
dostępny lokalnie; dwa testy SQL zostały tam jawnie pominięte, a ich wykonanie
należy do istniejącego CI PostgreSQL 16 z osobną bazą fix1_shadow_test na loopback.
Nie wykorzystano sekretów ani bazy produkcyjnej. Nie dodano prognozy, score,
rankingu, pobrania sieciowego ani zadania automatycznego.


## Checkpoint 2026-11-21 — kontrolny scoring rzeczywistych źródeł 22.11

Dodano tests/test_tge_november22_scores.py. Jawnie syntetyczny wektor JWCD/20
korzysta wyłącznie z czwartego zachowanego PK5 dostawy 22.11, którego publikacja
i oba czasy pobrania poprzedzają D−1 09:50 Europe/Warsaw. Nie jest to
wytrenowany model ani wydana prognoza. Ceny rzeczywiste pochodzą z zachowanego
HTML/CSV Fix1 22.11; ich dostępność zaczyna się od pobrania 11:19:39.063666.

Niezależny rachunek Fraction łączy oryginalne etykiety PSE i CSV TGE
bez użycia wyniku score_day. Test sprawdza wszystkie cztery metryki do 12
miejsc dziesiętnych: MAE=195.941666666667 PLN/MWh,
bias=-195.941666666667 PLN/MWh, MAPE=28.062222694008%,
WAPE=31.997332761782%. Jest 24 okresów, wszystkie ceny rzeczywiste niezerowe.
Te liczby mierzą kontrolny wektor syntetyczny, nie skuteczność OLS lub produkcji.

Test integracyjny archiwizuje rzeczywistą paczkę wyłącznie w izolowanej bazie,
odrzuca scoring mikrosekundę przed dostępnością Fix1 i wymaga pustej tabeli
wyników. W chwili dostępności oczekuje INSERT jednego wyniku zgodnego z oracle,
następnie REPLAY z referencją oraz bez ponownego jej dostarczenia. Sprawdza
niezmieniony zapis wyniku, dokładne ceny archiwalne i brak kwarantanny.

Lokalny test czterech metryk przeszedł (1/1); SQL lokalnie jawnie pominięto
z powodu braku PostgreSQL, do wykonania w istniejącym CI PostgreSQL 16.
Nie zmieniono adapterów, produkcji JWCD 90D, FROZEN, Railway, archiwów,
PR #2 ani harmonogramów; nie dodano modeli lub pobrania sieciowego.


## Checkpoint 2026-11-21 — odtwarzalny audyt trzech rzeczywistych dób PK5/Fix1

Zapisano docs/validation/paired_source_audit_2026-11-22.json z jawną listą
paczek i source_commit 82b8d19823d9156433f2366df461d9c361eedbc1.
Zakres: dostawy 20/21/22.11, 72 unikalne okresy UTC, trzy kompletne pary,
trzy doby model_ready w samym audycie źródeł. Dla 20.11 użyto PK5 second,
dla 21.11 third, dla 22.11 porównano wszystkie cztery obserwacje i wybrano
całe fourth. Raport zachowuje hashe Fix1, referencje, czasy, cutoff,
wybrane ID oraz odrzucone rewizje PK5. Nie zastępuje wcześniejszych raportów.

Read-only walk-forward wykonano jawnie na cel 22.11 z oceną w chwili
pobrania Fix1: 21.11 11:19:39.063666 Europe/Warsaw. Wcześniejsze dwie doby
pozostają w historii treningowej; ich Fix1 jest dostępny przed cutoff celu.
Brakuje 28/58/88 wcześniejszych dni dla okien 30/60/90D; brak dopuszczonego
dnia porównania, wspólnych okresów i rankingu. model_ready oznacza poprawność
wierszy, nie gotowość okien treningu. Nie dopisano cen ani brakujących dat.

Dwa nowe testy przeszły lokalnie (2/2). Pełny zapisany raport odtwarza się
we wszystkich sześciu kolejnościach trzech paczek; hashe wszystkich plików
źródłowych pozostają identyczne. CLI i API dają identyczny pełny wynik
dla obu kolejności wejścia, normalizując czas Europe/Warsaw do UTC.
Zakres celu jest jednodniowy, ale eksport nadal zawiera całe 72 wiersze.
Nie odczytywano PostgreSQL ani sieci; produkcja JWCD 90D, FROZEN,
starsze archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-21 — uszkodzone gzip w read-only audycie manifestów

Rozszerzono testy audytu o dwa jawnie uszkodzone warianty kopii rzeczywistego
HTML 21.11: gzip ucięty do nagłówka oraz nieprawidłowy strumień DEFLATE
z zachowanym nagłówkiem/trailerem. To test odporności dowodów wejściowych,
nie nowa obserwacja TGE i nie zmiana ceny w archiwum.

Walidacja potwierdziła, że _read_raw_evidence już normalizuje EOFError i
zlib.error do ValueError('Invalid gzip HTML evidence'). Nowa regresja sprawdza
również dokładny typ wyjątku źródłowego w __cause__. Nie wprowadzono zbędnej
zmiany kodu wykonawczego. Dla obu uszkodzeń oraz obu kolejności z prawidłowym
manifestem CLI z --fail-on-conflict zwraca kod 2, pusty stdout i diagnostykę
bez traceback. Nie powstaje częściowy raport, także gdy poprawna paczka
występuje pierwsza. Wszystkie bajty oryginalnych źródeł oraz uszkodzonej
kopii pozostają identyczne po audycie.

Lokalnie 9/9 testów audytu manifestów przeszło. Zapis obejmuje wyłącznie
test i checkpoint. Nie użyto sieci ani PostgreSQL, nie zmieniono parsera,
archiwów, produkcji JWCD 90D, FROZEN, Railway, PR #2 ani harmonogramów.


## Checkpoint 2026-11-22 — kontrolowane błędy wejściowego CSV walk-forward

CLI shadow_walkforward_csv obejmuje teraz odczyt wszystkich CSV, ocenę i
serializację JSON wspólną obsługą OSError, ValueError i csv.Error. Błędy
struktury CSV, brak pliku oraz nieprawidłowy UTF-8 zwracają kod 2 i zwięzłą
diagnostykę argparse bez traceback. Cały wynik powstaje przed pierwszym
zapisem stdout; wadliwy późniejszy plik nie udostępnia częściowego raportu.
Wcześniej te błędy odczytu dawały nieobsłużony wyjątek i kod 1.

Dodano tests/test_walkforward_csv_cli_input_errors.py. Pięć przypadków
(duplikat kolumn, zła liczba pól, niedomknięty cudzysłów, błędne UTF-8,
brak pliku) sprawdzono przed i po prawidłowym rzeczywistym CSV Fix1 22.11.
Regresja wymaga kodu 2, pustego stdout, diagnostyki bez traceback i
niezmienionych źródeł. Poprawny CSV bez pełnego pochodzenia JWCD nadal
kończy się kodem 0 z pełnym audytem, zero dopuszczonych dób i pustym rankingiem,
dokładnie zgodnym z API; nie utożsamiono niegotowości modelu z błędem pliku.

Lokalnie 2/2 nowe testy przeszły. Nie zmieniono obliczeń OLS, selekcji PK5,
scoringu, bramek publication_ts/retrieved_at ani D−1 09:50 Europe/Warsaw.
Odczyt offline FROZEN i dane archiwalne pozostają bez zmian. Brak zapisów
PostgreSQL, pobrań live, zmian produkcji lub Railway i zmian PR #2/harmonogramów.


## Checkpoint 2026-11-22 — rzeczywisty wczesny PK5 dostawy 24.11

Narzędzie capture_pk5_validation pobrało z oficjalnego filtrowanego endpointu
PSE 24 okresy dostawy 24.11, HTTP 200 bez zmiany URL. Publikacja wszystkich
okresów: 22.11 22:05:07.376 Europe/Warsaw; początek pobrania
22:12:48.103863, koniec 22:12:55.417751. Własny cutoff to 23.11 09:50
Europe/Warsaw (08:50Z). Nie próbowano zastępować tej doby danymi 23.11.

Zachowano oryginalny JSON gzip (mtime=0) i capture.json w
tests/fixtures/pse_pk5_delivery_2026-11-24. Rozmiar raw: 18663 bajty,
SHA-256 f3a18364f4a41446dc67b163c3b258c5ce7665dd43ede9f0f705cc7ed88b55c5.
snapshot_id:
pk5-capture-sha256:7b073cbe000adecf5232208a45d48bc1430e17ca11f7d038e501819967e19e10.
H01 JWCD=8615.00000, H24=12155.00000 MW.

Dwa nowe testy przeszły lokalnie (2/2): dokładne hashe/rozmiar/ID i czasy,
24 wartości JWCD zgodne z raw, kompletna siatka UTC, 24 identyczne publikacje
oraz poprawna chronologia przed własnym cutoff. Drugi test odrzuca użycie
tej obserwacji dla dostawy 23.11 jako WRONG_DELIVERY_DATE i syntetyczne
przekroczenie początku/końca pobrania ponad własny cutoff. Wejście pozostaje
niezmienione, a oryginalna obserwacja nadal przechodzi selekcję.

To wczesne PK5, nie sparowana doba treningu: brak Fix1 24.11, brak nowej
prognozy, score lub rankingu. Nie zmieniono produkcji JWCD 90D, FROZEN,
Railway, istniejących archiwów, PR #2 ani harmonogramów.


## Checkpoint 2026-11-22 — osobny read-only audyt zapisanych obserwacji PK5

Dodano shadow_pk5_audit.py: jawny CLI --day YYYY-MM-DD CAPTURE.json [...]
oraz audit_pk5_captures(paths, day=...). Korzysta ze wspólnego loadera
dowodów PK5 i dotychczasowej selekcji całych obserwacji. Nie wymaga Fix1,
nie czyta sieci lub bazy i niczego nie zapisuje. Najpierw waliduje wszystkie
oryginalne paczki, dopiero potem tworzy kompletny JSON.

Raport zawiera własny cutoff D−1 09:50 Europe/Warsaw w UTC, wybrane ID,
hash raw i URL każdego kandydata, początek/koniec pobrania, liczności
czasów publikacji wszystkich okresów, odrzucone obserwacje z powodami oraz
24 wybrane wiersze UTC/JWCD/publikacja. Nie deklaruje gotowości treningu.
Brak zwycięzcy (np. duplikat ID) jest pełną diagnostyką z pustymi wybranymi
wierszami i kodem 0; nieprawidłowy lub spóźniony dowód zwraca kod 2,
pusty stdout i diagnostykę bez traceback, bez częściowego wyboru.

Lokalnie 3/3 nowe testy przeszły. Na czterech rzeczywistych rewizjach 22.11
wszystkie 24 kolejności dają identyczny raport i wybór fourth; zachowano
mieszane publikacje second (10+14 okresów). API i CLI są zgodne w obu
kolejnościach. Wczesne PK5 24.11 przechodzi bez Fix1, a jego duplikat
daje MISSING_OR_DUPLICATE_SNAPSHOT_ID bez wyboru. Wadliwy JSON, błędny hash
i koniec pobrania po cutoff zatrzymują cały audyt w obu kolejnościach.
Hashe źródeł oraz bajty wadliwych kopii pozostają niezmienione.

Nie zmieniono historycznych checkpointów, produkcji JWCD 90D, FROZEN,
archiwów PostgreSQL, Railway, PR #2 ani harmonogramów. Nie wydano prognozy
i nie dodano nowej obserwacji live ani doby treningowej.


## Checkpoint 2026-11-23 — druga rzeczywista rewizja PK5 dostawy 24.11

Publiczny PSE zwrócił HTTP 200 i komplet 24 okresów dostawy 24.11.
Publikacja wszystkich okresów: 23.11 00:14:20.888 Europe/Warsaw
(22.11 23:14:20.888Z). Pobranie rozpoczęto 00:16:29.078131 i zakończono
00:16:38.369227, przed własnym cutoff 23.11 09:50. Oryginalny JSON gzip
i capture.json zapisano osobno w pse_pk5_delivery_2026-11-24_second.

Raw: 18661 bajtów; SHA-256
ee1a8d8ff8066f343265285f2ba1fdc087a6d04369a020e0284e38dac1b98699.
snapshot_id:
pk5-capture-sha256:f19185d51f1e25ddfbde34e69fd3edef88cbb1b3db277ddf6654ac41e3863ad1.
H01 JWCD=8522.00000, H24=12169.00000 MW; wobec obserwacji 22.11 22:12
zmieniły się 23 wartości. Nie nadpisano wcześniejszych bajtów ani metadanych.

Nowa regresja potwierdza hash/rozmiar/ID, 24 publikacje, dokładne czasy
i chronologię/cutoff. Obie kolejności wybierają całe drugie pobranie,
wszystkie wartości zgadzają się z raw, a wcześniejsza obserwacja jest
SUPERSEDED_JWCD_PUBLICATIONS. Wejścia pozostają niezmienione, bez mieszania
okresów rewizji. Lokalnie 3/3 testy PK5 24.11 przeszły.

Nie dodano Fix1 24.11, sparowanej doby treningu, prognozy lub rankingu.
Produkcja JWCD 90D, FROZEN, Railway, starsze archiwa, PR #2 i harmonogramy
pozostają bez zmian.


## Checkpoint 2026-11-23 — opcjonalny wymagany wybór w audycie PK5

CLI shadow_pk5_audit obsługuje --require-selection. Po pełnej walidacji
i utworzeniu kompletnego JSON zwraca kod 3, gdy istniejąca selekcja nie
może wskazać całej obserwacji (np. duplikat ID lub konflikt). Udany wybór
zwraca 0, także jeśli raport wymienia starsze, prawidłowo zastąpione rewizje.
Wadliwy dowód nadal zwraca 2 i pusty stdout. Bez opcji zachowano dotychczasowy
tryb diagnostyczny, kody i identyczny JSON; API oraz bramki selekcji bez zmian.

Dwie nowe regresje sprawdzają kod 0 dla czterech rzeczywistych rewizji 22.11,
kod 3 i identyczny pełny raport przy duplikacie oraz kod 2 bez raportu przy
brakującym pliku. Drugi test tworzy wyłącznie w katalogu tymczasowym
syntetyczne JWCD H01 +1 z raw PK5 24.11, z aktualnym hashem/rozmiarem
i ponownie wyliczoną przez loader tożsamością. Czasy publikacji i pobrania
pozostają identyczne: selekcja zwraca CONFLICTING_LATEST_SNAPSHOTS, bez
wybranych wierszy. W obu kolejnościach --require-selection zwraca kod 3
i pełny raport zgodny z API; oryginalne źródło i syntetyczna kopia pozostają
niezmienione. Nie jest to nowa oficjalna rewizja ani kwarantanna PostgreSQL.

Lokalnie 5/5 testów audytu PK5 przeszło. Nie zmieniono reguł publication_ts,
retrieved_at, końca pobrania ani cutoff D−1 09:50 Europe/Warsaw. Brak nowych
pobrań live, zapisów bazodanowych, prognoz lub rankingu. Produkcja JWCD 90D,
FROZEN, Railway, istniejące archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — trzecia rzeczywista rewizja PK5 dostawy 24.11

Oficjalny PSE zwrócił HTTP 200 i komplet 24 okresów dostawy 24.11.
Publikacja wszystkich okresów: 23.11 02:13:27.462 Europe/Warsaw.
Początek pobrania: 02:18:59.865233, koniec: 02:19:09.192381, przed
własnym cutoff 23.11 09:50. Oryginalny JSON gzip (mtime=0) i capture.json
zapisano osobno w pse_pk5_delivery_2026-11-24_third, bez zmian starszych dowodów.
Raw ma 18662 bajty, SHA-256:
8b585dc921d36ff5cf1acda0f3e0163d754161f8fba6248eae2657dbf6c37e7d.
snapshot_id:
pk5-capture-sha256:82207eb47c997127091073af89607944eb67dfffd8e0f97c914c4d983530374a.
H01 JWCD=8421.00000, H24=12057.00000 MW; wszystkie 24 wartości zmieniły
się względem drugiego pobrania o 00:16.

Nowa regresja sprawdza rozmiar/hash/ID, metadata HTTP/24 okresy, wszystkie
publikacje, dokładne czasy i chronologię/cutoff. We wszystkich sześciu
kolejnościach trzech rzeczywistych obserwacji selekcja wybiera całe trzecie
pobranie, a obie starsze są SUPERSEDED_JWCD_PUBLICATIONS. Wszystkie wartości
są zgodne z raw; nie miesza się okresów rewizji i nie zmienia wejścia.
Lokalnie 4/4 testy PK5 24.11 przeszły.

To kolejna obserwacja PK5 jednej doby, nie nowa para PK5/Fix1. Nie dodano
Fix1 24.11, okien treningu, prognozy, score ani rankingu. Produkcja JWCD 90D,
FROZEN, Railway, istniejące archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — okresy różniące rewizje PK5 od wybranej obserwacji

Read-only audyt PK5 dodaje revision_differences_from_selected. Dla każdego
kandydata wymienia ID, liczbę changed_periods oraz posortowaną listę
changed_periods_utc, porównując wartości JWCD z całą wybraną obserwacją.
Porównanie jest liczbowe (Decimal), bez obliczania modelu, uśredniania lub
mieszania okresów. Wybrana obserwacja ma zero różnic względem siebie.
Przy braku zwycięzcy, również duplikacie lub konflikcie, pole ma null:
nie deklaruje zera zmian i nie wyznacza arbitralnej referencji.

Nowa regresja korzysta z oryginalnych JSON trzech obserwacji PK5 24.11.
Niezależnie zestawia ich wartości z siatką UTC i sprawdza dokładne listy
okresów: 23 zmiany pierwszej do drugiej oraz 24 drugiej do trzeciej.
Wszystkie sześć kolejności trzech paczek daje identyczny pełny raport;
CLI z --require-selection zgadza się z API. Hashe i bajty źródeł pozostają
niezmienione. Rozszerzono też kontrole duplikatu i syntetycznego konfliktu
o null zamiast pozornego wyniku porównania.

Lokalnie 6/6 testów audytu przeszło. Reguły selekcji, publikacji, początku
i końca pobrania oraz cutoff D−1 09:50 Europe/Warsaw pozostają bez zmian.
Nie wykonano nowego pobrania live, zapisu bazy, prognozy ani rankingu.
Produkcja JWCD 90D, FROZEN, Railway, istniejące archiwa, PR #2 i harmonogramy
pozostają bez zmian.


## Checkpoint 2026-11-23 — kolekcja źródeł bez kumulacji raw wszystkich dób

Dodano load_history_collection(groups), który wczytuje i waliduje jedną
paczkę PK5/Fix1 naraz, zachowując jedynie przygotowane raporty. Po walidacji
bieżącej doby zwalnia jej paczkę raw przed odczytem następnej. CLI
shadow_source_collection korzysta z nowego loadera zamiast wcześniejszej
listy wszystkich paczek z oryginalnym HTML/JSON w pamięci.

prepare_history_collection zachowuje dotychczasowy kontrakt list/tuple
gotowych paczek. Oba wejścia współdzielą końcową kontrolę duplikatów dób,
sortowanie, łączenie wierszy i audyt. Wynik pozostaje kompletny przed
jakimkolwiek stdout lub atomowym eksportem; nie zmieniono formatu raportu,
doboru rewizji, dostępności publikacji/pobrania ani cutoff D−1 09:50.

Lokalnie 3/3 testy checkpointu trzech dób przeszły. We wszystkich sześciu
kolejnościach paczek nowy loader daje dokładnie taki sam pełny raport jak
dotychczasowe API i odtwarza niezmieniony zapisany checkpoint 72 okresów.
CLI nadal zachowuje wcześniejsze wiersze treningowe przy ograniczeniu celu.
Nowy test odrzuca puste i błędne grupy ścieżek przed otwarciem źródeł.
Hashe wszystkich zapisanych źródeł pozostają niezmienione.

Po rozgrzaniu obu ścieżek wykonano jedną lokalną próbę tracemalloc na
rzeczywistych paczkach 20/21/22.11: szczyt śledzonych alokacji Pythona
wyniósł 17840896 bajtów dla listy wszystkich paczek oraz 17300151 dla
odczytu doba po dobie. SHA-256 znormalizowanych JSON obu pełnych raportów
jest identyczny: 7cc8197a1e81982f1ed97b518c931f64725314d6fc120f18e6bd8efa19193054.
To pomiar jednej próby, nie RSS ani gwarancja stałej pamięci: bieżąca
doba, parser i raporty wynikowe nadal wymagają pamięci.

Nie wykonano pobrań live lub zapisów PostgreSQL. Produkcja JWCD 90D,
FROZEN, Railway, istniejące archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — czwarta rzeczywista rewizja PK5 dostawy 24.11

Pobrano komplet 24 okresów PK5 dostawy 24.11 z oficjalnego endpointu,
HTTP 200 bez zmiany URL. Publikacja wszystkich okresów: 23.11
05:13:37.112 Europe/Warsaw. Początek pobrania: 05:16:33.099925,
koniec: 05:16:40.125791, przed własnym cutoff 09:50. Oryginalny JSON gzip
i capture.json zapisano osobno w pse_pk5_delivery_2026-11-24_fourth.
Raw ma 18662 bajty, SHA-256:
b52e3be34a8134c6d138e1678a9a1c8e06b3b1534894fc4b760f7249f93262a7.
snapshot_id:
pk5-capture-sha256:84fc58e86395342b9d10855b5a36f38616e3ddec5844907048887642056ab498.
H01 JWCD=8396.00000, H24=12055.00000 MW. Wobec trzeciego pobrania
o 02:19 zmieniły się 23 wartości.

Nowa regresja sprawdza dokładny hash, rozmiar, ID, metadata HTTP/24 okresy,
publikacje, czasy i chronologię przed cutoff. Wszystkie 24 kolejności
czterech zachowanych obserwacji wybierają całe czwarte pobranie, trzy starsze
są SUPERSEDED_JWCD_PUBLICATIONS. Wartości wybranego wektora zgadzają się
z oryginalnym JSON; nie miesza się okresów ani nie zmienia wejścia.
Lokalnie 5/5 testów PK5 24.11 przeszło.

Nie nadpisano starszych dowodów, nie dodano Fix1 24.11 ani sparowanej doby
treningowej. Nie wydano prognozy lub rankingu i nie zmieniono produkcji
JWCD 90D, FROZEN, Railway, archiwów, PR #2 ani harmonogramów.


## Checkpoint 2026-11-23 — rzeczywisty Fix1 dostawy 23.11 i audyt samych cen

Oficjalna sesja TGE z 22.11 zwróciła HTTP 200 i komplet 24 cen Fixing I
dostawy 23.11. Odczyt zakończono 23.11 o 06:13:41.794372 Europe/Warsaw;
nie cofnięto tego czasu do wczorajszej publikacji. Zachowano oryginalny
HTML gzip (mtime=0), referencję CSV i manifest w tge_delivery_2026-11-23.
Referencja pochodzi z niezależnej ekstrakcji XPath TEGO SAMEGO HTML,
nie z odrębnego oficjalnego eksportu.

HTML: 426420 bajtów, SHA-256
ac5c3139d9e00c323f48475d899cda7af87f3c7114773c06da4fa62fdf6ab194.
CSV SHA-256:
68784f149172d92dcefd115c8ead9f522b9264cb71c7d1031b4fcd2539f0bebe.
H01=597.82, H24=468.67 PLN/MWh. Parser, XPath, CSV i jawna siatka UTC
są zgodne dla wszystkich 24 okresów.

Dwa nowe testy przeszły lokalnie (2/2). Read-only audyt jawnie wskazanych
manifestów 22/23.11 ma dwie doby, 48 okresów, brak luk kalendarza i zero
konfliktów cen, niezależnie od kolejności wejścia. Hashe źródeł nie zmieniają
się. Sam CSV cen 23.11 ma 24 prawidłowe Fix1, zero JWCD i zero model_ready
lub complete_pair_days: nowy komplet cen nie udaje sparowanej doby treningu.
Nie dodano ani nie odtworzono wstecznie dowodów przed-cutoff PK5 23.11.

Nie zmieniono wcześniejszych checkpointów korpusu, archiwów PostgreSQL,
produkcji JWCD 90D, FROZEN, Railway, PR #2 ani harmonogramów. Brak nowej
prognozy, score lub rankingu i brak zmian kodu parsera produkcyjnego.


## Checkpoint 2026-11-23 — atomowy import dwóch rzeczywistych dób Fix1

Dodano tests/test_real_two_day_manifest_archive.py. Test składa wyłącznie
w katalogu tymczasowym wspólny manifest zachowanych paczek 22/23.11, kopiując
oryginalne raw gzip i referencje CSV. Wpisy są celowo odwrócone, a loader
zwraca je w kolejności dat. Nie utworzono nowego oficjalnego dowodu ani
nie zmieniono istniejących fixture lub adaptera zapisu.

Test dry-run potwierdza pełną walidację dwóch dób / 48 okresów, zero zapisanych
okresów oraz identyczne bajty plików po odczycie. Test PostgreSQL wymusza
CheckViolation na ostatnim okresie drugiej doby, 23.11 22:00Z, i wymaga
wycofania cen, paczek i kwarantanny OBU dób, również pierwszej już przetworzonej.
Po usunięciu testowego ograniczenia ponowienie ma dać dwa INSERT / 48 okresów
z dokładnymi cenami, oryginalnym HTML i prawidłowymi hash/czasami zaakceptowanych
pobrań. Następny import ma dać dwa REPLAY / zero nowych okresów oraz
identyczny stan wszystkich trzech tabel.

Lokalny dry-run przeszedł (1/1); SQL jawnie pominięto z powodu braku lokalnego
PostgreSQL, do wykonania w istniejącym CI PostgreSQL 16 z bazą fix1_shadow_test
na loopback. Nie wykorzystuje się sekretów ani bazy produkcyjnej.
Nie zmieniono produkcji JWCD 90D, FROZEN, Railway, archiwów, PR #2,
harmonogramów lub reguł cutoff/publication_ts/retrieved_at. Nie dodano
sparowanej historii PK5, prognozy, score ani rankingu.


## Checkpoint 2026-11-23 — piąta rzeczywista obserwacja PK5 przed cutoff

Oficjalny PSE zwrócił HTTP 200 i 24 okresy dostawy 24.11. Publikacja
wszystkich okresów: 23.11 08:13:42.104 Europe/Warsaw. Początek pobrania
08:18:12.912361, koniec 08:18:19.542186, przed własnym cutoff 09:50.
Zachowano oryginalny JSON gzip (mtime=0) i capture.json osobno w
pse_pk5_delivery_2026-11-24_fifth, bez nadpisywania wcześniejszych obserwacji.

Raw: 18677 bajtów, SHA-256
5bc11ccd1a56b700fbffafb9d208c0b2ca95dc0bc6d47dc880304aa09c1fdc77.
snapshot_id:
pk5-capture-sha256:5e8bd37d651c7608c20935969b96a3ba5f115a81cecfe209c0536ab252eeb4b6.
H01 JWCD=8169.00000, H24=11982.00000 MW. Wszystkie 24 wartości zmieniły
się wobec pobrania o 05:16.

Nowa regresja potwierdza metadata HTTP/rozmiar/24 okresy, hash/ID, publikacje,
dokładne czasy i chronologię/cutoff. Wszystkie 120 kolejności pięciu zapisanych
obserwacji wybierają całe piąte pobranie; cztery starsze mają
SUPERSEDED_JWCD_PUBLICATIONS. Wybrane wartości zgadzają się z raw,
bez mieszania okresów i zmiany wejścia. Lokalnie 6/6 testów PK5 24.11 przeszło.

Brak jeszcze Fix1 24.11 do sparowania; nie dodano doby treningowej, prognozy,
score ani rankingu. Nie zastępuje to osobnego raportu 09:50. Produkcja
JWCD 90D, FROZEN, Railway, archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — szósta rzeczywista obserwacja PK5 przed 09:50

Pobrano z oficjalnego PSE komplet 24 okresów dostawy 24.11, HTTP 200 bez
zmiany URL. Publikacja wszystkich okresów: 23.11 09:13:42.117 Europe/Warsaw.
Początek pobrania: 09:19:27.368850, koniec: 09:19:34.382677, przed
własnym cutoff 09:50. Oryginalny JSON gzip (mtime=0) i capture.json
zapisano osobno w pse_pk5_delivery_2026-11-24_sixth.

Raw: 18677 bajtów, SHA-256
1bfff18989de46fe2fb00df77e8dd936551e47d3875db3f90ee996bb103c05c6.
snapshot_id:
pk5-capture-sha256:c6fde113a3de2d6714c221404f38890e6ed1c5736cd50ae809a2d669cc5738b4.
H01 JWCD=8108.00000, H24=11941.00000 MW. Wszystkie 24 wartości zmieniły
się wobec pobrania o 08:18. Starszych danych i metadata nie nadpisano.

Nowa regresja potwierdza metadata HTTP/24 okresy/rozmiar, hash/ID, publikacje,
dokładne czasy i chronologię przed cutoff. We wszystkich 720 kolejnościach
sześciu zapisanych obserwacji selekcja wybiera całe szóste pobranie;
pięć starszych ma SUPERSEDED_JWCD_PUBLICATIONS. Wszystkie wybrane wartości
są zgodne z raw; nie miesza się okresów ani nie zmienia wejścia.
Lokalnie 7/7 testów PK5 24.11 przeszło.

To zapis dowodu przed-cutoff, nie poranna prognoza lub zamiennik raportu 09:50.
Brak jeszcze Fix1 24.11 do sparowania; nie dodano okien treningu, score ani
rankingu. Produkcja JWCD 90D, FROZEN, Railway, archiwa, PR #2 i harmonogramy
pozostają bez zmian.


## Checkpoint 2026-11-23 — rzeczywisty HTTP 200 TGE bez opublikowanych cen 24.11

Pobrano oficjalną sesję TGE 23.11 dla dostawy 24.11: początek
23.11 10:12:11.049087 Europe/Warsaw, koniec 10:12:20.779548, HTTP 200,
URL zgodny z żądaniem. Tabela rdn zawiera wszystkie etykiety H01..H24,
ale KAŻDA cena Fixing I ma znak „-”. HTTP 200 i obecność 24 etykiet nie
oznaczają opublikowanego kompletu cen.

Zachowano wyłącznie surowy HTML gzip i capture.json w
tge_delivery_2026-11-24_unavailable. Raw ma 258957 bajtów; SHA-256:
b4afb7c156c20545cd9827c2b3912e83aaadaa0f610b034e30d8d952494de933.
Metadata zawiera oryginalny błąd XPath:
Fixing I price unavailable for 2026-11-24_H01.
Nie utworzono CSV referencyjnego ani manifestu dopuszczenia do archiwizacji.

Dwie nowe regresje przeszły lokalnie (2/2). Pierwsza weryfikuje dokładne
czasy/hash/rozmiar/HTTP i wszystkie 24 rzeczywiste etykiety z ceną „-”:
parser oraz niezależna ekstrakcja XPath odrzucają odpowiedź. Druga podaje
te same oryginalne bajty narzędziu capture z emulowanym odczytem HTTP
i potwierdza brak jakichkolwiek plików zaakceptowanej paczki. Nie zamienia
się brakujących cen na zero ani na inny fixing. Dowód źródłowy nie zmienia się.

To dowód jednego odczytu przed dostępnością cen, nie ustalenie godziny
oficjalnej publikacji. Brak Fix1 24.11 do sparowania z zachowanym PK5;
nie utworzono doby treningowej, prognozy, score lub rankingu. Nie zmieniono
parsera produkcyjnego, JWCD 90D, FROZEN, Railway, archiwów, PR #2
ani harmonogramów. Nie zastępuje to porannego raportu ani raportu 18:00.


## Checkpoint 2026-11-23 — kompletny Fix1 24.11 po odpowiedzi z brakującymi cenami

Nowe pobranie oficjalnej sesji TGE 23.11 zwróciło HTTP 200 i 24 ceny
Fixing I dostawy 24.11. Odczyt zakończono 23.11 o 11:14:34.948376
Europe/Warsaw. Oryginalny HTML gzip (mtime=0), CSV XPath i manifest
zapisano osobno w tge_delivery_2026-11-24. Wcześniejszy dowód z 10:12,
w którym wszystkie ceny miały „-”, pozostaje niezmieniony i niedopuszczony.
Nie utożsamiono końca nowego pobrania z dokładnym czasem oficjalnej publikacji.

HTML: 281609 bajtów, SHA-256
4c5dfb6aaea7e753dabe720595abd59962b5047334105d6dcb42b8a262ca3279.
CSV SHA-256:
484d664e1f954e30545cb9c9842a41708cc8a9c723e4bc70ecb59b210c058488.
H01=692.18, H24=896.86 PLN/MWh. Parser, XPath, CSV i jawna siatka UTC
są zgodne dla wszystkich okresów. XPath czyta ten sam oryginalny HTML,
nie jest odrębnym oficjalnym eksportem.

Nowe testy łączą tę paczkę z sześcioma odrębnymi zapisanymi obserwacjami
PK5 24.11. Obie kolejności dają identyczny raport i wybór całego sixth:
publikacja 09:13:42.117, początek pobrania 09:19:27.368850,
koniec 09:19:34.382677 Europe/Warsaw, przed własnym cutoff 09:50.
Pięć starszych obserwacji ma SUPERSEDED_JWCD_PUBLICATIONS, bez mieszania
okresów. H01 JWCD=8108.00000, H24=11941.00000 MW.

Dostępność Fix1 przypisano dopiero do 11:14:34.948376, bez cofania przed
cutoff. Lokalnie 3/3 nowe testy przeszły. Paczka wnosi jedną rzeczywistą
parę / 24 okresy; sama nie tworzy okien treningu 30/60/90D. Walk-forward
dla tej pojedynczej paczki przed i w chwili dostępności Fix1 ma zero wspólnych
okresów i pusty ranking. Nie wydano prognozy ani nie zastąpiono raportu 09:50.

Produkcja JWCD 90D, FROZEN, Railway, starsze archiwa, checkpointy korpusu,
PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — scoring kontrolny rzeczywistych źródeł 24.11

Dodano tests/test_tge_november24_scores.py. Jawnie syntetyczny wektor
JWCD/20 korzysta z szóstego PK5 24.11, którego publikacja i oba czasy
pobrania poprzedzają cutoff 23.11 09:50 Europe/Warsaw. To kontrola
mechanizmu scoringu, nie wytrenowany model, ocena produkcji lub wydana prognoza.
Ceny Fix1 pochodzą z zachowanego HTML/CSV 24.11 z dostępnością dopiero
od końca pobrania 23.11 11:14:34.948376 Europe/Warsaw.

Niezależny rachunek Fraction łączy etykiety oryginalnego PSE i CSV TGE,
nie używając wyjścia score_day. Test sprawdza wszystkie metryki do 12 miejsc:
MAE=440.462500000000 PLN/MWh, bias=-440.462500000000 PLN/MWh,
MAPE=40.578297996866%, WAPE=41.498908656941%. Są 24 okresy i 24 niezerowe
ceny rzeczywiste. Wyniki dotyczą wyłącznie kontrolnego wektora JWCD/20.

Test integracyjny archiwizuje rzeczywistą paczkę w osobnej bazie testowej.
Mikrosekundę przed dostępnością Fix1 wymaga odmowy scoringu i pustej tabeli
wyników. W chwili dostępności ma powstać jeden INSERT z metrykami zgodnymi
z oracle; kolejne wywołania z referencją i bez niej mają dać REPLAY,
niezmieniony zapis wyniku, dokładne 24 ceny archiwalne i brak kwarantanny.

Lokalny test metryk przeszedł (1/1). SQL jawnie pominięto lokalnie przy braku
PostgreSQL; jego wykonanie przewidziano w istniejącym CI PostgreSQL 16
na loopback / fix1_shadow_test. Nie wykorzystuje się sekretów produkcyjnych.
Nie zmieniono adapterów scoringu, JWCD 90D, FROZEN, Railway, istniejących
archiwów, PR #2 ani harmonogramów; nie dodano rankingu lub pobrania live.


## Checkpoint 2026-11-23 — odtwarzalny audyt czterech sparowanych dób z luką 23.11

Zapisano nowy docs/validation/paired_source_audit_2026-11-24.json z jawnymi
ścieżkami paczek i source_commit a1a2c9883b8a3ada96a4e98c48b71d9054bad19f.
Obejmuje dostawy 20/21/22/24.11, 96 unikalnych okresów UTC, cztery kompletne
pary i cztery doby poprawne w audycie wierszy. Najdłuższy ciąg wynosi trzy
doby. Zachowano dowody Fix1 i kandydatów PK5; dla 24.11 sześć obserwacji
wybiera całe sixth sprzed cutoff. Starszego checkpointu trzech dób nie zmieniono.

Zakres celów 23–24.11 jawnie wykazuje brak SPAROWANEJ doby 23.11. Jej
rzeczywisty Fix1 jest zachowany osobno, lecz brak dowodu PK5 sprzed własnego
cutoff, więc nie dopisano go do model_ready ani nie uzupełniono interpolacją.
Ocena odbywa się na jawny czas dostępności Fix1 24.11:
23.11 11:14:34.948376 Europe/Warsaw. Dla celu 24.11 istnieją trzy wcześniejsze
doby źródłowe z cenami dostępnymi przed cutoff celu; brakuje 27/57/87 dni
w oknach 30/60/90D. Brak wspólnych okresów i rankingu sześciu modeli.
To poprawność źródeł, nie gotowość okien treningowych.

Lokalnie 2/2 nowe testy przeszły. Wszystkie 24 kolejności czterech paczek
odtwarzają zapisany raport; loader doba po dobie jest zgodny z dotychczasowym
API i nie zmienia hashy źródeł. CLI dla obu kolejności zachowuje 96 wierszy
historii mimo ograniczenia celu do dwóch dni i daje identyczny pełny wynik.
Pokrycie celu: dwa dni kalendarza, jeden bez pary, jeden dopuszczony źródłowo
ale niescorowany, zero dni na wspólnej próbce.

Brak nowych pobrań live lub zapisów PostgreSQL. Produkcja JWCD 90D, FROZEN,
Railway, istniejące archiwa, PR #2 i harmonogramy pozostają bez zmian.
Nie wydano prognozy ani nie zastąpiono raportu 09:50 lub 18:00.


## Checkpoint 2026-11-23 — walidacja granic dat przed odczytem historii

evaluate_csv_rows odrzuca granice inne niż dokładny typ date oraz date.min
i date.max przed materializacją wejściowych wierszy. None nadal oznacza
brak ograniczenia, a odwrócony zakres zachowuje dotychczasowy błąd.
Chroni to obliczenia kalendarza i cutoff przed skrajnymi datami oraz przed
przypadkowym przekazaniem datetime, tekstu lub bool do API.

Dwa nowe testy regresji obejmują obie granice API z iteratorem, którego
nie wolno odczytać, oraz CLI z datami 0001-01-01 / 9999-12-31 i nieistniejącym
CSV. CLI zwraca kod 2 z komunikatem walidacji, pustym stdout, bez traceback
i bez otwarcia pliku. Lokalnie wszystkie 4/4 testy modułu błędów wejścia CLI
przeszły, w tym wcześniejsze przypadki uszkodzonych CSV i pełny raport
diagnostyczny poprawnych, niekompletnych danych. Pełny zestaw i izolowany
PostgreSQL pozostają do sprawdzenia w istniejącym CI po zapisie commitu.

Nie zmieniono selekcji danych, definicji D-1 09:50 Europe/Warsaw ani metryk
i modeli. Brak nowych pobrań live. Produkcja JWCD 90D, FROZEN, Railway,
istniejące archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — atomowy rollback kwarantanny przy imporcie dwóch rzeczywistych dób

Rozszerzono tests/test_real_two_day_manifest_archive.py o integrację paczek
Fix1 22/23.11 (48 okresów), bez modyfikacji zachowanych źródeł.
Test w osobnej bazie PostgreSQL wstawia 22.11, celowo zmienia jedną cenę
wyłącznie w tej bazie, a następnie importuje obie doby. Kwarantanna 22.11
powstaje przed kontrolowanym błędem CHECK na ostatnim okresie 23.11.
Po błędzie wszystkie trzy tabele mają być identyczne jak przed importem:
brak trwałej kwarantanny oraz brak częściowej doby 23.11.

Po usunięciu testowego CHECK cały manifest ma dać QUARANTINE dla 22.11
i INSERT 24 okresów 23.11. Konfliktowe ceny nie mogą zostać naprawione
ani nadpisane; raport blokuje ich scoring i nie podaje accepted_capture.
Zdrowa doba zachowuje dokładne ceny, surowy HTML i czas oryginalnego pobrania.
Dwa kolejne importy mają dać QUARANTINE + REPLAY, zero wstawionych okresów,
identyczne tabele (w tym kwarantanna z czasem utworzenia) i niezmienione pliki.

Lokalny dry-run obu rzeczywistych paczek przeszedł: 1 test OK, 2 integracje
SQL jawnie pominięte przy braku lokalnego PostgreSQL. Nowy scenariusz SQL
wymaga wykonania w istniejącym CI z izolowanym PostgreSQL 16 po zapisie.
Nie zmieniono kodu adaptera ani źródeł; to rozszerzenie weryfikacji atomowości
i idempotencji na zachowanych danych. Poprzedni commit b3518dc przeszedł
pełne CI: 607 testów OK (run 45182707614).

Produkcja JWCD 90D, FROZEN, Railway, istniejące archiwa, PR #2 i harmonogramy
pozostają bez zmian. Nie wykonano pobrań live ani oceny modeli na brakujących
oknach 30/60/90D.


## Checkpoint 2026-11-23 — read-only audyt historii PK5 bez wymagania Fix1

Dodano shadow_pk5_history_audit.py z API audit_pk5_history i CLI przyjmującym
powtarzalne --source-day YYYY-MM-DD capture.json [capture.json ...].
Każda jawna grupa przechodzi istniejący loader oryginalnego gzip/metadata,
kontrolę hashy, publikacji, początku i końca pobrania względem własnego
D-1 09:50 Europe/Warsaw oraz wybór jednej całej obserwacji. Wynik zawiera
pełne dotychczasowe raporty dobowe, liczbę wybranych/niewybranych dób
i okresów oraz luki między najwcześniejszą a najpóźniejszą podaną dostawą.
Luki opisują wyłącznie dostarczony zbiór, nie stan zewnętrznego archiwum.

API odrzuca puste/błędne grupy, nieobsługiwane daty i powtórzoną dobę przed
odczytem plików. Wszystkie źródła muszą przejść walidację przed zwróceniem
raportu; CLI przy błędzie ma kod 2 i pusty stdout. --require-selection
zwraca kod 3 z pełnym JSON, jeśli któraś dostarczona doba nie ma wyboru;
nie traktuje samej luki między grupami jako błędu walidacji źródła.
model_readiness_assessed=false: PK5 bez Fix1 nie jest gotową historią modelową.

Cztery testy lokalne przeszły. Oryginalne obserwacje second/fourth dla 22.11
oraz early/sixth dla 24.11 dają dwa wybory / 48 okresów PK5 i jawną lukę 23.11.
Wszystkie osiem kolejności grup i kandydatów zachowuje identyczne raporty,
wybór fourth/sixth i własne cutoff 21/23.11 08:50 UTC. CLI/API są zgodne.
Testy obejmują też duplikat ID bez wyboru, kod 3, uszkodzony hash w obu
kolejnościach grup bez częściowego JSON oraz niezmienność plików źródłowych.

Przykład audytu dwóch pojedynczych zachowanych obserwacji:
python shadow_pk5_history_audit.py --source-day 2026-11-22 tests/fixtures/pse_pk5_delivery_2026-11-22_fourth/capture.json --source-day 2026-11-24 tests/fixtures/pse_pk5_delivery_2026-11-24_sixth/capture.json --require-selection

Poprzedni etap 35386ba przeszedł pełne CI: 608 testów OK (run 45192025724),
w tym rzeczywisty scenariusz rollbacku kwarantanny w izolowanym PostgreSQL.
Nowy moduł nie wykonuje sieci/SQL/zapisów ani prognoz; nie pobrano nowych
danych i nie uzupełniono brakujących dni. Produkcja JWCD 90D, FROZEN,
Railway, istniejące archiwa, PR #2 i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — kalendarzowe pokrycie PK5 dla dób 23/24/25h

Raport shadow_pk5_history_audit rozszerzono o calendar_coverage_by_hours.
Osobno dla 23/24/25h podaje doby kalendarza, brakujące w dostarczonym
zbiorze, dostarczone bez wyboru oraz wybrane, a także okresy kalendarza,
wybrane i niepokryte. Zakres nadal obejmuje tylko pierwszą–ostatnią podaną
dostawę; długość doby wynika z istniejącej siatki UTC Europe/Warsaw.
Brak grupy źródeł i grupa bez dopuszczonej obserwacji są rozróżnione.

Rzeczywiste obserwacje 22/24.11 dają trzy doby 24h: 72 okresy kalendarza,
48 wybranych i 24 niepokryte z powodu luki 23.11. Przy duplikacie ID dla
24.11 pozostaje 24 wybranych i 48 niepokrytych okresów, rozdzielonych
na jedną brakującą dobę i jedną dostarczoną bez wyboru. Dotychczasowe testy
sprawdzają nowe pola także we wszystkich kolejnościach paczek i przez CLI,
przy zachowaniu niezmienności plików źródłowych.

Dodano dwa testy: wyłącznie kalendarzowe luki wokół 29.03.2026 i 25.10.2026
mają odpowiednio 23 i 25 brakujących okresów (łącznie z sąsiednimi dobami
71/73 okresy), a pojedyncza dostarczona doba bez wyboru nie jest oznaczana
jako brakująca. Te testy nie zawierają nowych obserwacji PK5 ani dowodów
dopuszczenia rzeczywistych źródeł 23/25h. Lokalnie cały moduł: 6/6 OK.

Nie zmieniono wyboru snapshotów ani kontroli publication_ts i obu czasów
pobrania względem własnego D-1 09:50 Europe/Warsaw. PK5 nadal nie dowodzi
gotowości modelu bez Fix1 (model_readiness_assessed=false). Poprzedni commit
dd63158 przeszedł pełne CI: 612 testów OK (run 45203006580).
Brak nowych pobrań live, zapisów SQL, prognoz lub uzupełniania brakujących
dni. Produkcja JWCD 90D, FROZEN, Railway, istniejące archiwa, PR #2
i harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — kwarantanna po scoringu blokuje także REPLAY

Rozszerzono tests/test_tge_november24_scores.py o scenariusz integracyjny
na rzeczywistym Fix1 24.11 i szóstym PK5 z publikacją/pobraniem przed
własnym cutoff 23.11 09:50 Europe/Warsaw. Prognoza JWCD/20 pozostaje
wyłącznie syntetycznym wektorem kontrolnym, nie modelem lub wydaną prognozą.

Test najpierw zapisuje 24 ceny i wynik scoringu w chwili dostępności Fix1.
Dopiero potem celowo zmienia jedną cenę w izolowanej bazie testowej i przez
archive_batch wykrywa konflikt, tworząc kwarantannę bez nadpisania cen.
Cztery próby scoringu (dotychczasowy/nowy forecast_id, oryginalny CSV/brak
CSV) mają zostać odrzucone jako Quarantined delivery day, także sekundę po
dostępności Fix1. Istniejący wynik nie może pozwolić ominąć bieżącej kontroli
przez REPLAY. Test porównuje pełną zawartość scores/prices/batches/quarantine
przed i po odmowach, łącznie z hashami, payload i czasami zapisów.

Dwa kolejne importy tej samej paczki muszą nadal zwrócić QUARANTINE,
bez naprawiania celowo uszkodzonej ceny, usuwania kwarantanny lub zmiany
wcześniejszego wyniku. Stary wynik pozostaje zachowany jako zapis historyczny;
test sprawdza odmowę API scoringu, nie usuwa i nie przepisuje tabeli scores.

Lokalnie niezależny test czterech metryk na rzeczywistych źródłach przeszedł
(1 OK); dwie integracje PostgreSQL jawnie pominięto z powodu braku lokalnego
serwera. Nowy scenariusz wymaga pełnego istniejącego CI z izolowanym
PostgreSQL 16. Nie zmieniono adapterów scoringu ani archiwizacji.
Poprzedni etap 30cc046 przeszedł CI: 614 testów OK (run 45215625757).

Brak nowych pobrań live, produkcyjnych połączeń lub wydanych prognoz.
Produkcja JWCD 90D, FROZEN, Railway, istniejące archiwa i pliki źródłowe,
PR #2 oraz harmonogramy pozostają bez zmian.


## Checkpoint 2026-11-23 — rzeczywisty ponowny odczyt Fix1 24.11 i REPLAY

Oficjalna sesja TGE 23.11 została ponownie odczytana dla dostawy 24.11.
HTTP 200, odczyt zakończony 23.11 o 19:17:07.872014 Europe/Warsaw.
Nową paczkę zapisano osobno w tests/fixtures/tge_delivery_2026-11-24_recaptured:
dokładny HTML jako gzip mtime=0, CSV XPath z jawnymi okresami UTC i manifest.
Poprzedniej paczki z 11:14:34.948376 Europe/Warsaw nie zmieniono.
Końca pobrania nie utożsamiono z dokładnym czasem oficjalnej publikacji.

Nowy HTML: 425640 bajtów, SHA-256
f2bcba1575766fd4f7ee1bc0cd70d660b6cdc1510a2f11b6cabd659cb87f0cff.
Różni się od poprzedniego HTML (281609 bajtów), ale wszystkie 24 ceny
Fixing I są identyczne. CSV również jest identyczny bajtowo:
SHA-256 484d664e1f954e30545cb9c9842a41708cc8a9c723e4bc70ecb59b210c058488.
H01=692.18, H24=896.86 PLN/MWh. Parser, XPath oraz walidowane partie UTC
dają te same wartości. XPath czyta ten sam oficjalny HTML, nie osobny eksport.

Dodano tests/test_tge_november24_recapture.py: test źródeł porównuje cały
nowy HTML/CSV z wcześniejszym dowodem i sprawdza niezmienność plików.
Test PostgreSQL wstawia wyłącznie pierwszą paczkę, a następnie importuje
kolejno nową, pierwszą i nową. Każda ma dać REPLAY / zero wstawionych okresów,
bez kwarantanny; ceny i pierwotny wiersz źródła mają pozostać identyczne. accepted_capture
musi nadal wskazywać pierwszy hash i czas dostępności, bez przesunięcia go
na godzinę ponownego pobrania. Nowy raw jest zachowany jako druga obserwacja, bez przepinania 24 cen
na nowy batch. Kolejne importy mają pozostawić wszystkie tabele identyczne.

Lokalnie test źródeł przeszedł (1 OK); SQL jawnie pominięto przy braku
lokalnego PostgreSQL i pozostawiono do istniejącego izolowanego CI.
Poprzedni commit 5a8dbdc przeszedł CI: 615 testów OK (run 45225414738).
To kolejny odczyt tej samej doby, nie nowy dzień treningowy ani dowód
historycznego PK5; nie zwiększa kompletności okien 30/60/90D.

Nie zmieniono adapterów, produkcji JWCD 90D, FROZEN, Railway, istniejących
archiwów i wcześniejszych fixture, PR #2 ani harmonogramów. Nie wydano
prognozy i nie zastąpiono raportu 09:50 lub 18:00.


Weryfikacja SQL pierwszej wersji testu (run 45237109122) wykazała błędne
założenie testowe o braku dodatkowego batch przy REPLAY. Adapter zachowuje
nowy surowy dowód bez zmiany zaakceptowanych cen i źródła. Poprawiono test:
po pierwszym ponowieniu wymaga dwóch obserwacji raw, oryginalnego przypisania
cen i czasu accepted_capture, a kolejne importy porównują całe tabele bez
dalszych przyrostów. Kod adaptera pozostaje bez zmian.

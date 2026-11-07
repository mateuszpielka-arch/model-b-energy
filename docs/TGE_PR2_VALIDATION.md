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

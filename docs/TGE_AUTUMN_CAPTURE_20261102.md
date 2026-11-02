# Checkpoint 2026-11-02 — rzeczywista doba 25h TGE

Baza 07c95ebd2e971a13a427dc35a0422888d0a138f0. Ref ponownie sprawdzony przed zapisem;
PR #2 open, bez ingerencji. Produkcja/90D/FROZEN/archiwa bez zmian.

Z kodu strony ustalono parametr dateShow (data sesji, nie dostawy). Pobranie:
https://tge.pl/energia-elektryczna-rdn?dateShow=24-10-2026
HTTP 200, 442277 bajtów. Zachowano oryginalne bajty i manifest SHA/czas/URL
w tests/fixtures/tge_rdn_session_2026-10-24_captured_2026-11-02.*.
W odpowiedzi instrumenty dostawy 25.10 mają kolejność H01,H02,H02a,H03..H24,
25 odrębnych etykiet. H02a ma inną cenę niż H02 i H03. Są też ceny ujemne.
Jest to bezpośredni dowód oryginalnego HTML, nie niezależny eksport referencyjny.
Próba drugiego kanału web open historycznego URL nie udała się; nie deklarujemy
niezależnego porównania wszystkich 25 cen ani finalnej walidacji DST.

Wykryto, że pomocniczy parse_fix1/parse_verified_layout zwracał 24 wartości,
pomijając H02a. Standard-day admission i tak BLOKOWAŁ tę dobę, więc nie stanowi
to dowodu zapisu błędu przez nową ścieżkę SHADOW. Produkcyjny kolektor to inny kod.
Poprawka: pomocniczy parser teraz zgłasza unsupported label zamiast cicho pomijać.
Wydzielono wspólną kontrolę nagłówków/tabeli _validated_rdn_rows.

Nowy tge_period_labels.parse_fix1_labels zachowuje pełne uporządkowane etykiety
z cenami Decimal (24h i jesienne25h). Braki, duplikaty, zmiana kolejności, nieznane
etykiety i błędne ceny blokują wynik. 23h wiosenne nadal jawnie zablokowane.
Nie przypisuje H02a ani innych etykiet jesiennych do UTC; semantyka musi być
potwierdzona dokumentacją/niezależnym eksportem i zgodnością PSE. Nie zakładać,
że numer TGE równa się numerowi lokalnej godziny startu albo pozycji UTC.
Admission do archiwum/scoringu pozostaje ograniczone do standardowych dób.

Dodano 7 testów: oryginalne25 etykiet i wybrane ceny, brak cichej utraty H02a,
blokada standard admission DST, brak/duplikat H02a, jawna blokada wiosny,
regresja zwykłej doby. Testy nie są dowodem oficjalnego mapowania UTC.
Lokalnie:105 testów,93 OK,12 PostgreSQL SKIPPED. Oczekiwanie na pełne CI.
Rozszerzono ścieżki CI o tge_*.py, nadal wyłącznie gałąź testową.

Nie ukończono 7 kolejnych niezależnie zweryfikowanych dni, mapowania DST,
importu historii XLSB ani rzeczywistego rankingu. Ten krok dostarcza brakujący
oryginalny przypadek25h i eliminuje utratę etykiety w kodzie SHADOW.

## Dowód CI

https://github.com/mateuszpielka-arch/model-b-energy/actions/runs/41508226888
Commit 8e3e6286cb6e936d6078e1c8b66d4961c3595b13; job 125492955445:
completed/success. Odczytane logi:105 testów,OK,bez pominięć, także PostgreSQL.
Wynik nie oznacza potwierdzenia mapowania UTC ani wdrożenia DST do archiwum.

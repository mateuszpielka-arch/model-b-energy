# Checkpoint 2026-11-02 — siedem historycznych sesji TGE

Baza 23719916c3f8e5a2c210974ab1eb618fd03d99d4; PR #2 open, bez ingerencji.
Przed zapisem ponownie sprawdzono ref gałęzi testowej, brak nowych cudzych zmian.

Wykonano pobranie siedmiu kolejnych dat dostawy 27.10–02.11.2026, parametrem
dateShow ustawiając sesję D-1. Wszystkie HTTP 200. Oryginalne odpowiedzi, czas
zakończenia pobrania, URL i SHA256 raw/CSV w tests/fixtures/tge_history_2026-10-27_2026-11-02.

Dodano tools/capture_tge_validation.py: jawne uruchomienie read-only, maksymalnie
7 dni i 2 równoczesne pobrania. Brak baz/sekretów/importów produkcyjnych. Opcjonalny
lxml (dostępny lokalnie) jest niezależnym mechanizmem parsowania, którego XPath
odczytuje cenę z oficjalnej tabeli RDN po kontroli grupy Fixing I i jednostki PLN/MWh.
CSV referencyjne NIE są generowane przez tge_parser_safe. Oba mechanizmy czytają
TEN SAM oficjalny HTML; nie jest to niezależny oficjalny eksport ani dowód
nieomylności źródła. Zachowano tę informację w manifeście.

Nowy test porównuje wszystkie 168 cen parsera testowanego z referencją lxml,
sprawdza hashe obydwu artefaktów i kompletność/ciągłość dat. Osobno test braku
przejścia na nieobecną datę. Łącznie 3 nowe testy z subtestami każdego dnia.
Lokalnie 108 testów: 96 OK, 12 PostgreSQL SKIPPED. Oczekiwanie na pełne CI.

Nie utożsamiać siedmiu sesji historycznych pobranych jednego dnia z 7 dniami
operacyjnej obserwacji SHADOW. Ta druga bramka nie jest spełniona. Nie włączono
nowego parsera do kolektora, nie zapisano cen do istniejącego PostgreSQL i nie
naprawiano archiwów. Nie zmieniono modelu 90D, FROZEN, konfiguracji Railway ani PR #2.
Mapowanie oficjalnych etykiet DST/UTC i sparowana historia JWCD nadal wymagają pracy.

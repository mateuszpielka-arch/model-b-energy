# Checkpoint 2026-11-02 — drugi rzeczywisty dzień parsera

Baza gałęzi test/tge-layout-validation-20261101: 954e0755268b01a797ec859ed7395d348054a91f.
Przed zapisem remote ref bez nowych zmian. PR #2 open, head 5e993ee, bez ingerencji.

Pobrano HTTP 200 z https://tge.pl/energia-elektryczna-rdn (388924 bajty).
Zapisano oryginalne bajty jako tests/fixtures/tge_rdn_captured_2026-11-02.html,
manifest czasu pobrania i SHA256 oraz CSV cen referencyjnych na 2026-11-03.
Hash raw: b9a458939b229676b8d23933e97dfeb43ed551cd2645aa0d9b4c53baa4a2ece4.

Wszystkie 24 ceny H01–H24 przepisano z niezależnego renderowania oficjalnej tabeli
TGE (web open/find), z kolumny Fixing I / Kurs PLN/MWh / instrument 60.
CSV NIE powstał z wyniku tge_parser_safe. To niezależna ścieżka odczytu tej samej
strony, a nie drugi oficjalny eksport. Wynik parsera: 24/24 zgodne w Decimal.

4 nowe testy: pełne porównanie cen; zgodność hashy/admission batch;
rozróżnienie Fixing I od notowań ciągłych dla H01/H07; blokada nieobecnej daty.
python -m unittest discover -s tests -q: 46 testów, 40 OK, 6 PostgreSQL SKIPPED lokalnie.
Ostatnie sześć wymaga disposable PostgreSQL z CI; lokalnie bazy nadal brak.
Testowany kod/fixtures commit 92406984962435d44e0362f525264d982d6c32b7.

Obecnie posiadamy surowe odpowiedzi i porównanie pełnej doby dla dwóch dat dostawy:
02.11 i 03.11. Nie osiągnięto jeszcze bramki 7 kolejnych dni ani oficjalnego
mapowania DST. Nie dokonano importu do istniejącego archiwum, scoringu prognozy,
backtestu ani rankingu. Parser/kolektor/model produkcyjny, FROZEN i PR #2 bez zmian.

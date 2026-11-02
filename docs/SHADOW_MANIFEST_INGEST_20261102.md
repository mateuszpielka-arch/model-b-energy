# Checkpoint 2026-11-02 — wejście manifest -> walidacja -> staging PostgreSQL

Baza 3027b8d78d78026364b86815c95c8e317c42198e. PR #2 sprawdzony open.
Nie zmieniono kolektora, produkcyjnego 90D, FROZEN, archiwów ani Railway.

Dodano shadow_ingest_manifest.py: domyślny dry-run bez bazy/sieci/sekretów.
Wczytuje manifest, sprawdza HTTP200, hashe raw/CSV, zgodność dat CSV z manifestem,
kompletność cen przez prepare_batch oraz ścieżki plików ograniczone do katalogu
manifestu. Błąd dowolnego wejścia zatrzymuje walidację PRZED DDL/zapisem.
--write wymaga jawnego DSN lokalnej bazy fix1_shadow_test*; adapter odrzuca zdalne
hosty/nazwy produkcyjne i nadal nie odczytuje DATABASE_URL.

Po walidacji wszystkich plików zapisuje każdą dobę własną transakcją istniejącego
adaptera. To atomowość PER DOBA, nie transakcja całego zakresu7dni. Przy awarii DB
po kilku dniach wcześniejsze doby mogą być zapisane; ponowienie jest idempotentne.
Konflikty trafiają do kwarantanny bez nadpisania historycznych cen.

Poprawiono provenance w shadow_archive_batch.py: historyczne adresy dateShow są
akceptowane TYLKO na https://tge.pl/energia-elektryczna-rdn, z jedynym parametrem
sesji dokładnie D-1 (brak fragmentów/innych hostów/innych parametrów). Konkretny
historyczny URL zachowywany teraz w ArchiveBatch i DB, nie zastępowany adresem
strony bieżącej. Wersja admission rdn-layout-shadow-v2. Zwykły URL nadal działa.

Rzeczywisty dry-run manifestu27.10–02.11:7 dni,168 okresów,wszystkie VALIDATED_DRY_RUN.
Raport maszynowy docs/SHADOW_MANIFEST_DRYRUN_20261102.json. Źródłem referencji jest
niezależny parser lxml tego samego HTML TGE, nie niezależny oficjalny eksport.

5 nowych testów:7 realnych sesji dryrun; blokada wyjścia ścieżką poza katalog;
zła sesja/hash; PostgreSQL7dni insert/replay (168 cen,7paczek,konkretne URL);
błąd manifestu bez zapisów. Dane nie trafiają do produkcyjnej bazy.
Lokalnie113testów:99OK,14PostgreSQL SKIPPED. Wymagane potwierdzenie CI.

To wejście CLI do testowego stagingu, NIE wdrożony codzienny kolektor i NIE import
naprawczy istniejących archiwów. DST, pełna historia JWCD/XLSB i realny ranking
nadal wymagają dalszej walidacji.

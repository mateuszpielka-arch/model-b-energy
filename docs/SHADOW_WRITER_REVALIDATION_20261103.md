# Checkpoint 2026-11-03 — ponowna walidacja na granicy zapisu

Baza d823e93a920424883444081744d3d17f6dea4b5c; odczytano PR#2(open) i ref gałęzi.
Nie ma nowych cudzych zmian w bazie przed przygotowaniem commitu.

Znalezione ryzyko w adapterze SHADOW: ArchiveBatch jest frozen dataclass, ale caller
może utworzyć nową instancję np. dataclasses.replace z inną ceną i poprawnym hashem
niezmienionego HTML. Poprzednia bramka sprawdzała hash/kompletność, lecz nie
przeliczała ponownie zgodności wszystkich cen z raw bezpośrednio przed zapisem.
Nie jest to dowód błędu istniejącego archiwum ani problem produkcyjnego modelu.

Dodano revalidate_batch i użyto w archive_batch PRZED transakcją/zapisem:
- ponowne sprawdzenie raw hash, wspieranej wersji admission, kompletności/unikalności
  okresów UTC i skończonych cen;
- rekonstrukcja canonical batch przez prepare_batch z oryginalnego HTML, zgodność
  każdej ceny, dnia, URL historycznej sesji i świadomego timestampu retrieval;
- niepoprawna paczka wywołuje błąd przed INSERT, nie trafia jako poprawny actual
  ani jako konflikt z dobrym archiwum. Konflikty prawidłowych źródeł nadal obsługuje
  dotychczasowa kwarantanna bez nadpisania cen.
Hash referencji nadal wymaga niezależnego dowodu po stronie loadera; walidacja
nie certyfikuje autentyczności zewnętrznego pliku lub podpisu TGE.

8 nowych testów: canonical/reordered batch; zmiana ceny przy poprawnym rawhash;
wersja/URL; naive timestamp/referencehash; złe/duplikowane okresy; NaN/zmiana raw;
PostgreSQL zero zapisów po podmianie ceny; dobry zapis pozostaje nietknięty po
próbie podmienionego replay. Dane testowe oparte na oryginalnym zapisanym HTML.
Lokalnie125testów:105OK,20PostgreSQL SKIPPED. Oczekiwanie na pełne CI.

Nie zmieniono produkcji, FROZEN, 90D, istniejących archiwów, Railway ani PR#2.
Aktualny audyt bazy nadal wymaga dostępu read-only/eksportu; realny ranking niewykonany.

## Dowód CI

https://github.com/mateuszpielka-arch/model-b-energy/actions/runs/41543665441
Commita3eb4faf5e36df53ce52324fff4b730e99c0ed79;job125613767351 completed/success.
Logi:125testów,OK,bez pominięć,także20 integracyjnych PostgreSQL.
Potwierdzono zero zapisów po podmianie ceny oraz nienaruszone wcześniejsze dane.

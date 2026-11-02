# Checkpoint 2026-11-02 — przygotowanie paczek archiwalnych

Baza: dc77933b74033504eae4e56f6b7210e9f6776bfa, gałąź test/tge-layout-validation-20261101.
PR #2 sprawdzony: open. Nie zmieniano jego stanu ani kodu produkcyjnego.

Dodano shadow_archive_batch.py i 7 testów w tests/test_shadow_archive_batch.py.
prepare_batch wymaga surowego HTML, URL TGE, świadomego strefy czasu pobrania,
pełnego zestawu cen referencyjnych i SHA256 artefaktu referencyjnego. Porównuje
każdą cenę, a nie średnią. Zwraca niezmienny batch z hashem surowych bajtów,
hashem referencji, wersją parsera, 24 okresami UTC i cenami Decimal.

classify_existing zwraca INSERT, REPLAY lub QUARANTINE. Zmiana ceny, częściowa
doba i duplikaty kierują do kwarantanny. Funkcja nie modyfikuje danych.
Identyfikator paczki nie zmienia się przy ponownym pobraniu identycznego źródła.
To plan decyzji; transakcyjny adapter DB musi wykonać sprawdzenie/zapis pod blokadą.

Wykonano: python -m unittest discover -s tests -q -> 36/36 OK.
7 nowych testów korzysta z rzeczywistego zapisanego HTML i pliku referencyjnego
na 02.11, w tym celowego konfliktu H08. Pozostałe 29 testów również przechodzi.
Nie pobrano w tym cyklu nowych danych TGE. Nie policzono nowych wyników modelu.

Ograniczenia i blokada:
- Brak lokalnych postgres/initdb i psycopg; próba przygotowania zależności napotkała
  błędy uprawnień apt, kolejna nie zakończyła się i została przerwana.
- Nie uruchomiono izolowanej bazy. NIE wykonano testu atomowości, współbieżności,
  rollbacku ani trwałej idempotencji PostgreSQL. Nie ma jeszcze adaptera zapisującego.
- Moduł weryfikuje zgodność z przekazaną referencją, nie jej autentyczność.
  Caller musi przechować i niezależnie zweryfikować artefakt referencji.
- Standardowe 24h tylko; DST nadal blokowane przez parser. Format daty/UTC
  paczki nie stanowi jeszcze dowodu prawidłowego mapowania oficjalnych etykiet DST.
- Brak zapisów lub konfiguracji Railway, brak dostępu do produkcyjnych sekretów.

Następny krok: izolowany PostgreSQL, osobny staging/quarantine i adapter transakcji;
blokada całej doby oraz testy konfliktów/rollbacku/równoległości na prawdziwym silniku.

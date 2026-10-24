# ADR-0005: Scheduler powiadomień jako pętla asyncio

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

Subskrypcje sprawdzamy cyklicznie. Wiele subskrypcji może dotyczyć tego samego
świadczenia i województwa, a nie chcemy dublować żądań do NFZ.

## Decyzja

- Prosta pętla `asyncio` (`termin/notify/watcher.py`) zamiast APScheduler:
  jedno zadanie, stały interwał (domyślnie 60 min) z jitterem.
- W każdym przebiegu subskrypcje są grupowane po kluczu zapytania
  (świadczenie, przypadek, dzieci, zbiór województw). Jedno zapytanie do NFZ
  obsługuje całą grupę, a filtr obszaru i daty stosujemy lokalnie.
- Deduplikacja: powiadamiamy tylko o terminie **wcześniejszym** niż ostatnio
  zgłoszony i nie częściej niż co `cooldown` (domyślnie 12 h).
- Pętla działa w procesie web (`termin serwer --watcher`) albo osobno
  (`termin watcher`).

## Konsekwencje

- Brak zależności i łatwe testy (`run_once()` z zamockowanym NFZ).
- Brak rozproszenia na wiele instancji. Przy jednej instancji to nie problem.

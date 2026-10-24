"""Metryki Prometheus wspólne dla klienta NFZ i serwera web (`/metrics`)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

NFZ_REQUESTS = Counter(
    "termin_nfz_requests_total",
    "Żądania HTTP do API NFZ (bez trafień cache)",
    ["endpoint", "outcome"],
)
NFZ_ERRORS = Counter(
    "termin_nfz_errors_total",
    "Błędy API NFZ po wyczerpaniu ponowień albo błędy 4xx",
    ["endpoint", "kind"],
)
CACHE_LOOKUPS = Counter(
    "termin_cache_lookups_total",
    "Odczyty cache odpowiedzi NFZ",
    ["result"],
)
NFZ_LATENCY = Histogram(
    "termin_nfz_request_seconds",
    "Czas odpowiedzi API NFZ",
    ["endpoint"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20),
)
SEARCHES = Counter("termin_searches_total", "Wyszukiwania", ["channel"])
NOTIFICATIONS = Counter(
    "termin_notifications_total", "Wysłane powiadomienia", ["channel", "outcome"]
)

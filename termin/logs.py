"""Logowanie strukturalne (structlog) bez danych osobowych.

Procesor `redact_pii` usuwa wartości kluczy, które mogą zawierać dane osobowe
(lokalizacja użytkownika, adresy kontaktowe do powiadomień, tokeny). Dane placówek
(adres, telefon placówki) są publiczne, ale i tak ich nie logujemy, bo nie są potrzebne.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "lat",
        "lon",
        "latitude",
        "longitude",
        "gdzie",
        "where",
        "location",
        "address",
        "query_location",
        "email",
        "target",
        "chat_id",
        "token",
        "phone",
        "password",
        "smtp_password",
        "telegram_token",
        "ip",
        "client_ip",
        "code",
        "recovery_codes",
        "challenge",
        "secret",
        "totp_secret",
    }
)

REDACTED = "[ukryte]"


def redact_pii(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    """Konfiguruje structlog i stdlib logging. Bezpieczne do wielokrotnego wywołania."""
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=log_level, force=True)
    # httpx loguje pełne URL-e z parametrami (np. adres w Nominatim): wyciszamy.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # slowapi loguje każde odrzucone zapytanie: przy ataku to zalew logów (I/O, dysk).
    # Odrzucenia liczymy w metryce termin_rate_limited_total.
    logging.getLogger("slowapi").setLevel(logging.ERROR)

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_pii,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        # sys.stderr odczytywany przy każdym logu (nie przy konfiguracji): odporne na
        # podmianę strumienia (testy CLI, uvicorn).
        logger_factory=lambda *_: structlog.PrintLogger(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger

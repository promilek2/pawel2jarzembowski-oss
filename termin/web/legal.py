"""Strony prawne: regulamin, polityka prywatności, cookies, dostępność, kontakt.

Treść to szablony w `termin/web/legal_docs/*.html` uzupełniane danymi operatora z konfiguracji
(`TERMIN_OPERATOR_*`). Brakujące dane są wyraźnie oznaczone, żeby administrator instancji
wiedział, co uzupełnić przed publicznym uruchomieniem.
"""

from __future__ import annotations

import html
from importlib import resources
from string import Template

from termin.config import Settings
from termin.store.accounts import TERMS_VERSION
from termin.web.auth import session_cookie_name

LEGAL_PAGES = {
    "regulamin": "Regulamin",
    "prywatnosc": "Polityka prywatności",
    "cookies": "Cookies i pamięć przeglądarki",
    "dostepnosc": "Deklaracja dostępności",
    "kontakt": "Kontakt",
}

REQUIRED_OPERATOR_FIELDS = ("operator_name", "operator_address", "operator_email")


def missing_operator_fields(settings: Settings) -> list[str]:
    return [f for f in REQUIRED_OPERATOR_FIELDS if not getattr(settings, f)]


def _fill(value: str | None, label: str) -> str:
    if value:
        return html.escape(value)
    return f'<mark class="todo">[UZUPEŁNIJ: {html.escape(label)}]</mark>'


def legal_values(settings: Settings) -> dict[str, str]:
    email = settings.operator_email
    email_html = (
        f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>'
        if email
        else _fill(None, "e-mail kontaktowy (TERMIN_OPERATOR_EMAIL)")
    )
    security = settings.security_contact or email
    operator_id = settings.operator_id
    dpo = settings.dpo_email
    return {
        "public_url": html.escape(settings.public_url),
        "operator_name": _fill(settings.operator_name, "nazwa usługodawcy (TERMIN_OPERATOR_NAME)"),
        "operator_address": _fill(settings.operator_address, "adres (TERMIN_OPERATOR_ADDRESS)"),
        "operator_email": email_html,
        "operator_id_line": f", {html.escape(operator_id)}" if operator_id else "",
        "operator_id_dl": (
            f"<dt>Identyfikatory</dt><dd>{html.escape(operator_id)}</dd>" if operator_id else ""
        ),
        "dpo_line": (
            f" Inspektor ochrony danych: {html.escape(dpo)}."
            if dpo
            else " Administrator nie wyznaczył inspektora ochrony danych."
        ),
        "dpo_dl": f"<dt>Inspektor ochrony danych</dt><dd>{html.escape(dpo)}</dd>" if dpo else "",
        "security_contact": html.escape(security) if security else _fill(None, "kontakt"),
        "hosting_provider": _fill(
            settings.hosting_provider, "dostawca serwera (TERMIN_HOSTING_PROVIDER)"
        ),
        "ntfy_server": html.escape(settings.ntfy_server),
        "effective_date": html.escape(settings.legal_effective_date),
        "terms_version": TERMS_VERSION,
        "session_days": str(settings.session_days),
        "unverified_days": str(settings.unverified_account_days),
        "session_cookie": session_cookie_name(settings),
        "assistant_recipient": ASSISTANT_RECIPIENT if _assistant_ai(settings) else "",
        "assistant_where": (
            "; w trybie AI także u Anthropic (punkt 4)" if _assistant_ai(settings) else ""
        ),
    }


ASSISTANT_RECIPIENT = (
    "<li><strong>Anthropic PBC</strong> (USA; model AI Claude), tylko gdy zadajesz pytanie "
    "pomocnikowi „Jak to działa?”: otrzymuje treść pytania i wcześniejszych wiadomości tej "
    "rozmowy, żeby przygotować odpowiedź. Działa jako podmiot przetwarzający na podstawie "
    "warunków komercyjnych i umowy powierzenia Anthropic; dane z API nie służą do trenowania "
    "modeli. Przekazanie do USA odbywa się na podstawie mechanizmu z rozdziału V RODO "
    "przewidzianego w tej umowie (np. standardowych klauzul umownych). Przed pierwszym "
    "pytaniem pokazujemy o tym informację. Nie wpisuj w pomocniku danych osobowych ani "
    "informacji o zdrowiu.</li>"
)


def _assistant_ai(settings: Settings) -> bool:
    return settings.ai_enabled and settings.ai_api_key is not None


def render_legal(name: str, settings: Settings) -> str:
    """Treść strony prawnej (HTML do wstawienia w układ strony)."""
    raw = resources.files("termin.web.legal_docs").joinpath(f"{name}.html").read_text("utf-8")
    body = Template(raw).safe_substitute(legal_values(settings))
    missing = missing_operator_fields(settings)
    if missing:
        notice = (
            '<p class="todo-banner" role="note"><strong>Uwaga dla administratora:</strong> '
            "uzupełnij dane usługodawcy w konfiguracji (zmienne "
            + ", ".join(f"<code>TERMIN_{f.upper()}</code>" for f in missing)
            + ") przed publicznym uruchomieniem serwisu.</p>"
        )
        body = notice + body
    return body


def security_txt(settings: Settings, expires: str) -> str:
    contact = settings.security_contact or settings.operator_email
    lines = []
    if contact:
        lines.append(
            f"Contact: {'mailto:' + contact if '@' in contact and ':' not in contact else contact}"
        )
    lines += [
        f"Expires: {expires}",
        "Preferred-Languages: pl, en",
        f"Canonical: {settings.public_url}/.well-known/security.txt",
        "Policy: https://github.com/promilek2/pawel2jarzembowski-oss/blob/main/SECURITY.md",
    ]
    return "\n".join(lines) + "\n"

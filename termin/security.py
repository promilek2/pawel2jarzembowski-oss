"""Bezpieczeństwo: hasła (scrypt), tokeny, walidacja e-maila i siły hasła.

Bez zewnętrznych zależności: `hashlib.scrypt` jest w bibliotece standardowej, a parametry
(N=2^15, r=8, p=1) odpowiadają zaleceniom OWASP dla scrypt.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar
from urllib.parse import quote

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
PASSWORD_MIN = 10
PASSWORD_MAX = 128

_EMAIL = re.compile(
    r"^[^@\s<>()\[\],;:\"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+$"
)

# Najczęstsze hasła (krótka lista: pełne słowniki to zadanie dla menedżera haseł użytkownika).
_COMMON = frozenset(
    {
        "1234567890", "12345678910", "qwertyuiop", "password123", "haslo12345",
        "zaq12wsxcde", "qwerty1234", "1q2w3e4r5t", "polska1234", "1111111111",
        "0987654321", "abcdefghij", "iloveyou12", "admin12345", "termin1234",
    }
)  # fmt: skip


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


# Skrót do porównań, gdy użytkownik nie istnieje: stały czas odpowiedzi logowania.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str, email: str | None = None) -> str | None:
    """Komunikat, co jest nie tak z hasłem, albo None."""
    if len(password) < PASSWORD_MIN:
        return f"Hasło musi mieć co najmniej {PASSWORD_MIN} znaków."
    if len(password) > PASSWORD_MAX:
        return f"Hasło może mieć najwyżej {PASSWORD_MAX} znaków."
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) < 4:
        return "To hasło jest zbyt łatwe do odgadnięcia. Wybierz inne."
    if email and email.split("@")[0].lower() in lowered and len(email.split("@")[0]) >= 4:
        return "Hasło nie może zawierać nazwy z adresu e-mail."
    return None


def normalize_email(email: str) -> str | None:
    email = email.strip()
    if len(email) > 254 or not _EMAIL.match(email):
        return None
    local, _, domain = email.rpartition("@")
    return f"{local}@{domain.lower()}".lower()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Tokeny trzymamy w bazie tylko jako skrót: wyciek bazy nie daje dostępu do sesji."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------- haszowanie poza pętlą

T = TypeVar("T")

HASH_WORKERS = 4
"""Ile haszy scrypt liczymy naraz (każdy zajmuje ~32 MB RAM i kilkadziesiąt ms CPU)."""
HASH_QUEUE_MAX = 64
"""Ile operacji może czekać w kolejce; powyżej odmawiamy (ochrona przed zalewem logowań)."""

_hash_pool = ThreadPoolExecutor(max_workers=HASH_WORKERS, thread_name_prefix="termin-hash")
_pending = 0
_pending_lock = threading.Lock()


class BusyError(RuntimeError):
    """Serwer jest przeciążony (za dużo operacji w kolejce)."""


async def run_hashing(fn: Callable[[], T]) -> T:
    """Liczy hasz w osobnej, ograniczonej puli wątków: nie blokuje pętli zdarzeń i limituje RAM."""
    global _pending
    with _pending_lock:
        if _pending >= HASH_QUEUE_MAX:
            raise BusyError("Za dużo prób logowania naraz. Spróbuj za chwilę.")
        _pending += 1
    try:
        return await asyncio.get_running_loop().run_in_executor(_hash_pool, fn)
    finally:
        with _pending_lock:
            _pending -= 1


async def hash_password_async(password: str) -> str:
    return await run_hashing(lambda: hash_password(password))


async def verify_password_async(password: str, stored: str) -> bool:
    return await run_hashing(lambda: verify_password(password, stored))


# ---------------------------------------------------------------------- weryfikacja dwuetapowa

TOTP_PERIOD = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
"""Ile kroków (po 30 s) w przód i w tył akceptujemy: zegar telefonu bywa przesunięty."""


def new_totp_secret() -> str:
    """Sekret TOTP w base32 (160 bitów, RFC 4226 zaleca co najmniej 128)."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def totp_now(secret: str, at: float) -> str:
    return _hotp(secret, int(at) // TOTP_PERIOD)


def totp_match(secret: str, code: str, at: float, *, after_step: int | None = None) -> int | None:
    """Krok czasowy pasującego kodu albo None. Kroki <= `after_step` odrzucamy (powtórka kodu)."""
    code = re.sub(r"\s", "", code)
    if not re.fullmatch(rf"\d{{{TOTP_DIGITS}}}", code):
        return None
    now = int(at) // TOTP_PERIOD
    for step in range(now - TOTP_WINDOW, now + TOTP_WINDOW + 1):
        if after_step is not None and step <= after_step:
            continue
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def totp_uri(secret: str, email: str, issuer: str = "Termin") -> str:
    label = quote(f"{issuer}:{email}", safe=":@")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={TOTP_DIGITS}"


# ---------------------------------------------------------------------- kody odzyskiwania

RECOVERY_CODES = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bez 0/O i 1/I, łatwo przepisać


def new_recovery_code() -> str:
    """16 znaków (80 bitów) w grupach po 4, np. `K7QM-3XRA-PL9D-W2HF`."""
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def normalize_recovery_code(code: str) -> str | None:
    raw = re.sub(r"[\s-]", "", code).upper()
    if len(raw) != 16 or any(ch not in _RECOVERY_ALPHABET for ch in raw):
        return None
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------------- urządzenia


def device_label(user_agent: str | None) -> str:
    """Zgrubny opis urządzenia do listy sesji („Chrome · Android”). Nie zapisujemy pełnego UA."""
    ua = (user_agent or "")[:512]
    if "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Przeglądarka"
    if "Android" in ua:
        system = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        system = "iOS"
    elif "Windows" in ua:
        system = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "macOS"
    elif "Linux" in ua:
        system = "Linux"
    else:
        return browser
    return f"{browser} · {system}"

"""Bezpieczeństwo: hasła (scrypt), tokeny, walidacja e-maila i siły hasła.

Bez zewnętrznych zależności: `hashlib.scrypt` jest w bibliotece standardowej, a parametry
(N=2^15, r=8, p=1) odpowiadają zaleceniom OWASP dla scrypt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

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

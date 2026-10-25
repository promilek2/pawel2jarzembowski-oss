"""Web Push bez zewnętrznych usług pośrednich (ADR-0012).

- szyfrowanie treści: RFC 8291 (`aes128gcm`, RFC 8188), ECDH P-256 + HKDF + AES-GCM,
- uwierzytelnienie serwera: VAPID (RFC 8292), JWT podpisany ES256,
- klucz VAPID z konfiguracji (`TERMIN_VAPID_PRIVATE_KEY`) albo wygenerowany i zapisany w bazie.

Wysyłamy wyłącznie do znanych usług push przeglądarek (lista `PUSH_HOSTS`): adres urządzenia
podaje użytkownik, więc bez tej listy serwer dałby się użyć do zapytań w dowolne miejsce (SSRF).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from termin import USER_AGENT
from termin.config import Settings
from termin.logs import get_logger
from termin.notify.base import Notification, NotifyError
from termin.store.push import PushStore, PushSubscription

log = get_logger(__name__)

PUSH_HOSTS = (
    "fcm.googleapis.com",  # Chrome, Edge (część), Samsung, Brave, Opera
    "android.googleapis.com",
    ".push.services.mozilla.com",  # Firefox
    ".notify.windows.com",  # Edge na Windows
    ".push.apple.com",  # Safari (macOS, iOS 16.4+ po instalacji na ekranie początkowym)
)
RECORD_SIZE = 4096
TTL_SECONDS = 24 * 3600
JWT_LIFETIME_S = 12 * 3600


class InvalidSubscriptionKeys(ValueError):
    """Klucze urządzenia są uszkodzone: takiej subskrypcji nie da się użyć."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_endpoint(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.port not in (None, 443):
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


# ---------------------------------------------------------------------- klucze VAPID


@dataclass(frozen=True)
class VapidKeys:
    private_key: ec.EllipticCurvePrivateKey
    public_key: str
    """Klucz publiczny (punkt nieskompresowany, base64url) dla `pushManager.subscribe`."""
    subject: str

    @classmethod
    def from_private(cls, raw: str, subject: str) -> VapidKeys:
        raw = raw.strip()
        if raw.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(raw.encode(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise ValueError("klucz VAPID musi być kluczem EC P-256")
        else:
            key = ec.derive_private_key(int.from_bytes(b64url_decode(raw), "big"), ec.SECP256R1())
        return cls(key, b64url(_public_bytes(key.public_key())), subject)

    def private_raw(self) -> str:
        return b64url(self.private_key.private_numbers().private_value.to_bytes(32, "big"))


def vapid_subject(settings: Settings) -> str:
    if settings.vapid_subject:
        return settings.vapid_subject
    email = settings.security_contact or settings.operator_email
    if email:
        return f"mailto:{email}"
    return (
        settings.public_url
        if settings.public_url.startswith("https://")
        else "mailto:termin@localhost"
    )


def load_vapid(conn: sqlite3.Connection, settings: Settings) -> VapidKeys:
    """Klucz z konfiguracji albo z bazy; przy pierwszym uruchomieniu generujemy nowy."""
    subject = vapid_subject(settings)
    if settings.vapid_private_key:
        return VapidKeys.from_private(settings.vapid_private_key, subject)
    row = conn.execute("SELECT value FROM app_secrets WHERE name = 'vapid_private'").fetchone()
    if row is not None:
        return VapidKeys.from_private(row[0], subject)
    keys = VapidKeys.from_private(
        b64url(
            ec.generate_private_key(ec.SECP256R1())
            .private_numbers()
            .private_value.to_bytes(32, "big")
        ),
        subject,
    )
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO app_secrets (name, value, created_at) VALUES (?, ?, ?)",
            ("vapid_private", keys.private_raw(), dt.datetime.now(dt.UTC).isoformat()),
        )
    # przy wyścigu dwóch procesów wygrywa pierwszy zapis
    stored = conn.execute("SELECT value FROM app_secrets WHERE name = 'vapid_private'").fetchone()
    return VapidKeys.from_private(stored[0], subject)


def vapid_authorization(endpoint: str, keys: VapidKeys, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now if now is not None else time.time()) + JWT_LIFETIME_S),
        "sub": keys.subject,
    }
    payload = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    r, s = decode_dss_signature(keys.private_key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{payload}.{signature}, k={keys.public_key}"


# ---------------------------------------------------------------------- szyfrowanie (RFC 8291)


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    server_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Treść zaszyfrowana dla urządzenia (nagłówek RFC 8188 + jeden rekord)."""
    try:
        ua_public = b64url_decode(p256dh)
        auth_secret = b64url_decode(auth)
    except ValueError as exc:
        raise InvalidSubscriptionKeys("nieprawidłowe klucze subskrypcji push") from exc
    if len(ua_public) != 65 or len(auth_secret) != 16:
        raise InvalidSubscriptionKeys("nieprawidłowe klucze subskrypcji push")
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("treść powiadomienia jest za długa")
    try:
        ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    except ValueError as exc:  # punkt spoza krzywej
        raise InvalidSubscriptionKeys("nieprawidłowe klucze subskrypcji push") from exc
    as_private = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_private.public_key())
    shared = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 = ostatni rekord
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


# ---------------------------------------------------------------------- wysyłka


def push_payload(notification: Notification, settings: Settings) -> bytes:
    """JSON dla service workera; treść przycięta, żeby zmieściła się w jednym rekordzie."""
    body = notification.body.split("\n\nPrzestań obserwować")[0]
    data = {
        "title": notification.title[:120],
        "body": body[:600],
        "url": notification.click_url or settings.public_url,
        "tag": notification.tags[0] if notification.tags else "termin",
    }
    return json.dumps(data, ensure_ascii=False).encode()


class PushNotifier:
    """Kanał „push”: cel to id użytkownika; wysyłamy na wszystkie jego urządzenia."""

    channel = "push"

    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.conn = conn
        self.settings = settings
        self.store = PushStore(conn)
        self._transport = transport
        self._keys: VapidKeys | None = None

    @property
    def keys(self) -> VapidKeys:
        if self._keys is None:
            self._keys = load_vapid(self.conn, self.settings)
        return self._keys

    async def send_to(
        self, sub: PushSubscription, payload: bytes, client: httpx.AsyncClient
    ) -> bool:
        if not allowed_endpoint(sub.endpoint):
            self.store.drop(sub.id)
            return False
        try:
            body = encrypt(payload, sub.p256dh, sub.auth)
        except InvalidSubscriptionKeys:
            self.store.drop(sub.id)
            return False
        except ValueError as exc:  # za długa treść: błąd po naszej stronie, urządzenie zostaje
            log.warning("push.payload_rejected", reason=str(exc))
            return False
        headers = {
            "Authorization": vapid_authorization(sub.endpoint, self.keys),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SECONDS),
            "Urgency": "normal",
            "User-Agent": USER_AGENT,
        }
        try:
            response = await client.post(sub.endpoint, content=body, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("push.failed", reason=type(exc).__name__)
            return False
        if response.status_code in (404, 410):
            self.store.drop(sub.id)  # urządzenie wypisało się albo subskrypcja wygasła
            return False
        if response.status_code >= 400:
            log.warning("push.rejected", status=response.status_code)
            return False
        self.store.mark_ok(sub.id)
        return True

    async def send(self, target: str, notification: Notification) -> None:
        try:
            user_id = int(target)
        except ValueError as exc:
            raise NotifyError("push: nieprawidłowy cel") from exc
        subs = self.store.for_user(user_id)
        if not subs:
            raise NotifyError("push: brak urządzeń z włączonymi powiadomieniami")
        payload = push_payload(notification, self.settings)
        async with httpx.AsyncClient(
            transport=self._transport, timeout=10, follow_redirects=False
        ) as client:
            results = [await self.send_to(sub, payload, client) for sub in subs]
        if not any(results):
            raise NotifyError("push: żadne urządzenie nie przyjęło powiadomienia")

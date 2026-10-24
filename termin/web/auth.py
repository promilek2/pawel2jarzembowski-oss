"""Konta w panelu: rejestracja, logowanie, sesje, reset hasła, eksport i usunięcie danych.

Sesja: losowy token w ciasteczku HttpOnly + SameSite=Lax (+ Secure i prefiks __Host- przy
HTTPS). Ochrona CSRF: sprawdzanie nagłówka Origin dla wszystkich zapytań zmieniających stan
oraz token CSRF w nagłówku `X-CSRF-Token` dla zapytań z sesją (ADR-0010).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from slowapi import Limiter

from termin.config import Settings
from termin.logs import get_logger
from termin.notify.base import Notification, NotifyError
from termin.notify.channels import EmailNotifier
from termin.runtime import Runtime
from termin.security import normalize_email, password_problem
from termin.store.accounts import AccountError, AccountStore, LockedError, User
from termin.store.subscriptions import SubscriptionStore

log = get_logger(__name__)

CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT = frozenset({"/api/auth/login", "/api/auth/register"})

Mailer = Callable[[str, str, str], Awaitable[None]]
"""(adresat, temat, treść) → wysłanie e-maila."""


def session_cookie_name(settings: Settings) -> str:
    # __Host-: przeglądarka przyjmie ciasteczko tylko z Secure, Path=/ i bez Domain
    return "__Host-termin_session" if settings.secure_cookies else "termin_session"


def default_mailer(settings: Settings) -> Mailer | None:
    if not settings.mail_enabled:
        return None
    notifier = EmailNotifier(settings)

    async def send(to: str, subject: str, body: str) -> None:
        await notifier.send(to, Notification(title=subject, body=body))

    return send


# ---------------------------------------------------------------------- modele wejścia


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    accept_terms: bool = False


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ResetIn(TokenIn):
    password: str = Field(max_length=128)


class PasswordIn(BaseModel):
    password: str = Field(max_length=128)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=128)
    new: str = Field(max_length=128)


# ---------------------------------------------------------------------- pomocnicze


def accounts(request: Request) -> AccountStore:
    runtime: Runtime = request.app.state.runtime
    s = runtime.settings
    return AccountStore(
        runtime.conn,
        max_failed_logins=s.max_failed_logins,
        lockout=dt.timedelta(minutes=s.lockout_minutes),
        session_ttl=dt.timedelta(days=s.session_days),
    )


def current_session(request: Request) -> tuple[User, str] | None:
    """(użytkownik, token CSRF) z ciasteczka sesji; wynik zapamiętany w request.state."""
    if hasattr(request.state, "auth"):
        cached: tuple[User, str] | None = request.state.auth
        return cached
    runtime: Runtime = request.app.state.runtime
    token = request.cookies.get(session_cookie_name(runtime.settings))
    result = accounts(request).session_user(token) if token else None
    request.state.auth = result
    return result


def current_user(request: Request) -> User | None:
    session = current_session(request)
    return session[0] if session else None


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Zaloguj się, żeby to zrobić.")
    return user


def allowed_origins(settings: Settings, request: Request) -> set[str]:
    public = urlsplit(settings.public_url)
    own = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    return {f"{public.scheme}://{public.netloc}", own}


async def csrf_guard(request: Request) -> JSONResponse | None:
    """Odrzuca zapytania zmieniające stan z obcych stron. None = przepuść."""
    if request.method not in UNSAFE_METHODS:
        return None
    path = request.url.path
    if not (path.startswith("/api/") or path.startswith("/stop/")):
        return None
    runtime: Runtime = request.app.state.runtime
    origin = request.headers.get("origin")
    if origin and origin != "null" and origin not in allowed_origins(runtime.settings, request):
        return JSONResponse({"detail": "Niedozwolone źródło zapytania.", "code": "csrf"}, 403)
    if path.startswith("/api/") and path not in CSRF_EXEMPT:
        session = current_session(request)
        if session is not None and request.headers.get(CSRF_HEADER) != session[1]:
            return JSONResponse(
                {"detail": "Sesja wygasła. Odśwież stronę i spróbuj ponownie.", "code": "csrf"},
                403,
            )
    return None


def _set_session(response: Response, settings: Settings, token: str, expires: dt.datetime) -> None:
    response.set_cookie(
        session_cookie_name(settings),
        token,
        max_age=int((expires - dt.datetime.now(dt.UTC)).total_seconds()),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def _clear_session(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        session_cookie_name(settings),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _me(user: User | None, csrf: str | None, settings: Settings) -> dict[str, Any]:
    return {
        "user": user.public_dict() if user else None,
        "csrf": csrf,
        "accounts_enabled": settings.accounts_enabled,
        "mail_enabled": settings.mail_enabled,
    }


# ---------------------------------------------------------------------- router


def build_auth_router(settings: Settings, limiter: Limiter) -> APIRouter:
    router = APIRouter(prefix="/api")
    login_limit = limiter.limit(settings.login_rate_limit)
    heavy = limiter.limit(settings.rate_limit)

    def enabled() -> None:
        if not settings.accounts_enabled:
            raise HTTPException(404, "Konta są wyłączone w tej instancji.")

    async def send_mail(request: Request, to: str, subject: str, body: str) -> bool:
        mailer: Mailer | None = request.app.state.mailer
        if mailer is None:
            return False
        try:
            await mailer(to, subject, body)
        except NotifyError as exc:
            log.warning("auth.mail_failed", reason=str(exc))
            return False
        return True

    async def send_verification(request: Request, user: User) -> bool:
        token = accounts(request).create_email_token(user.id, "verify")
        link = f"{settings.public_url}/?konto=weryfikacja&token={token}"
        return await send_mail(
            request,
            user.email,
            "Termin: potwierdź adres e-mail",
            "Dzień dobry,\n\npotwierdź adres e-mail konta w Termin, otwierając link (ważny 3 dni):"
            f"\n{link}\n\nJeśli to nie Ty zakładałeś/zakładałaś konto, zignoruj tę wiadomość. "
            "Niepotwierdzone konto usuniemy automatycznie.\n",
        )

    def login_response(request: Request, user: User, status: int = 200) -> JSONResponse:
        session = accounts(request).create_session(user.id)
        response = JSONResponse(_me(user, session.csrf_token, settings), status_code=status)
        _set_session(response, settings, session.token, session.expires_at)
        return response

    @router.get("/auth/me")
    async def me(request: Request) -> dict[str, Any]:
        session = current_session(request)
        return _me(session[0] if session else None, session[1] if session else None, settings)

    @router.post("/auth/register")
    @login_limit
    async def register(request: Request, payload: RegisterIn) -> JSONResponse:
        enabled()
        if not payload.accept_terms:
            raise HTTPException(400, "Aby założyć konto, zaakceptuj regulamin.")
        email = normalize_email(payload.email)
        if email is None:
            raise HTTPException(400, "Podaj poprawny adres e-mail.")
        problem = password_problem(payload.password, email)
        if problem:
            raise HTTPException(400, problem)
        try:
            user = accounts(request).create(email, payload.password)
        except AccountError as exc:
            raise HTTPException(409, str(exc)) from exc
        if settings.mail_enabled:
            await send_verification(request, user)
        log.info("auth.registered")
        return login_response(request, user, status=201)

    @router.post("/auth/login")
    @login_limit
    async def login(request: Request, payload: LoginIn) -> JSONResponse:
        enabled()
        email = normalize_email(payload.email) or payload.email.strip().lower()
        try:
            user = accounts(request).authenticate(email, payload.password)
        except LockedError as exc:
            raise HTTPException(429, str(exc)) from exc
        except AccountError as exc:
            raise HTTPException(401, str(exc)) from exc
        # stara sesja z tego urządzenia (jeśli była) przestaje działać
        old = request.cookies.get(session_cookie_name(settings))
        if old:
            accounts(request).delete_session(old)
        return login_response(request, user)

    @router.post("/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        token = request.cookies.get(session_cookie_name(settings))
        if token:
            accounts(request).delete_session(token)
        response = JSONResponse(_me(None, None, settings))
        _clear_session(response, settings)
        return response

    @router.post("/auth/verify")
    @heavy
    async def verify(request: Request, payload: TokenIn) -> dict[str, Any]:
        enabled()
        store = accounts(request)
        try:
            user_id = store.use_email_token(payload.token, "verify")
        except AccountError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.mark_verified(user_id)
        return {"verified": True}

    @router.post("/auth/resend-verification", status_code=202)
    @login_limit
    async def resend(request: Request) -> dict[str, Any]:
        user = require_user(request)
        if user.verified:
            return {"sent": False, "verified": True}
        return {"sent": await send_verification(request, user)}

    @router.post("/auth/request-reset", status_code=202)
    @login_limit
    async def request_reset(request: Request, payload: EmailIn) -> dict[str, Any]:
        enabled()
        if not settings.mail_enabled:
            raise HTTPException(
                501, "Reset hasła wymaga poczty e-mail, której ta instancja nie ma skonfigurowanej."
            )
        email = normalize_email(payload.email)
        user = accounts(request).by_email(email) if email else None
        if user is not None:
            token = accounts(request).create_email_token(user.id, "reset")
            link = f"{settings.public_url}/?konto=reset&token={token}"
            await send_mail(
                request,
                user.email,
                "Termin: ustaw nowe hasło",
                "Dzień dobry,\n\naby ustawić nowe hasło, otwórz link (ważny 1 godzinę):\n"
                f"{link}\n\n"
                "Jeśli to nie Ty prosiłeś/prosiłaś o zmianę hasła, zignoruj tę wiadomość.\n",
            )
        # zawsze ta sama odpowiedź: nie zdradzamy, czy konto istnieje
        return {"detail": "Jeśli konto istnieje, wysłaliśmy link do ustawienia nowego hasła."}

    @router.post("/auth/reset")
    @login_limit
    async def reset(request: Request, payload: ResetIn) -> JSONResponse:
        enabled()
        store = accounts(request)
        try:
            # najpierw sprawdzamy hasło, żeby słabe hasło nie „zużyło” linku z e-maila
            user_id = store.use_email_token(payload.token, "reset", consume=False)
        except AccountError as exc:
            raise HTTPException(400, str(exc)) from exc
        user = store.get(user_id)
        assert user is not None
        problem = password_problem(payload.password, user.email)
        if problem:
            raise HTTPException(400, problem)
        store.use_email_token(payload.token, "reset")
        store.set_password(user_id, payload.password)
        store.mark_verified(user_id)  # link z e-maila potwierdza adres
        return login_response(request, user)

    @router.post("/account/password")
    @login_limit
    async def change_password(request: Request, payload: PasswordChangeIn) -> JSONResponse:
        user = require_user(request)
        store = accounts(request)
        if not store.check_password(user.id, payload.current):
            raise HTTPException(400, "Obecne hasło jest nieprawidłowe.")
        problem = password_problem(payload.new, user.email)
        if problem:
            raise HTTPException(400, problem)
        store.set_password(user.id, payload.new)  # wylogowuje wszystkie urządzenia
        return login_response(request, user)

    @router.get("/account/export")
    async def export(request: Request) -> Response:
        user = require_user(request)
        data = accounts(request).export(user.id)
        return Response(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="termin-moje-dane.json"'},
        )

    @router.post("/account/delete")
    @login_limit
    async def delete_account(request: Request, payload: PasswordIn) -> JSONResponse:
        user = require_user(request)
        store = accounts(request)
        if not store.check_password(user.id, payload.password):
            raise HTTPException(400, "Hasło jest nieprawidłowe.")
        store.delete(user.id)
        log.info("auth.account_deleted")
        response = JSONResponse({"deleted": True})
        _clear_session(response, settings)
        return response

    @router.post("/account/logout-all")
    async def logout_all(request: Request) -> JSONResponse:
        user = require_user(request)
        accounts(request).delete_sessions(user.id)
        response = JSONResponse(_me(None, None, settings))
        _clear_session(response, settings)
        return response

    @router.get("/account/subscriptions")
    async def my_subscriptions(request: Request) -> list[dict[str, Any]]:
        user = require_user(request)
        runtime: Runtime = request.app.state.runtime
        return [s.public_dict() for s in SubscriptionStore(runtime.conn).for_user(user.id)]

    @router.delete("/account/subscriptions/{token}", status_code=204)
    async def delete_my_subscription(request: Request, token: str) -> Response:
        user = require_user(request)
        runtime: Runtime = request.app.state.runtime
        store = SubscriptionStore(runtime.conn)
        sub = store.get(token)
        if sub is None or sub.user_id != user.id:
            raise HTTPException(404, "Nie ma takiej obserwacji.")
        store.delete(token)
        return Response(status_code=204)

    return router

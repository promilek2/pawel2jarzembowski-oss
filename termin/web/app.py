"""Panel web: API JSON + statyczny frontend (PWA)."""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from importlib import resources
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.trustedhost import TrustedHostMiddleware

from termin import REPO_URL, __version__
from termin.config import Settings, get_settings
from termin.core.benefits import Variant
from termin.core.client import NfzError
from termin.core.geo import GeocodingError
from termin.core.models import Case, SortMode
from termin.core.provinces import PROVINCES
from termin.core.search import SearchError, SearchRequest
from termin.core.serialize import response_to_dict
from termin.logs import configure_logging, get_logger
from termin.metrics import HTTP_LATENCY, HTTP_REQUESTS, RATE_LIMITED, SEARCHES
from termin.notify.channels import build_notifiers
from termin.notify.message import unsubscribe_url
from termin.notify.subscribe import SubscribeError, SubscribeRequest, create_subscription
from termin.notify.watcher import Watcher
from termin.notify.webpush import PushNotifier
from termin.runtime import Runtime, Transports
from termin.security import BusyError
from termin.store.push import PushStore
from termin.store.snapshots import to_csv, to_json
from termin.store.subscriptions import SubscriptionStore
from termin.web.auth import Mailer, build_auth_router, csrf_guard, current_user, default_mailer
from termin.web.ics import call_reminder
from termin.web.legal import LEGAL_PAGES, missing_operator_fields, render_legal, security_txt
from termin.web.personal import build_personal_router
from termin.web.protection import BodyLimitMiddleware, IpRateLimiter, MailThrottle, client_ip

log = get_logger(__name__)

STATIC_DIR = resources.files("termin.web").joinpath("static")
NFZ_OFFICIAL = "https://terminyleczenia.nfz.gov.pl"
# Znak Termin (jak icon.svg): bilet z wcięciami, „T” i pomarańczowa kropka.
BRAND_MARK = (
    '<svg class="brand-mark" width="36" height="36" viewBox="0 0 64 64" aria-hidden="true">'
    '<path class="logo-tile" d="M12 10H52A8 8 0 0 1 60 18V26A6 6 0 0 0 60 38V46A8 8 0 0 1 52 54'
    'H12A8 8 0 0 1 4 46V38A6 6 0 0 0 4 26V18A8 8 0 0 1 12 10Z"/>'
    '<path class="logo-t" d="M20 20H44V26H35V44H29V26H20Z"/>'
    '<circle class="logo-dot" cx="45" cy="42" r="4.5"/></svg>'
)


class SubscriptionIn(BaseModel):
    q: str = Field(default="", max_length=200)
    benefit: str | None = Field(default=None, max_length=200)
    variant: Variant | None = None
    where: str | None = Field(default=None, max_length=200)
    lat: float | None = Field(default=None, ge=48.9, le=55.0)
    lon: float | None = Field(default=None, ge=14.0, le=24.3)
    radius: float | None = Field(default=None, gt=0, le=300)
    province: str | None = Field(default=None, pattern=r"^\d{2}$")
    before: dt.date | None = None
    case: Literal[1, 2] = 1
    channel: Literal["ntfy", "telegram", "email", "push"]
    target: str = Field(min_length=1, max_length=200)
    consent: bool = False
    """Wyraźna zgoda na przetwarzanie informacji o świadczeniu (art. 9 ust. 2 lit. a RODO)."""


def create_app(
    settings: Settings | None = None,
    *,
    transports: Transports | None = None,
    today: Callable[[], dt.date] = dt.date.today,
    start_watcher: bool = False,
    mailer: Mailer | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json=settings.log_json)
        runtime = Runtime(settings, transports=transports, today=today)
        app.state.runtime = runtime
        app.state.search_slots = asyncio.Semaphore(settings.max_concurrent_searches)
        app.state.push = (
            PushNotifier(runtime.conn, settings, runtime.transports.notify)
            if settings.push_enabled
            else None
        )
        missing = missing_operator_fields(settings)
        if missing:
            log.warning("legal.operator_missing", fields=",".join(missing))
        task: asyncio.Task[None] | None = None
        stop = asyncio.Event()
        if start_watcher:
            notifiers = build_notifiers(settings, runtime.transports.notify, runtime.conn)
            task = asyncio.create_task(Watcher(runtime, notifiers).run_forever(stop))
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await runtime.aclose()

    app = FastAPI(
        title="Termin",
        version=__version__,
        description="Najszybsze terminy u specjalistów na NFZ (dane: API NFZ „Terminy leczenia”).",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    # Limit zapytań: chroni API NFZ przed użyciem nas jako darmowego scrapera. Adres IP
    # jest trzymany tylko w pamięci procesu (PRIVACY.md).
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
    app.state.limiter = limiter
    heavy = limiter.limit(settings.rate_limit)
    light = limiter.limit(settings.rate_limit_light)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        RATE_LIMITED.labels(_route(request)).inc()
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Za dużo zapytań. Odczekaj chwilę i spróbuj ponownie.",
                "code": "rate",
            },
            headers={"Retry-After": "60"},
        )

    app.state.mailer = mailer if mailer is not None else default_mailer(settings)
    app.state.mail_throttle = MailThrottle(settings.mails_per_address_per_hour)
    ip_limiter = IpRateLimiter(settings.global_requests_per_minute, settings.max_tracked_ips)
    subscribe_limit = limiter.limit(settings.subscribe_rate_limit)

    @asynccontextmanager
    async def search_slot(request: Request) -> AsyncIterator[None]:
        """Ogranicza liczbę równoczesnych wyszukiwań (każde to zapytania do NFZ)."""
        slots: asyncio.Semaphore = request.app.state.search_slots
        try:
            await asyncio.wait_for(slots.acquire(), timeout=settings.search_queue_timeout_s)
        except TimeoutError as exc:
            raise HTTPException(
                503, "Serwer jest teraz mocno obciążony. Spróbuj za chwilę.",
                headers={"Retry-After": "10"},
            ) from exc  # fmt: skip
        try:
            yield
        finally:
            slots.release()

    async def with_timeout(coro: Any) -> Any:
        try:
            return await asyncio.wait_for(coro, timeout=settings.search_timeout_s)
        except TimeoutError as exc:
            raise HTTPException(504, "Wyszukiwanie trwało za długo. Spróbuj ponownie.") from exc

    app.include_router(build_auth_router(settings, limiter))
    app.include_router(build_personal_router(settings, limiter))

    def rt(request: Request) -> Runtime:
        runtime: Runtime = request.app.state.runtime
        return runtime

    @app.exception_handler(NfzError)
    async def _nfz_error(_request: Request, exc: NfzError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc), "code": "nfz"})

    @app.exception_handler(SearchError)
    async def _search_error(_request: Request, exc: SearchError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "code": "search"})

    @app.exception_handler(BusyError)
    async def _busy(_request: Request, exc: BusyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "code": "busy"},
            headers={"Retry-After": "10"},
        )

    @app.exception_handler(GeocodingError)
    async def _geo_error(_request: Request, exc: GeocodingError) -> JSONResponse:
        detail = "Usługa wyszukiwania adresów jest chwilowo niedostępna. Podaj miasto."
        return JSONResponse(status_code=503, content={"detail": detail, "code": "geocoding"})

    # ------------------------------------------------------------------ API

    @app.get("/api/config")
    async def config(request: Request) -> dict[str, Any]:
        runtime = rt(request)
        channels = ["ntfy"]
        if settings.telegram_token:
            channels.append("telegram")
        if settings.smtp_host and settings.smtp_from:
            channels.append("email")
        return {
            "version": __version__,
            "tile_url": settings.tile_url,
            "tile_attribution": settings.tile_attribution,
            "default_radius_km": settings.default_radius_km,
            "km_per_day": settings.km_per_day,
            "travel_time": runtime.router is not None,
            "channels": channels,
            "ntfy_server": settings.ntfy_server,
            "official_url": NFZ_OFFICIAL,
            "repo_url": REPO_URL,
            "provinces": {code: p.name for code, p in PROVINCES.items()},
            "accounts_enabled": settings.accounts_enabled,
            "mail_enabled": settings.mail_enabled,
            "legal_pages": LEGAL_PAGES,
        }

    @app.get("/api/suggest")
    @light
    async def suggest(
        request: Request, q: Annotated[str, Query(max_length=100)] = ""
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": c.entry.id,
                "label": c.entry.label,
                "query": c.entry.aliases[0],
                "kind": c.entry.kind,
                "has_child_variant": c.entry.has_child_variant,
            }
            for c in rt(request).catalog.suggest(q, limit=8)
        ]

    @app.get("/api/search")
    @heavy
    async def search(
        request: Request,
        q: Annotated[str, Query(max_length=200)] = "",
        where: Annotated[str | None, Query(max_length=200)] = None,
        lat: Annotated[float | None, Query(ge=48.9, le=55.0)] = None,
        lon: Annotated[float | None, Query(ge=14.0, le=24.3)] = None,
        radius: Annotated[float | None, Query(gt=0, le=300)] = None,
        province: Annotated[str | None, Query(pattern=r"^\d{2}$")] = None,
        mode: SortMode = SortMode.BALANCED,
        case: Annotated[int, Query(ge=1, le=2)] = 1,
        variant: Variant | None = None,
        km_per_day: Annotated[float | None, Query(gt=0, le=100)] = None,
        benefit: Annotated[str | None, Query(max_length=200)] = None,
        travel: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> dict[str, Any]:
        runtime = rt(request)
        if not q.strip() and not benefit:
            raise HTTPException(400, "Wpisz, czego szukasz.")
        point = None
        if lat is not None and lon is not None:
            point = runtime.geocoder.point(lat, lon)
        req = SearchRequest(
            text=q,
            where=where if point is None else None,
            point=point,
            radius_km=radius,
            province=province,
            case=Case(case),
            variant=variant,
            mode=mode,
            km_per_day=km_per_day,
            limit=limit,
            with_travel_time=travel,
            benefit_query=benefit,
        )
        SEARCHES.labels("web").inc()
        async with search_slot(request):
            response = await with_timeout(runtime.search.search(req))
        return response_to_dict(response)

    @app.get("/api/history/{queue_id}")
    @light
    async def history(
        request: Request, queue_id: str, days: Annotated[int, Query(ge=7, le=730)] = 180
    ) -> dict[str, Any]:
        return rt(request).snapshots.trend(queue_id, days).to_dict()

    @app.get("/api/stats")
    @heavy
    async def stats(
        request: Request,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        province: Annotated[str, Query(pattern=r"^\d{2}$")],
        case: Annotated[int, Query(ge=1, le=2)] = 1,
        variant: Variant | None = None,
    ) -> dict[str, Any]:
        runtime = rt(request)
        if province not in PROVINCES:
            raise HTTPException(400, "Nieznane województwo.")
        resolution = runtime.catalog.resolve(q, Variant.ANY)
        query = resolution.query
        latest = runtime.snapshots.latest_snapshot_id(query, province, case)
        if latest is None or latest[1] < runtime.today():
            async with search_slot(request):
                await with_timeout(runtime.take_snapshot(query, province, case, source="search"))
        children = {Variant.CHILD: True, Variant.ADULT: False}.get(variant) if variant else None
        result = runtime.snapshots.wait_stats(query, province, case, for_children=children)
        data = result.to_dict()
        data["label"] = resolution.label if resolution.entry else query
        data["province_name"] = PROVINCES[province].name
        return data

    @app.get("/api/export.{fmt}")
    @heavy
    async def export(
        request: Request,
        fmt: Literal["csv", "json"],
        q: Annotated[str | None, Query(max_length=200)] = None,
        province: Annotated[str | None, Query(pattern=r"^\d{2}$")] = None,
        since: dt.date | None = None,
        until: dt.date | None = None,
    ) -> Response:
        runtime = rt(request)
        query = runtime.catalog.resolve(q, Variant.ANY).query if q else None
        rows = runtime.snapshots.export_rows(
            query=query, province=province, since=since, until=until
        )
        if fmt == "csv":
            return Response(
                to_csv(rows),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="termin-snapshoty.csv"'},
            )
        return Response(to_json(rows), media_type="application/json")

    @app.get("/api/ics/{queue_id}")
    @light
    async def ics(request: Request, queue_id: str, day: dt.date | None = None) -> Response:
        runtime = rt(request)
        entry = await runtime.client.queue(queue_id)
        if entry is None:
            raise HTTPException(404, "Nie znaleziono placówki.")
        when = day or (runtime.today() + dt.timedelta(days=1))
        body = call_reminder(entry, when, url=f"{settings.public_url}/#wynik={queue_id}")
        return Response(
            body,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="zadzwon.ics"'},
        )

    @app.post("/api/subscriptions", status_code=201)
    @subscribe_limit
    async def subscribe(request: Request, payload: SubscriptionIn) -> dict[str, Any]:
        runtime = rt(request)
        point = None
        if payload.lat is not None and payload.lon is not None:
            point = runtime.geocoder.point(payload.lat, payload.lon)
        if payload.channel == "telegram" and not settings.telegram_token:
            raise HTTPException(400, "Powiadomienia Telegram nie są włączone w tej instancji.")
        if payload.channel == "email" and not (settings.smtp_host and settings.smtp_from):
            raise HTTPException(400, "Powiadomienia e-mail nie są włączone w tej instancji.")
        user = current_user(request)
        target = payload.target
        if payload.channel == "push":
            if user is None:
                raise HTTPException(401, "Zaloguj się, żeby dostawać powiadomienia w aplikacji.")
            if not settings.push_enabled or PushStore(runtime.conn).count(user.id) == 0:
                raise HTTPException(
                    400, "Najpierw włącz powiadomienia na tym urządzeniu (Konto → Powiadomienia)."
                )
            target = str(user.id)
        try:
            sub = await create_subscription(
                runtime,
                SubscribeRequest(
                    text=payload.q,
                    benefit_query=payload.benefit,
                    variant=payload.variant,
                    where=payload.where if point is None else None,
                    point=point,
                    province=payload.province,
                    radius_km=payload.radius,
                    before_date=payload.before,
                    case=Case(payload.case),
                    channel=payload.channel,
                    target=target,
                    consent=payload.consent,
                    user_id=user.id if user else None,
                ),
            )
        except SubscribeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**sub.public_dict(), "unsubscribe_url": unsubscribe_url(settings, sub.token)}

    @app.get("/api/subscriptions/{token}")
    @light
    async def get_subscription(request: Request, token: str) -> dict[str, Any]:
        sub = SubscriptionStore(rt(request).conn).get(token)
        if sub is None:
            raise HTTPException(404, "Nie ma takiej obserwacji.")
        return sub.public_dict()

    @app.delete("/api/subscriptions/{token}", status_code=204)
    @light
    async def delete_subscription(request: Request, token: str) -> Response:
        if not SubscriptionStore(rt(request).conn).delete(token):
            raise HTTPException(404, "Nie ma takiej obserwacji.")
        return Response(status_code=204)

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        try:
            rt(request).conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # baza niedostępna = instancja niezdrowa
            log.error("healthz.db_error", reason=type(exc).__name__)
            return JSONResponse({"status": "error", "db": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ------------------------------------------------------------------ strony

    @app.get("/stop/{token}", response_class=HTMLResponse)
    async def stop_page(request: Request, token: str) -> HTMLResponse:
        # GET niczego nie usuwa (podglądy linków w komunikatorach), tylko pokazuje formularz.
        sub = SubscriptionStore(rt(request).conn).get(token)
        if sub is None:
            return HTMLResponse(
                _page("Nie ma takiej obserwacji", "<p>Mogła już zostać usunięta.</p>"), 404
            )
        body = (
            f"<p>Obserwujesz: <strong>{html.escape(sub.label)}</strong> "
            f"({html.escape(sub.area_label or '')}).</p>"
            f'<form method="post"><button type="submit" class="btn btn-signal btn-big">'
            "Przestań obserwować i usuń dane</button></form>"
        )
        return HTMLResponse(_page("Przestać obserwować?", body))

    @app.post("/stop/{token}", response_class=HTMLResponse)
    async def stop_confirm(request: Request, token: str) -> HTMLResponse:
        removed = SubscriptionStore(rt(request).conn).delete(token)
        title = "Gotowe" if removed else "Nie ma takiej obserwacji"
        body = (
            "<p>Nie będziemy już wysyłać powiadomień. Dane tej obserwacji zostały usunięte.</p>"
            if removed
            else "<p>Mogła już zostać usunięta.</p>"
        )
        return HTMLResponse(_page(title, body), 200 if removed else 404)

    def legal_route(name: str, title: str) -> None:
        async def page() -> HTMLResponse:
            return HTMLResponse(_page(title, render_legal(name, settings), legal=True))

        app.add_api_route(
            f"/{name}", page, methods=["GET"], response_class=HTMLResponse, include_in_schema=False
        )

    for name, title in LEGAL_PAGES.items():
        legal_route(name, title)

    @app.get("/.well-known/security.txt", include_in_schema=False)
    async def security_txt_route() -> PlainTextResponse:
        expires = (dt.datetime.now(dt.UTC) + dt.timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z")
        return PlainTextResponse(security_txt(settings, expires))

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(STATIC_DIR.joinpath("index.html").read_text("utf-8"))

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> Response:
        # SW musi być serwowany z katalogu głównego, żeby obejmował całą aplikację.
        return Response(
            STATIC_DIR.joinpath("sw.js").read_text("utf-8"),
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> Response:
        return Response(
            STATIC_DIR.joinpath("manifest.webmanifest").read_text("utf-8"),
            media_type="application/manifest+json",
        )

    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nDisallow: /api/\nDisallow: /stop/\n")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    max_body = settings.max_body_kb * 1024

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        response: Response
        if request.url.path != "/healthz" and not ip_limiter.allow(client_ip(request)):
            RATE_LIMITED.labels("global").inc()
            response = JSONResponse(
                {"detail": "Za dużo zapytań. Odczekaj chwilę.", "code": "rate"},
                429,
                headers={"Retry-After": "60"},
            )
        elif (blocked := await csrf_guard(request)) is not None:
            response = blocked
        else:
            response = await call_next(request)
        route = _route(request)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(route).observe(time.perf_counter() - started)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        h.setdefault(
            "Permissions-Policy",
            "geolocation=(self), camera=(), microphone=(), payment=(), usb=(), interest-cohort=()",
        )
        if settings.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        tile_origin = _origin(settings.tile_url)
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: " + tile_origin + "; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; font-src 'self'; "
            "manifest-src 'self'; worker-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response

    # Kolejność: ostatnio dodany działa pierwszy (najbardziej zewnętrzny).
    app.add_middleware(BodyLimitMiddleware, max_bytes=max_body)
    if settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    return app


def _route(request: Request) -> str:
    """Szablon ścieżki (np. /api/history/{queue_id}), żeby metryki nie puchły od ID."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return "/static" if request.url.path.startswith("/static/") else "other"


def _origin(url: str) -> str:
    """'https://{s}.tile.example.org/{z}/…' → 'https://*.tile.example.org' (do CSP)."""
    if not url.startswith(("https://", "http://")):
        return ""
    return "/".join(url.split("/")[:3]).replace("{s}.", "*.")


def _page(title: str, body: str, *, legal: bool = False) -> str:
    links = "".join(
        f'<li><a href="/{name}">{html.escape(label)}</a></li>'
        for name, label in LEGAL_PAGES.items()
    )
    heading = "" if legal else f"<h1>{html.escape(title)}</h1>"
    return f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Termin</title>
<link rel="icon" href="/static/icons/icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css">
<script type="module" src="/static/page.js"></script></head>
<body>
<header class="site-header wrap">
<a class="brand" href="/">{BRAND_MARK}<span class="brand-name">Termin</span></a>
<a class="pill" href="/">Wróć do wyszukiwarki</a></header>
<main id="main" class="wrap page legal">{heading}{body}</main>
<footer class="site-footer"><div class="wrap footer-grid">
<ul class="footer-links">{links}</ul>
<p class="small">Termin nie jest oficjalną aplikacją NFZ.</p></div></footer>
</body></html>"""

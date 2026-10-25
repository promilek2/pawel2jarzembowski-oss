"""API funkcji dla zalogowanych (ADR-0012): zgłoszenia z telefonu, sprawy, notatki, Web Push.

Wszystkie ścieżki wymagają sesji. Zapis informacji o własnym leczeniu wymaga dodatkowo
wyraźnej zgody (art. 9 RODO): bez niej odpowiedź 428 z `code: consent_required`.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from slowapi import Limiter

from termin.config import Settings
from termin.logs import get_logger
from termin.notify.base import Notification, NotifyError
from termin.notify.webpush import PushNotifier, allowed_endpoint, b64url_decode
from termin.runtime import Runtime
from termin.security import device_label
from termin.store.accounts import User
from termin.store.personal import (
    CaseStore,
    NoteStore,
    PersonalDataError,
    PhoneReport,
    PhoneReportStore,
    give_consent,
    has_consent,
    import_personal,
    valid_queue_id,
    withdraw_consent,
)
from termin.store.push import PushStore
from termin.web.auth import require_user
from termin.web.ics import visit_event

log = get_logger(__name__)

CaseStatusIn = Literal["to_call", "waiting", "booked", "done", "rejected"]


class ConsentIn(BaseModel):
    consent: bool


class ReportIn(BaseModel):
    queue_id: str = Field(max_length=64)
    reached: bool
    outcome: Literal["same", "other", "none"] | None = None
    nfz_date: dt.date | None = None
    offered_date: dt.date | None = None


class NoteIn(BaseModel):
    note: str = Field(max_length=2000)


class CaseIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    queue_id: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=300)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=60)
    status: CaseStatusIn = "to_call"
    visit_date: dt.date | None = None
    note: str | None = Field(default=None, max_length=2000)


class CasePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    status: CaseStatusIn | None = None
    visit_date: dt.date | None = None
    note: str | None = Field(default=None, max_length=2000)
    clear_visit_date: bool = False


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=16, max_length=40)


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)
    keys: PushKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)


def _ids(raw: str) -> list[str]:
    return [x for x in raw.split(",") if valid_queue_id(x)][:100]


def build_personal_router(settings: Settings, limiter: Limiter) -> APIRouter:
    router = APIRouter(prefix="/api")
    light = limiter.limit(settings.rate_limit_light)
    writes = limiter.limit(settings.rate_limit)

    def rt(request: Request) -> Runtime:
        runtime: Runtime = request.app.state.runtime
        return runtime

    def consented(request: Request) -> User:
        user = require_user(request)
        if not has_consent(rt(request).conn, user.id):
            raise HTTPException(
                428,
                "Ta funkcja zapisuje informacje o Twoim leczeniu. Potrzebujemy Twojej zgody.",
            )
        return user

    def push_notifier(request: Request) -> PushNotifier:
        notifier: PushNotifier | None = getattr(request.app.state, "push", None)
        if notifier is None or not settings.push_enabled:
            raise HTTPException(404, "Powiadomienia push są wyłączone w tej instancji.")
        return notifier

    # ------------------------------------------------------------------ stan i zgoda

    @router.get("/me/features")
    async def features(request: Request) -> dict[str, Any]:
        user = require_user(request)
        conn = rt(request).conn
        push: dict[str, Any] = {"enabled": settings.push_enabled}
        notifier: PushNotifier | None = getattr(request.app.state, "push", None)
        if settings.push_enabled and notifier is not None:
            push["public_key"] = notifier.keys.public_key
            push["devices"] = PushStore(conn).count(user.id)
        return {"health_consent": has_consent(conn, user.id), "push": push}

    @router.post("/me/health-consent")
    async def consent(request: Request, payload: ConsentIn) -> dict[str, Any]:
        user = require_user(request)
        conn = rt(request).conn
        if payload.consent:
            give_consent(conn, user.id)
            return {"health_consent": True}
        removed = withdraw_consent(conn, user.id)
        log.info("personal.consent_withdrawn")
        return {"health_consent": False, "removed": removed}

    @router.post("/me/import")
    @writes
    async def import_backup(
        request: Request, payload: Annotated[dict[str, Any], Body()]
    ) -> dict[str, int]:
        """Przywraca sprawy i notatki z pliku „Pobierz moje dane” (np. po utracie bazy)."""
        user = consented(request)
        result = import_personal(rt(request).conn, user.id, payload)
        log.info("personal.imported", cases=result["cases"], notes=result["notes"])
        return result

    # ------------------------------------------------------------------ „Sprawdziłem telefonicznie”

    @router.post("/reports", status_code=201)
    @writes
    async def add_report(request: Request, payload: ReportIn) -> dict[str, Any]:
        user = consented(request)
        runtime = rt(request)
        store = PhoneReportStore(runtime.conn)
        try:
            store.add(
                user.id,
                PhoneReport(
                    queue_id=payload.queue_id,
                    reached=payload.reached,
                    outcome=payload.outcome,
                    nfz_date=payload.nfz_date,
                    offered_date=payload.offered_date,
                ),
                runtime.today(),
            )
        except PersonalDataError as exc:
            raise HTTPException(400, str(exc)) from exc
        summary = store.summary([payload.queue_id], runtime.today())
        return {"summary": summary.get(payload.queue_id)}

    @router.get("/reports/summary")
    @light
    async def report_summary(
        request: Request, ids: Annotated[str, Query(max_length=7000)] = ""
    ) -> dict[str, Any]:
        require_user(request)
        runtime = rt(request)
        return PhoneReportStore(runtime.conn).summary(_ids(ids), runtime.today())

    # ------------------------------------------------------------------ notatki

    @router.get("/notes")
    @light
    async def notes(
        request: Request, ids: Annotated[str, Query(max_length=7000)] = ""
    ) -> dict[str, str]:
        user = require_user(request)
        return NoteStore(rt(request).conn).get_many(user.id, _ids(ids))

    @router.put("/notes/{queue_id}")
    @writes
    async def put_note(request: Request, queue_id: str, payload: NoteIn) -> dict[str, Any]:
        user = consented(request)
        try:
            text = NoteStore(rt(request).conn).put(user.id, queue_id, payload.note)
        except PersonalDataError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"queue_id": queue_id, "note": text}

    # ------------------------------------------------------------------ „Moje sprawy”

    @router.get("/cases")
    @light
    async def list_cases(request: Request) -> list[dict[str, Any]]:
        user = require_user(request)
        return CaseStore(rt(request).conn).list_for(user.id)

    @router.post("/cases", status_code=201)
    @writes
    async def create_case(request: Request, payload: CaseIn) -> dict[str, Any]:
        user = consented(request)
        try:
            return CaseStore(rt(request).conn).create(user.id, payload.model_dump())
        except PersonalDataError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.patch("/cases/{case_id}")
    @writes
    async def update_case(request: Request, case_id: int, payload: CasePatch) -> dict[str, Any]:
        user = consented(request)
        data = payload.model_dump(exclude_unset=True, exclude={"clear_visit_date"})
        if payload.clear_visit_date:
            data["visit_date"] = None
        try:
            return CaseStore(rt(request).conn).update(user.id, case_id, data)
        except PersonalDataError as exc:
            raise HTTPException(404 if "Nie ma" in str(exc) else 400, str(exc)) from exc

    @router.delete("/cases/{case_id}", status_code=204)
    @writes
    async def delete_case(request: Request, case_id: int) -> Response:
        user = require_user(request)
        if not CaseStore(rt(request).conn).delete(user.id, case_id):
            raise HTTPException(404, "Nie ma takiej sprawy.")
        return Response(status_code=204)

    @router.get("/cases/{case_id}/ics")
    @light
    async def case_ics(request: Request, case_id: int) -> Response:
        user = require_user(request)
        case = CaseStore(rt(request).conn).get(user.id, case_id)
        if case is None or not case["visit_date"]:
            raise HTTPException(404, "Ta sprawa nie ma daty wizyty.")
        body = visit_event(
            case_id,
            case["label"],
            dt.date.fromisoformat(case["visit_date"]),
            provider=case["provider"],
            address=case["address"],
            phone=case["phone"],
        )
        return Response(
            body,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="wizyta.ics"'},
        )

    # ------------------------------------------------------------------ Web Push

    @router.post("/push/subscribe", status_code=201)
    @writes
    async def push_subscribe(request: Request, payload: PushSubscribeIn) -> dict[str, Any]:
        user = require_user(request)
        push_notifier(request)
        if not allowed_endpoint(payload.endpoint):
            raise HTTPException(400, "Ta przeglądarka używa nieobsługiwanej usługi powiadomień.")
        try:
            if len(b64url_decode(payload.keys.p256dh)) != 65:
                raise ValueError
            if len(b64url_decode(payload.keys.auth)) != 16:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Nieprawidłowe klucze powiadomień.") from exc
        store = PushStore(rt(request).conn)
        store.add(
            user.id,
            payload.endpoint,
            payload.keys.p256dh,
            payload.keys.auth,
            device_label(request.headers.get("user-agent")),
        )
        return {"devices": store.count(user.id)}

    @router.post("/push/unsubscribe")
    async def push_unsubscribe(request: Request, payload: PushUnsubscribeIn) -> dict[str, Any]:
        user = require_user(request)
        store = PushStore(rt(request).conn)
        store.remove(user.id, payload.endpoint)
        return {"devices": store.count(user.id)}

    @router.post("/push/test")
    @limiter.limit("5/minute")
    async def push_test(request: Request) -> dict[str, Any]:
        user = require_user(request)
        notifier = push_notifier(request)
        try:
            await notifier.send(
                str(user.id),
                Notification(
                    title="Termin: powiadomienia działają",
                    body="Tak będą wyglądać informacje o wcześniejszych terminach i wizytach.",
                    click_url=f"{settings.public_url}/?konto=panel",
                ),
            )
        except NotifyError as exc:
            raise HTTPException(
                400, "Nie udało się wysłać powiadomienia na żadne urządzenie."
            ) from exc
        return {"sent": True}

    return router

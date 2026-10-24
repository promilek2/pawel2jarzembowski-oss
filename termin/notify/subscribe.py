"""Tworzenie subskrypcji z zapytania (wspólne dla CLI, web i bota Telegram)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from termin.core.benefits import Resolution, Variant
from termin.core.models import Case, GeoPoint
from termin.core.provinces import PROVINCES
from termin.core.search import SearchError, SearchRequest
from termin.runtime import Runtime
from termin.store.subscriptions import CHANNELS, Subscription, SubscriptionStore, new_token


class SubscribeError(ValueError):
    """Komunikat dla użytkownika (np. niejednoznaczne świadczenie)."""


@dataclass(frozen=True, slots=True)
class SubscribeRequest:
    text: str
    channel: str
    target: str
    where: str | None = None
    point: GeoPoint | None = None
    province: str | None = None
    radius_km: float | None = None
    before_date: dt.date | None = None
    case: Case = Case.STABLE
    variant: Variant | None = None
    benefit_query: str | None = None
    default_variant: Variant = Variant.ADULT
    """Wariant, gdy zapytanie jest dwuznaczne, a nie da się dopytać (np. bot)."""
    user_id: int | None = None
    consent: bool = False
    """Wyraźna zgoda na przetwarzanie informacji o obserwowanym świadczeniu (art. 9 RODO)."""


def parse_date(value: str) -> dt.date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(value, fmt).date()  # noqa: DTZ007 - sama data
        except ValueError:
            continue
    raise SubscribeError(f"Nie rozumiem daty „{value}”. Użyj formatu DD.MM.RRRR.")


async def create_subscription(runtime: Runtime, req: SubscribeRequest) -> Subscription:
    if req.channel not in CHANNELS:
        raise SubscribeError(f"Nieznany kanał „{req.channel}”. Dostępne: {', '.join(CHANNELS)}.")
    if req.channel != "desktop" and not req.target.strip():
        raise SubscribeError("Podaj, dokąd wysyłać powiadomienia (temat ntfy, czat, e-mail).")
    if not req.consent:
        raise SubscribeError(
            "Obserwowanie wymaga zgody na przetwarzanie informacji o tym, jakiego świadczenia "
            "szukasz (to może być informacja o zdrowiu)."
        )
    if req.before_date is not None and req.before_date <= runtime.today():
        raise SubscribeError("Data „przed” musi być w przyszłości.")

    search_req = SearchRequest(
        text=req.text,
        where=req.where,
        point=req.point,
        province=req.province,
        radius_km=req.radius_km,
        variant=req.variant,
        benefit_query=req.benefit_query,
    )
    resolution: Resolution = runtime.search.resolve(search_req)
    if resolution.status == "ambiguous":
        options = "; ".join(c.entry.label for c in resolution.candidates)
        raise SubscribeError(f"Doprecyzuj, czego szukasz. Możliwe: {options}.")
    if resolution.status == "choose_variant":
        resolution = resolution.with_variant(req.default_variant)

    try:
        point = await runtime.search.locate(search_req)
    except SearchError as exc:
        raise SubscribeError(str(exc)) from exc
    if point is None and req.province is None:
        raise SubscribeError("Podaj miejsce (adres, kod pocztowy) albo województwo.")
    if req.province is not None and req.province not in PROVINCES:
        raise SubscribeError(f"Nieznany kod województwa: {req.province}")

    radius = req.radius_km or (runtime.settings.default_radius_km if point else None)
    if point is not None:
        area = f"{radius:g} km od: {point.label or 'wskazanego punktu'}"
    else:
        assert req.province is not None
        area = f"województwo {PROVINCES[req.province].name}"
    sub = Subscription(
        token=new_token(),
        label=resolution.label,
        benefit_query=resolution.query,
        channel=req.channel,
        target=req.target.strip(),
        variant=resolution.variant,
        exclude=resolution.exclude,
        case_no=int(req.case),
        province=req.province if point is None else None,
        lat=point.lat if point else None,
        lon=point.lon if point else None,
        radius_km=radius if point else None,
        area_label=area,
        before_date=req.before_date,
        user_id=req.user_id,
        consent_at=dt.datetime.now(dt.UTC),
    )
    store = SubscriptionStore(runtime.conn)
    limits = runtime.settings
    if store.count() >= limits.max_subscriptions_total:
        raise SubscribeError("Ta instancja osiągnęła limit obserwacji. Spróbuj później.")
    if sub.channel != "desktop" and (
        store.count(channel=sub.channel, target=sub.target) >= limits.max_subscriptions_per_target
    ):
        raise SubscribeError(
            f"Ten adres powiadomień ma już {limits.max_subscriptions_per_target} obserwacji. "
            "Usuń którąś, żeby dodać nową."
        )
    if req.user_id is not None and (
        store.count(user_id=req.user_id) >= limits.max_subscriptions_per_user
    ):
        raise SubscribeError(
            f"Możesz mieć najwyżej {limits.max_subscriptions_per_user} obserwacji."
        )
    return store.add(sub)

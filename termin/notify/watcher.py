"""Obserwator subskrypcji (ADR-0005).

Jeden przebieg (`run_once`):
1. grupuje aktywne subskrypcje po identycznym zapytaniu do NFZ (świadczenie, przypadek,
   województwo), żeby nie dublować żądań,
2. pobiera kolejki (cache klienta dodatkowo chroni NFZ) i zapisuje snapshot,
3. dla każdej subskrypcji wybiera najwcześniejszy termin w jej obszarze i przed jej datą,
4. powiadamia tylko o terminie **wcześniejszym** niż ostatnio zgłoszony i nie częściej niż
   co `notify_cooldown_h`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from termin.core.benefits import Resolution
from termin.core.client import NfzError, QueueQuery
from termin.core.models import GeoPoint, QueueEntry, RankedResult, SortMode
from termin.core.provinces import province_from_teryt, provinces_within
from termin.core.ranking import Candidate, RankingOptions, nearest_location, rank
from termin.logs import get_logger
from termin.metrics import NOTIFICATIONS
from termin.notify.base import Notifier, NotifyError
from termin.notify.message import build_notification
from termin.runtime import Runtime
from termin.store.subscriptions import Subscription, SubscriptionStore

log = get_logger(__name__)

GroupKey = tuple[str, int, str]
"""(fragment świadczenia, przypadek, województwo): jedno zapytanie do NFZ."""


@dataclass(slots=True)
class WatchReport:
    subscriptions: int = 0
    queries: int = 0
    notified: int = 0
    not_better: int = 0
    cooldown: int = 0
    no_match: int = 0
    errors: list[str] = field(default_factory=list)


def subscription_provinces(sub: Subscription, runtime: Runtime) -> list[str]:
    if sub.province:
        return [sub.province]
    assert sub.lat is not None and sub.lon is not None
    radius = sub.radius_km or runtime.settings.default_radius_km
    codes = set(provinces_within(sub.lat, sub.lon, radius))
    codes.add(runtime.geocoder.gazetteer.province_for_point(sub.lat, sub.lon))
    return sorted(codes)


def best_for(
    sub: Subscription,
    entries: list[tuple[str | None, QueueEntry]],
    today: dt.date,
    stale_after_days: int,
) -> RankedResult | None:
    """Najwcześniejszy przyszły termin pasujący do subskrypcji (obszar, wariant, data)."""
    matcher = Resolution(
        "ok", sub.label, sub.benefit_query, variant=sub.variant, exclude=sub.exclude
    )
    point = (
        GeoPoint(lat=sub.lat, lon=sub.lon) if sub.lat is not None and sub.lon is not None else None
    )
    candidates: list[Candidate] = []
    for province, entry in entries:
        first = entry.first_date
        if first is None or first < today or not matcher.matches(entry):
            continue
        if sub.before_date is not None and first >= sub.before_date:
            continue
        if sub.province and province != sub.province:
            continue
        location, distance = nearest_location(entry, point)
        if point is not None:
            radius = sub.radius_km
            if distance is None or (radius is not None and distance > radius):
                continue
        candidates.append(Candidate(entry, location, province, distance))
    ranked = rank(
        candidates,
        RankingOptions(today=today, mode=SortMode.FASTEST, stale_after_days=stale_after_days),
    )
    return ranked[0] if ranked else None


class Watcher:
    def __init__(
        self,
        runtime: Runtime,
        notifiers: dict[str, Notifier],
        *,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self.runtime = runtime
        self.store = SubscriptionStore(runtime.conn)
        self.notifiers = notifiers
        self._now = now

    async def run_once(self) -> WatchReport:
        report = WatchReport()
        subs = self.store.active()
        report.subscriptions = len(subs)
        if not subs:
            return report

        groups: dict[GroupKey, list[Subscription]] = defaultdict(list)
        sub_keys: dict[str, list[GroupKey]] = {}
        for sub in subs:
            keys = [
                (sub.benefit_query, sub.case_no, p)
                for p in subscription_provinces(sub, self.runtime)
            ]
            sub_keys[sub.token] = keys
            for key in keys:
                groups[key].append(sub)

        fetched: dict[GroupKey, list[tuple[str | None, QueueEntry]]] = {}

        async def fetch(key: GroupKey) -> None:
            query, case_no, province = key
            try:
                entries = await self.runtime.client.queues(
                    QueueQuery(benefit=query, province=province, case=case_no)
                )
            except NfzError as exc:
                report.errors.append(f"{query}@{province}: {exc}")
                return
            self.runtime.snapshots.save(
                query=query,
                province=province,
                case_no=case_no,
                entries=entries,
                source="scheduler",
                now=self._now(),
            )
            fetched[key] = [(province_from_teryt(e.teryt_place) or province, e) for e in entries]

        await asyncio.gather(*(fetch(key) for key in groups))
        report.queries = len(groups)

        now = self._now()
        today = self.runtime.today()
        cooldown = dt.timedelta(hours=self.runtime.settings.notify_cooldown_h)
        for sub in subs:
            seen: set[str] = set()
            entries: list[tuple[str | None, QueueEntry]] = []
            for key in sub_keys[sub.token]:
                for province, entry in fetched.get(key, []):
                    if entry.id not in seen:
                        seen.add(entry.id)
                        entries.append((province, entry))
            self.store.mark_checked(sub.token, now)
            best = best_for(sub, entries, today, self.runtime.settings.stale_after_days)
            if best is None or best.entry.first_date is None:
                report.no_match += 1
                continue
            baseline = sub.last_notified_date
            if baseline is not None and baseline < today:
                baseline = None  # poprzednio zgłoszony termin już minął: liczymy od nowa
            if baseline is not None and best.entry.first_date >= baseline:
                report.not_better += 1
                continue
            if sub.last_notified_at is not None and now - sub.last_notified_at < cooldown:
                report.cooldown += 1
                continue
            await self._notify(sub, best, report, now)
        log.info(
            "watcher.run",
            subscriptions=report.subscriptions,
            queries=report.queries,
            notified=report.notified,
            errors=len(report.errors),
        )
        return report

    async def _notify(
        self, sub: Subscription, best: RankedResult, report: WatchReport, now: dt.datetime
    ) -> None:
        notifier = self.notifiers.get(sub.channel)
        if notifier is None:
            report.errors.append(f"kanał {sub.channel} nie jest skonfigurowany")
            NOTIFICATIONS.labels(sub.channel, "not_configured").inc()
            return
        notification = build_notification(sub, best, self.runtime.settings)
        try:
            await notifier.send(sub.target, notification)
        except NotifyError as exc:
            report.errors.append(str(exc))
            NOTIFICATIONS.labels(sub.channel, "error").inc()
            log.warning("watcher.notify_failed", channel=sub.channel, reason=str(exc))
            return
        assert best.entry.first_date is not None
        self.store.mark_notified(sub.token, best.entry.first_date, best.entry.id, now)
        NOTIFICATIONS.labels(sub.channel, "ok").inc()
        report.notified += 1

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Pętla z interwałem `watch_interval_min` (± 10% jitteru)."""
        stop = stop or asyncio.Event()
        interval = self.runtime.settings.watch_interval_min * 60
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # pętla musi przeżyć pojedynczy błąd
                log.exception("watcher.error")
            delay = interval * random.uniform(0.9, 1.1)  # noqa: S311
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue

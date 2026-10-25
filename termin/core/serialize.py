"""Serializacja wyników do JSON (wspólna dla CLI `--json` i API web)."""

from __future__ import annotations

from typing import Any

from termin.core.models import RankedResult
from termin.core.ranking import alert_text
from termin.core.search import SearchResponse


def result_to_dict(r: RankedResult) -> dict[str, Any]:
    loc = r.location
    return {
        "id": r.entry.id,
        "benefit": r.entry.benefit,
        "provider": r.entry.provider,
        "place": loc.place,
        "address": loc.address,
        "locality": loc.locality,
        "phone": loc.phone or r.entry.phone,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "first_date": r.expected_date.isoformat() if r.expected_date else None,
        "forecast": r.entry.is_forecast,
        "pcus": r.entry.pcus,
        "in_cer": r.entry.in_cer,
        "location_approx": r.entry.location_approx,
        "as_at": r.entry.as_at.isoformat() if r.entry.as_at else None,
        "days_until": r.days_until,
        "distance_km": r.distance_km,
        "travel_min": r.travel_min,
        "score": r.score,
        "province": r.province,
        "awaiting": r.entry.awaiting,
        "average_period": r.entry.average_period,
        "for_children": r.entry.is_for_children,
        "many_places": r.entry.many_places,
        "transport": r.entry.public_transport_lines,
        "barrier_free": r.entry.barrier_free,
        "accessibility": {
            "toilet": r.entry.toilet,
            "ramp": r.entry.ramp,
            "wheelchairs": r.entry.wheelchairs,
            "automatic_door": r.entry.automatic_door,
            "car_park": r.entry.car_park,
            "elevator": r.entry.elevator,
        },
        "explanation": r.explanation,
        "reasons": [{"code": x.code, "params": x.params} for x in r.reasons],
        "alerts": [{"code": a.code, "params": a.params, "text": alert_text(a)} for a in r.alerts],
    }


def response_to_dict(response: SearchResponse) -> dict[str, Any]:
    res = response.resolution
    point = response.point
    return {
        "status": res.status,
        "query": res.query,
        "label": res.label,
        "variant": res.variant.value,
        "candidates": [
            {
                "id": c.entry.id,
                "label": c.entry.label,
                "query": c.entry.aliases[0],
                "score": round(c.score),
            }
            for c in res.candidates
        ],
        "has_child_variant": bool(res.entry and res.entry.has_child_variant),
        "note": res.entry.note if res.entry else None,
        "point": (
            {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source}
            if point
            else None
        ),
        "provinces": response.provinces,
        "mode": response.request.mode.value,
        "case": int(response.request.case),
        "radius_km": response.request.radius_km,
        "today": response.today.isoformat(),
        "total_found": response.total_found,
        "without_location": response.without_location,
        "benefit_names": response.benefit_names,
        "medians": response.medians,
        "results": [result_to_dict(r) for r in response.results],
    }

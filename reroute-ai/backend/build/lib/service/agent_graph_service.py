"""LangGraph-backed propose workflow (tools -> classification -> ranking context).

This module intentionally returns plain dict state so existing API schemas and
persistence paths in ``agent_service`` stay compatible.

Enhanced with:
  - LLM-powered ranking explanations + disruption narratives
  - Passenger data validation (reject fakes)
  - Connection feasibility filtering
  - Personalized scoring (cabin_class, budget_band)
  - Offer expiry tracking
  - Price comparison (old vs new)
  - No mock fallback (clear error if no offers)
  - Weather-severity-aware cascade
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from agent.tools import fetch_flight_status, fetch_weather_signals, search_alternatives
from integrations.location_resolver import IATA_TO_CITY, resolve_coords

logger = logging.getLogger(__name__)


class AgentGraphState(TypedDict, total=False):
    trip_context: dict[str, Any]
    simulate_disruption: str | None
    flight_status: dict[str, Any]
    weather: dict[str, Any]
    offers: list[dict[str, Any]]
    duffel_passengers: list[dict[str, Any]]
    search_meta: dict[str, Any]
    tool_trace_summary: list[str]
    disruption_type: str
    delay_minutes: int | None
    booking_mode: str
    options: list[dict[str, Any]]
    options_by_offer_id: dict[str, dict[str, Any]]
    cascade_preview: dict[str, Any]
    compensation_draft: dict[str, Any]
    checkpoint_events: list[dict[str, Any]]
    # New fields
    llm_disruption_narrative: str | None
    cascade_narrative: str | None
    offers_expired_at: str | None
    price_comparison: dict[str, Any] | None
    passenger_validation: dict[str, Any] | None


class AgentConfirmGraphState(TypedDict, total=False):
    booking_mode: str
    trip_context: dict[str, Any]
    selected_option_id: str
    applied_option_id: str
    options_by_offer_id: dict[str, dict[str, Any]]
    option_ctx: dict[str, Any]
    requires_user_review: bool
    acknowledged_uncertainty: bool
    duffel_passengers: list[dict[str, Any]]
    passenger_details: list[dict[str, Any]]
    stale_offer: bool
    duffel_order_id: str | None
    can_apply: bool
    error_message: str | None
    checkpoint_events: list[dict[str, Any]]


# ── Utilities ─────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _append_checkpoint(
    state: AgentGraphState | AgentConfirmGraphState,
    *,
    node: str,
    details: dict[str, Any] | None = None,
) -> None:
    ev = {"at": _now_iso(), "node": node, "details": details or {}}
    state.setdefault("checkpoint_events", []).append(ev)  # type: ignore[call-arg]


def _extract_http_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _extract_http_error_codes(exc: Exception) -> list[str]:
    response = getattr(exc, "response", None)
    if response is None:
        return []
    try:
        payload = response.json()
    except Exception:
        return []
    return [e.get("code") for e in (payload.get("errors") or []) if isinstance(e.get("code"), str)]


def _safe_int(v: object) -> int | None:
    try:
        return int(v) if v is not None else None  # type: ignore[arg-type]
    except Exception:
        return None


def _delay_label(delay_minutes: int | None) -> str:
    if delay_minutes is None:
        return "unknown delay"
    h, m = divmod(max(0, int(delay_minutes)), 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def _extract_arrival_time(offer: dict[str, Any]) -> str | None:
    for s in offer.get("slices") or []:
        for seg in (s.get("segments") or []) if isinstance(s, dict) else []:
            if seg.get("arriving_at"):
                return str(seg["arriving_at"])
    return None


def _extract_departure_time(offer: dict[str, Any]) -> str | None:
    for s in offer.get("slices") or []:
        for seg in (s.get("segments") or []) if isinstance(s, dict) else []:
            if seg.get("departing_at"):
                return str(seg["departing_at"])
    return None


def _extract_offer_legs(offer: dict[str, Any]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    for s in offer.get("slices") or []:
        if not isinstance(s, dict):
            continue
        for seg in s.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            origin = seg.get("origin") or {}
            destination = seg.get("destination") or {}
            carrier = seg.get("marketing_carrier") or {}
            legs.append({
                "from": origin.get("iata_code") if isinstance(origin, dict) else None,
                "to": destination.get("iata_code") if isinstance(destination, dict) else None,
                "departure_time": seg.get("departing_at"),
                "arrival_time": seg.get("arriving_at"),
                "flight_number": seg.get("operating_carrier_flight_number") or seg.get("marketing_carrier_flight_number"),
                "carrier": carrier.get("name") if isinstance(carrier, dict) else None,
            })
    return legs


def _count_stops(offer: dict[str, Any]) -> int:
    total_segments = 0
    for s in offer.get("slices") or []:
        if isinstance(s, dict):
            total_segments += len(s.get("segments") or [])
    return max(0, total_segments - 1)


def _compute_duration_minutes(offer: dict[str, Any]) -> int | None:
    dep = _extract_departure_time(offer)
    arr = _extract_arrival_time(offer)
    if not dep or not arr:
        return None
    try:
        d = datetime.fromisoformat(dep.replace("Z", "+00:00"))
        a = datetime.fromisoformat(arr.replace("Z", "+00:00"))
        return max(0, int((a - d).total_seconds() / 60))
    except Exception:
        return None


def _weather_code_label(code: int | None) -> str:
    if code is None:
        return "Unknown"
    labels = {
        0: "Clear sky", 1: "Partly cloudy", 2: "Partly cloudy", 3: "Partly cloudy",
        45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
        61: "Rain", 63: "Rain", 65: "Rain", 80: "Rain", 81: "Rain", 82: "Rain",
        71: "Snow", 73: "Snow", 75: "Snow", 85: "Snow", 86: "Snow",
        95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
    }
    return labels.get(code, f"Code {code}")


def _is_severe_weather(code: int | None) -> bool:
    """Weather codes that could cause flight disruptions."""
    return code is not None and code in {65, 75, 82, 85, 86, 95, 96, 99}


# ── Passenger Validation ──────────────────────────────────────

def _validate_passengers(passengers: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate passenger data, return warnings and errors."""
    warnings: list[str] = []
    errors: list[str] = []
    valid = True

    if not passengers:
        errors.append("No passengers provided")
        return {"valid": False, "warnings": warnings, "errors": errors}

    for i, p in enumerate(passengers, 1):
        label = f"Passenger {i}"
        gn = p.get("given_name", "")
        fn = p.get("family_name", "")
        if not gn or not fn:
            errors.append(f"{label}: Missing name")
            valid = False
        if gn in ("Traveler", "Guest") or fn in ("Traveler", ""):
            warnings.append(f"{label}: Name looks like a placeholder ({gn} {fn})")

        born_on = p.get("born_on", "")
        if not born_on:
            errors.append(f"{label}: Missing date of birth")
            valid = False
        elif born_on == "1990-01-01":
            warnings.append(f"{label}: DOB looks like a default (1990-01-01)")

        phone = p.get("phone_number", "")
        if not phone:
            warnings.append(f"{label}: Missing phone number")
        elif phone == "+14155552671":
            warnings.append(f"{label}: Phone looks like a test number")
        elif not re.fullmatch(r"\+\d{8,15}", phone.replace(" ", "")):
            warnings.append(f"{label}: Phone format may be invalid")

        email = p.get("email", "")
        if email == "traveler@example.com":
            warnings.append(f"{label}: Email is a placeholder")

    return {"valid": valid, "warnings": warnings, "errors": errors}


# ── Clean Passenger Payload for Duffel ────────────────────────

def _clean_passenger_payload(base: dict[str, Any], pid: Any) -> dict[str, Any]:
    def _pick_str(key: str, default: str) -> str:
        v = base.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else default

    def _norm_gender(v: Any) -> str:
        if isinstance(v, str):
            t = v.strip().lower()
            if t in {"m", "male"}:
                return "m"
            if t in {"f", "female"}:
                return "f"
            if t in {"x", "other", "nonbinary", "non-binary"}:
                return "x"
        return "m"

    out: dict[str, Any] = {
        "id": pid,
        "email": _pick_str("email", "traveler@example.com"),
        "title": _pick_str("title", "mr"),
        "gender": _norm_gender(base.get("gender")),
        "family_name": _pick_str("family_name", "Traveler"),
        "given_name": _pick_str("given_name", "Guest"),
    }
    born_on = base.get("born_on")
    if isinstance(born_on, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", born_on.strip()):
        out["born_on"] = born_on.strip()
    else:
        out["born_on"] = "1990-01-01"
    phone = base.get("phone_number")
    if isinstance(phone, str):
        p = phone.strip().replace(" ", "")
        if re.fullmatch(r"\+\d{8,15}", p):
            out["phone_number"] = p
    if "phone_number" not in out:
        out["phone_number"] = "+14155552671"
    return {k: v for k, v in out.items() if v not in (None, "")}


# ── LLM Helpers (lazy-loaded) ─────────────────────────────────

async def _llm_generate(prompt: str, max_tokens: int = 300) -> str | None:
    """Call GPT-4o-mini for short text generation. Returns None on failure."""
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        from agent.policy import SYSTEM_PROMPT as AGENT_SYSTEM_PROMPT
        from config import get_settings
        settings = get_settings()
        if not settings.OPENAI_API_KEY:
            return None

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=settings.OPENAI_API_KEY, max_tokens=max_tokens)
        resp = await llm.ainvoke([
            SystemMessage(content=AGENT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception:
        logger.debug("llm_generate_failed", exc_info=True)
        return None


async def _llm_rank_explanation(option: dict[str, Any], disruption_type: str, delay_minutes: int | None, trip_context: dict[str, Any]) -> str | None:
    """Generate a short explanation for why this rebooking option is good."""
    legs = trip_context.get("legs", {})
    primary = legs.get("primary_flight", {}) if isinstance(legs, dict) else {}
    prefs = trip_context.get("preferences", {})
    meeting = (legs.get("meeting", {}) if isinstance(legs, dict) else {}).get("scheduled_time_utc", "")

    prompt = (
        f"Flight {primary.get('flight_number', 'N/A')} from {primary.get('origin', '?')} to {primary.get('destination', '?')} "
        f"was {disruption_type}{f' by {_delay_label(delay_minutes)}' if delay_minutes else ''}.\n\n"
        f"Rebooking option: {option.get('summary', 'N/A')}\n"
        f"Stops: {option.get('stops', 0)}\n"
        f"User preferences: cabin={prefs.get('cabin_class', 'economy')}, budget={prefs.get('budget_band', 'mid')}\n"
        f"{'Meeting at: ' + meeting if meeting else 'No meeting scheduled.'}\n\n"
        f"In 1-2 sentences, explain why this is a good or bad rebooking option for this traveler. "
        f"Mention price, timing, stops, and whether they'll make their meeting if applicable."
    )
    return await _llm_generate(prompt, max_tokens=150)


async def _llm_disruption_narrative(flight_status: dict, weather: dict, trip_context: dict, disruption_type: str, delay_minutes: int | None) -> str | None:
    """Generate a natural language disruption explanation."""
    legs = trip_context.get("legs", {})
    primary = legs.get("primary_flight", {}) if isinstance(legs, dict) else {}
    origin_city = IATA_TO_CITY.get(str(primary.get("origin", "")).upper(), primary.get("origin", ""))
    dest_city = IATA_TO_CITY.get(str(primary.get("destination", "")).upper(), primary.get("destination", ""))

    origin_wx = weather.get("origin_latest", {})
    dest_wx = weather.get("destination_latest", {})

    prompt = (
        f"Flight {primary.get('flight_number', 'N/A')} from {origin_city} to {dest_city} on {primary.get('date', 'N/A')}.\n"
        f"Status: {disruption_type}{f', delayed {_delay_label(delay_minutes)}' if disruption_type == 'delayed' and delay_minutes else ''}.\n"
        f"Source: {flight_status.get('source', 'unknown')}.\n"
        f"Origin weather: {_weather_code_label(_safe_int(origin_wx.get('weather_code')))}, "
        f"{origin_wx.get('temperature_2m', '?')}°C, wind {origin_wx.get('wind_speed_10m', '?')} km/h.\n"
        f"Destination weather: {_weather_code_label(_safe_int(dest_wx.get('weather_code')))}, "
        f"{dest_wx.get('temperature_2m', '?')}°C.\n\n"
        f"Write a 2-3 sentence explanation of the disruption situation for the traveler. "
        f"Include likely cause if inferable from weather. Be empathetic but factual."
    )
    return await _llm_generate(prompt, max_tokens=200)


async def _llm_cascade_narrative(cascade_preview: dict, trip_context: dict) -> str | None:
    """Generate a narrative about downstream impact."""
    legs = trip_context.get("legs", {})
    meeting = (legs.get("meeting", {}) if isinstance(legs, dict) else {}).get("scheduled_time_utc", "")
    conn_buffer = (legs.get("connection", {}) if isinstance(legs, dict) else {}).get("departure_after_arrival_minutes", 90)
    hotel_buffer = (legs.get("hotel", {}) if isinstance(legs, dict) else {}).get("check_in_buffer_minutes", 60)

    prompt = (
        f"Disruption: {cascade_preview.get('disruption_type', 'unknown')} — {cascade_preview.get('disruption_message', '')}.\n"
        f"Connection buffer: {conn_buffer} min. Missed connection: {cascade_preview.get('missed_connection', False)}.\n"
        f"Hotel check-in buffer: {hotel_buffer} min.\n"
        f"{'Meeting scheduled at: ' + meeting if meeting else 'No meeting.'}\n\n"
        f"In 2-3 sentences, explain the real-world impact on the traveler's plans. "
        f"Be specific about what they need to do (reschedule meeting, contact hotel, etc.)."
    )
    return await _llm_generate(prompt, max_tokens=200)


# ── Scoring ───────────────────────────────────────────────────

def _score_offer(offer: dict[str, Any], preferences: dict[str, Any] | None = None) -> float:
    """Score offer using cost, arrival time, stops, and user preferences."""
    try:
        amount = float(offer.get("total_amount"))
    except Exception:
        amount = 999999.0

    # Stops penalty
    stops = _count_stops(offer)
    stops_penalty = stops * 50.0

    # Duration penalty
    duration = _compute_duration_minutes(offer)
    duration_penalty = (duration / 60.0) * 10 if duration else 0

    # Arrival time score (earlier is better)
    arrival = _extract_arrival_time(offer)
    arrival_score = len(arrival) * 0.01 if arrival else 100.0

    # Budget preference weighting
    prefs = preferences or {}
    budget = prefs.get("budget_band", "mid")
    if budget == "low":
        cost_weight = 0.8  # Price matters most
    elif budget == "high" or budget == "flexible":
        cost_weight = 0.3  # Convenience matters more
    else:
        cost_weight = 0.5

    time_weight = 1.0 - cost_weight

    score = (amount * cost_weight) + (arrival_score + stops_penalty + duration_penalty) * time_weight
    return score


# ── Connection Feasibility ────────────────────────────────────

def _check_connection_feasible(offer: dict[str, Any], conn_buffer_minutes: int) -> bool:
    """Check if layover between segments meets minimum connection time."""
    for s in offer.get("slices") or []:
        if not isinstance(s, dict):
            continue
        segments = s.get("segments") or []
        for i in range(len(segments) - 1):
            arr = segments[i].get("arriving_at")
            dep = segments[i + 1].get("departing_at")
            if not arr or not dep:
                continue
            try:
                arr_dt = datetime.fromisoformat(arr.replace("Z", "+00:00"))
                dep_dt = datetime.fromisoformat(dep.replace("Z", "+00:00"))
                layover = (dep_dt - arr_dt).total_seconds() / 60
                if layover < conn_buffer_minutes:
                    return False
            except Exception:
                continue
    return True


# ── Graph Nodes ───────────────────────────────────────────────

async def _observe_flight(state: AgentGraphState) -> AgentGraphState:
    trip_context = state["trip_context"]
    legs_block = trip_context.get("legs") if isinstance(trip_context.get("legs"), dict) else {}
    primary = legs_block.get("primary_flight") if isinstance(legs_block.get("primary_flight"), dict) else {}
    try:
        fs = await fetch_flight_status(
            flight_number=str(primary.get("flight_number") or ""),
            date=str(primary.get("date") or ""),
            simulate_disruption=state.get("simulate_disruption"),
        )
    except Exception as e:
        fs = {"status": "unknown", "delay_minutes": None, "source": f"error:{type(e).__name__}"}
    _append_checkpoint(state, node="observe_flight", details={"status": fs.get("status"), "source": fs.get("source")})
    return {"flight_status": fs, "checkpoint_events": state.get("checkpoint_events", [])}


async def _observe_weather(state: AgentGraphState) -> AgentGraphState:
    trip_context = state["trip_context"]
    legs_block = trip_context.get("legs") if isinstance(trip_context.get("legs"), dict) else {}
    primary = legs_block.get("primary_flight") if isinstance(legs_block.get("primary_flight"), dict) else {}
    wx = legs_block.get("weather") if isinstance(legs_block.get("weather"), dict) else {}
    origin_lat, origin_lon = wx.get("origin_lat"), wx.get("origin_lon")
    dest_lat, dest_lon = wx.get("destination_lat"), wx.get("destination_lon")

    if dest_lat is None or dest_lon is None:
        dest_code = primary.get("destination")
        if isinstance(dest_code, str) and dest_code.strip():
            coords = await resolve_coords(dest_code)
            if coords:
                dest_lat, dest_lon = coords
    if origin_lat is None or origin_lon is None:
        origin_code = primary.get("origin")
        if isinstance(origin_code, str) and origin_code.strip():
            coords = await resolve_coords(origin_code)
            if coords:
                origin_lat, origin_lon = coords

    weather: dict[str, Any] = {"source": "skipped_missing_coords", "origin_latest": {}, "destination_latest": {}}
    if dest_lat is not None and dest_lon is not None:
        try:
            dest_w = await fetch_weather_signals(latitude=float(dest_lat), longitude=float(dest_lon))
            weather["destination_latest"] = dest_w.get("latest") or {}
            weather["source"] = dest_w.get("source") or weather["source"]
        except Exception:
            weather["destination_latest"] = {}
            weather["source"] = "error"
    if origin_lat is not None and origin_lon is not None:
        try:
            org_w = await fetch_weather_signals(latitude=float(origin_lat), longitude=float(origin_lon))
            weather["origin_latest"] = org_w.get("latest") or {}
            if weather["source"] == "skipped_missing_coords":
                weather["source"] = org_w.get("source") or weather["source"]
        except Exception:
            weather["origin_latest"] = {}
            if weather["source"] == "skipped_missing_coords":
                weather["source"] = "error"

    _append_checkpoint(state, node="observe_weather", details={
        "source": weather.get("source"),
        "origin_lat": origin_lat, "dest_lat": dest_lat,
    })
    return {"weather": weather, "checkpoint_events": state.get("checkpoint_events", [])}


async def _search_offers(state: AgentGraphState) -> AgentGraphState:
    try:
        search = await search_alternatives(
            trip_context=state["trip_context"],
            simulate_disruption=state.get("simulate_disruption"),
        )
    except Exception:
        search = {"source": "duffel_error", "orq": {"data": {"offers": [], "passengers": []}}}
    orq = (search.get("orq") or {}).get("data") or {}
    meta = (search.get("orq") or {}).get("_reroute_meta") or {}
    offers = orq.get("offers") or []
    passengers = orq.get("passengers") or []

    # Track offer expiry (~30 min from now)
    offers_expired_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat() if offers else None

    _append_checkpoint(state, node="search_offers", details={
        "offers_count": len(offers),
        "date_shifted": bool(meta.get("date_shifted")),
        "selected_departure_date": meta.get("selected_departure_date"),
    })
    return {
        "offers": offers,
        "duffel_passengers": passengers,
        "search_meta": meta,
        "offers_expired_at": offers_expired_at,
        "checkpoint_events": state.get("checkpoint_events", []),
    }


def _classify(state: AgentGraphState) -> AgentGraphState:
    fs = state.get("flight_status") or {}
    weather = state.get("weather") or {}
    offers = state.get("offers") or []
    search_meta = state.get("search_meta") if isinstance(state.get("search_meta"), dict) else {}

    raw_status = str(fs.get("status") or "unknown").lower()
    if raw_status not in {"delayed", "cancelled", "diverted", "unknown"}:
        raw_status = "unknown"
    delay_minutes = _safe_int(fs.get("delay_minutes"))
    if raw_status == "cancelled":
        delay_minutes = 0

    origin_latest = weather.get("origin_latest", {}) if isinstance(weather, dict) else {}
    dest_latest = weather.get("destination_latest", {}) if isinstance(weather, dict) else {}

    def _weather_text(prefix: str, latest: dict[str, Any]) -> str:
        code = _safe_int(latest.get("weather_code"))
        precip = _safe_int(latest.get("precipitation_probability"))
        wind = latest.get("wind_speed_10m")
        temp = latest.get("temperature_2m")
        severe = " [SEVERE]" if _is_severe_weather(code) else ""
        if not latest:
            return f"weather_{prefix}: unavailable"
        return (
            f"weather_{prefix}: {_weather_code_label(code)}{severe}"
            f"{f', {temp}°C' if temp is not None else ''}"
            f"{f', rain chance {precip}%' if precip is not None else ''}"
            f"{f', wind {wind} km/h' if wind is not None else ''}"
        )

    tool_trace_summary = [
        f"flight_status: {raw_status} (source={fs.get('source')})",
        f"delay_minutes: {delay_minutes if delay_minutes is not None else 'unknown'}",
        _weather_text("origin", origin_latest),
        _weather_text("destination", dest_latest),
        f"duffel_offers_count: {len(offers) if isinstance(offers, list) else 0} (source=duffel)",
    ]
    if search_meta.get("date_shifted"):
        tool_trace_summary.append(
            f"offer_date_shift: requested={search_meta.get('requested_departure_date')} "
            f"selected={search_meta.get('selected_departure_date')}"
        )

    # Validate passengers
    pax = state["trip_context"].get("passengers", [])
    pax_validation = _validate_passengers(pax)
    if pax_validation["warnings"]:
        tool_trace_summary.append(f"passenger_warnings: {'; '.join(pax_validation['warnings'])}")

    _append_checkpoint(state, node="classify", details={"disruption_type": raw_status, "delay_minutes": delay_minutes})
    return {
        "disruption_type": raw_status,
        "delay_minutes": delay_minutes,
        "tool_trace_summary": tool_trace_summary,
        "passenger_validation": pax_validation,
        "checkpoint_events": state.get("checkpoint_events", []),
    }


def _rank_options(state: AgentGraphState) -> AgentGraphState:
    trip_context = state["trip_context"]
    legs_block = trip_context.get("legs") if isinstance(trip_context.get("legs"), dict) else {}
    primary = legs_block.get("primary_flight") if isinstance(legs_block.get("primary_flight"), dict) else {}
    preferences = trip_context.get("preferences") if isinstance(trip_context.get("preferences"), dict) else {}
    conn_sub = legs_block.get("connection") if isinstance(legs_block.get("connection"), dict) else {}
    conn_buffer = int(conn_sub.get("departure_after_arrival_minutes") or 30)

    offers = list(state.get("offers") or [])
    booking_mode = "live"
    is_simulate = bool(state.get("simulate_disruption"))

    if not offers and not is_simulate:
        # No real offers and not in demo mode — report clearly
        booking_mode = "no_offers"
        _append_checkpoint(state, node="rank_options", details={"booking_mode": "no_offers", "options_count": 0})
        return {
            "booking_mode": booking_mode,
            "options_by_offer_id": {},
            "options": [],
            "checkpoint_events": state.get("checkpoint_events", []),
        }

    if not offers and is_simulate:
        # Demo/simulate mode with no real offers — generate mock offers for presentation
        booking_mode = "mock"
        origin = primary.get("origin", "JFK")
        dest = primary.get("destination", "ATL")
        offers = [
            {"id": f"mock_offer_{i}", "total_amount": str(200 + i * 75), "total_currency": "USD",
             "slices": [{"segments": [{"origin": {"iata_code": origin}, "destination": {"iata_code": dest},
              "departing_at": f"{primary.get('date', '2026-04-01')}T{8+i*2:02d}:00:00",
              "arriving_at": f"{primary.get('date', '2026-04-01')}T{11+i*2:02d}:30:00",
              "operating_carrier_flight_number": f"{1000+i*111}",
              "marketing_carrier": {"name": ["Delta", "United", "American"][i-1]},
              "passengers": [{"cabin_class_marketing_name": "Economy"}]}]}]
            }
            for i in range(1, 4)
        ]
        logger.info("rank_options_mock_for_simulate", extra={"count": len(offers)})

    # Deduplicate offers by itinerary shape (origin→dest + departure time, rounded to 10min).
    # Duffel returns multiple fare classes per flight — we keep ONLY the cheapest per unique journey.
    def _itinerary_key(offer: dict[str, Any]) -> str:
        parts = []
        for s in offer.get("slices") or []:
            if not isinstance(s, dict):
                continue
            for seg in s.get("segments") or []:
                if not isinstance(seg, dict):
                    continue
                origin = (seg.get("origin") or {})
                dest = (seg.get("destination") or {})
                o_iata = origin.get("iata_code", "") if isinstance(origin, dict) else ""
                d_iata = dest.get("iata_code", "") if isinstance(dest, dict) else ""
                # Round departure to 10-min window so minor time variants collapse
                dep = str(seg.get("departing_at") or "")[:15]  # "2026-04-01T05:2" — 10-min bucket
                parts.append(f"{o_iata}>{d_iata}@{dep}")
        return "|".join(parts) or str(offer.get("id", ""))

    seen_itineraries: dict[str, dict[str, Any]] = {}
    for offer in offers:
        key = _itinerary_key(offer)
        existing = seen_itineraries.get(key)
        if existing is None:
            seen_itineraries[key] = offer
        else:
            # Keep the cheaper one
            try:
                new_price = float(offer.get("total_amount") or 999999)
                old_price = float(existing.get("total_amount") or 999999)
                if new_price < old_price:
                    seen_itineraries[key] = offer
            except (ValueError, TypeError):
                pass
    deduped = list(seen_itineraries.values())
    logger.info("rank_options_dedup", extra={"original": len(offers), "deduped": len(deduped)})

    # Filter out offers with infeasible connections
    feasible = [o for o in deduped if _check_connection_feasible(o, conn_buffer)]
    if not feasible:
        feasible = deduped  # Fall back to all if none are feasible
        logger.info("all_offers_infeasible_connections, using all offers anyway")

    # Score with personalized preferences
    scored = sorted(feasible, key=lambda o: _score_offer(o, preferences))

    options_by_offer_id: dict[str, dict[str, Any]] = {}
    ranked: list[dict[str, Any]] = []

    # Show up to 3 truly distinct options
    top = scored[:3]
    if len(top) == 1:
        logger.info("only_one_unique_itinerary_after_dedup", extra={"total_offers": len(offers)})

    for idx, offer in enumerate(top, start=1):
        offer_id = str(offer.get("id") or f"offer_{idx}")
        total_amount = str(offer.get("total_amount") or "0")
        total_currency = str(offer.get("total_currency") or "USD")
        arrival_time = _extract_arrival_time(offer)
        offer_legs = _extract_offer_legs(offer)
        stops = _count_stops(offer)
        duration = _compute_duration_minutes(offer)

        # Extract cabin/fare class from Duffel offer if available
        fare_label = ""
        try:
            slices = offer.get("slices") or []
            if slices and isinstance(slices[0], dict):
                fare_brand = slices[0].get("fare_brand_name") or ""
                cabin = (slices[0].get("segments") or [{}])[0].get("passengers", [{}])[0].get("cabin_class_marketing_name", "")
                fare_label = fare_brand or cabin
        except Exception:
            pass

        route_points = [str(x.get("from")) for x in offer_legs if x.get("from")] + (
            [str(offer_legs[-1].get("to"))] if offer_legs and offer_legs[-1].get("to") else []
        )
        route_chain = " → ".join(route_points) if route_points else f"{primary.get('origin', '?')} → {primary.get('destination', '?')}"

        # Format display strings
        price_display = f"{total_currency} {total_amount}"
        arrival_display = None
        if arrival_time:
            try:
                dt = datetime.fromisoformat(arrival_time.replace("Z", "+00:00"))
                arrival_display = dt.strftime("%b %d, %I:%M %p")
            except Exception:
                arrival_display = arrival_time

        options_by_offer_id[offer_id] = {
            "duffel_offer_id": offer_id,
            "payments": [{"type": "balance", "currency": total_currency, "amount": total_amount}],
            "arrival_time": arrival_time,
            "legs": offer_legs,
        }
        ranked.append({
            "option_id": offer_id,
            "score": float(idx),
            "summary": f"{route_chain} · arrive {arrival_display or 'TBD'} · {price_display}" + (f" · {fare_label}" if fare_label else "") + f" · {stops} stop{'s' if stops != 1 else ''}" + (f" · {duration // 60}h {duration % 60}m" if duration else ""),
            "legs": offer_legs or [{"from": primary.get("origin"), "to": primary.get("destination"), "arrival_time": arrival_time}],
            "modality": "flight",
            "price_display": price_display,
            "arrival_display": arrival_display,
            "stops": stops,
            "duration_minutes": duration,
        })

    _append_checkpoint(state, node="rank_options", details={
        "booking_mode": booking_mode,
        "options_count": len(ranked),
        "total_offers": len(offers),
        "feasible_offers": len(feasible),
    })
    return {
        "booking_mode": booking_mode,
        "options_by_offer_id": options_by_offer_id,
        "options": ranked,
        "checkpoint_events": state.get("checkpoint_events", []),
    }


def _build_outputs(state: AgentGraphState) -> AgentGraphState:
    trip_context = state["trip_context"]
    legs_block = trip_context.get("legs") if isinstance(trip_context.get("legs"), dict) else {}
    disruption_type = str(state.get("disruption_type") or "unknown")
    delay_minutes = state.get("delay_minutes")
    weather = state.get("weather") or {}

    conn_sub = legs_block.get("connection") if isinstance(legs_block.get("connection"), dict) else {}
    conn_buffer = int(conn_sub.get("departure_after_arrival_minutes") or 90)
    missed_connection = disruption_type in ("delayed", "diverted") and (delay_minutes or 0) >= conn_buffer

    if disruption_type == "delayed":
        disruption_message = f"Flight delayed by {_delay_label(delay_minutes)}."
    elif disruption_type == "cancelled":
        disruption_message = "Flight cancelled."
    elif disruption_type == "diverted":
        disruption_message = "Flight diverted."
    else:
        disruption_message = "Live disruption status unavailable right now."

    meet_sub = legs_block.get("meeting") if isinstance(legs_block.get("meeting"), dict) else {}
    meeting_scheduled = meet_sub.get("scheduled_time_utc") or ""
    hotel_sub = legs_block.get("hotel") if isinstance(legs_block.get("hotel"), dict) else {}
    hotel_buffer = int(hotel_sub.get("check_in_buffer_minutes") or 60)

    if disruption_type in ("delayed", "diverted") and delay_minutes is not None:
        meeting_message = f"Meeting likely delayed by ~{delay_minutes} minutes." + (f" Consider rescheduling from {meeting_scheduled}." if meeting_scheduled else "")
        hotel_message = f"Hotel check-in adjusted: late arrival buffer ~{delay_minutes} minutes."
    elif disruption_type == "cancelled":
        meeting_message = "Meeting rescheduling recommended due to cancellation."
        hotel_message = "Hotel check-in adjustment recommended due to cancellation."
    else:
        meeting_message = "Meeting impact unknown until live status stabilizes."
        hotel_message = "Hotel impact unknown until live status stabilizes."

    # Weather severity flag
    origin_wx = weather.get("origin_latest", {})
    dest_wx = weather.get("destination_latest", {})
    origin_severe = _is_severe_weather(_safe_int(origin_wx.get("weather_code")))
    dest_severe = _is_severe_weather(_safe_int(dest_wx.get("weather_code")))
    weather_warning = None
    if origin_severe:
        weather_warning = f"Severe weather at origin: {_weather_code_label(_safe_int(origin_wx.get('weather_code')))}"
    if dest_severe:
        w = f"Severe weather at destination: {_weather_code_label(_safe_int(dest_wx.get('weather_code')))}"
        weather_warning = f"{weather_warning}. {w}" if weather_warning else w

    comp_eligible = disruption_type in ("cancelled", "diverted") or (
        disruption_type == "delayed" and (delay_minutes or 0) >= 120
    )

    # Price comparison
    options = state.get("options") or []
    price_comparison = None
    if options:
        cheapest = options[0]
        try:
            price_val = float(str(cheapest.get("price_display", "0")).split()[-1])
            price_comparison = {
                "cheapest_option": cheapest.get("option_id"),
                "cheapest_price": cheapest.get("price_display"),
                "note": "This is the cost for the replacement flight. Your original ticket refund depends on airline policy.",
            }
        except Exception:
            pass

    cascade_preview = {
        "disruption_type": disruption_type,
        "disruption_message": disruption_message,
        "delay_minutes": delay_minutes,
        "missed_connection": missed_connection,
        "hotel_update_message": hotel_message,
        "meeting_update_message": meeting_message,
        "weather_warning": weather_warning,
        "what_we_changed_summary": ["proposed top-3 rebooking options", "computed likely cascade from live status"],
    }
    compensation_draft = {
        "eligible": bool(comp_eligible),
        "eligibility_basis": {
            "rules_used": "EU261_and_DOT_thresholds",
            "delay_minutes": delay_minutes,
            "disruption_type": disruption_type,
        },
        "claim_text_draft": (
            "Based on eligibility criteria, you may be entitled to compensation under EU261 (up to €600) "
            "or DOT regulations. Review official rules for final determination."
            if comp_eligible else
            "Based on current delay/disruption thresholds, compensation may not apply. "
            "Rules: EU261 requires 3+ hour delay at arrival; DOT covers cancellations and significant delays."
        ),
        "evidence_checklist": ["flight status record", "rebooking receipts", "incident notes", "bank details if submitting"],
    }
    _append_checkpoint(state, node="build_outputs")
    return {
        "cascade_preview": cascade_preview,
        "compensation_draft": compensation_draft,
        "price_comparison": price_comparison,
        "checkpoint_events": state.get("checkpoint_events", []),
    }


# ── LLM Enhancement Node (runs after build_outputs) ──────────

async def _enhance_with_llm(state: AgentGraphState) -> AgentGraphState:
    """Add LLM-generated narratives to the state. Non-blocking — failures are ignored."""
    trip_context = state["trip_context"]
    flight_status = state.get("flight_status") or {}
    weather = state.get("weather") or {}
    disruption_type = str(state.get("disruption_type") or "unknown")
    delay_minutes = state.get("delay_minutes")
    cascade_preview = state.get("cascade_preview") or {}
    options = state.get("options") or []

    # Generate disruption narrative
    narrative = await _llm_disruption_narrative(flight_status, weather, trip_context, disruption_type, delay_minutes)

    # Generate cascade narrative
    cascade_narr = await _llm_cascade_narrative(cascade_preview, trip_context)

    # Generate per-option explanations
    for opt in options:
        explanation = await _llm_rank_explanation(opt, disruption_type, delay_minutes, trip_context)
        if explanation:
            opt["llm_explanation"] = explanation

    _append_checkpoint(state, node="llm_enhance", details={"narrative_generated": narrative is not None})
    return {
        "llm_disruption_narrative": narrative,
        "cascade_narrative": cascade_narr,
        "options": options,
        "checkpoint_events": state.get("checkpoint_events", []),
    }


# ── Main Execution ────────────────────────────────────────────

async def run_propose_graph(*, trip_context: dict[str, Any], simulate_disruption: str | None) -> AgentGraphState:
    """Execute propose workflow via LangGraph when available, with LLM enhancement."""
    state: AgentGraphState = {
        "trip_context": trip_context,
        "simulate_disruption": simulate_disruption,
        "checkpoint_events": [],
    }
    try:
        from langgraph.graph import END, START, StateGraph  # type: ignore

        graph = StateGraph(AgentGraphState)
        graph.add_node("observe_flight", _observe_flight)
        graph.add_node("observe_weather", _observe_weather)
        graph.add_node("search_offers", _search_offers)
        graph.add_node("classify", _classify)
        graph.add_node("rank", _rank_options)
        graph.add_node("outputs", _build_outputs)
        graph.add_node("llm_enhance", _enhance_with_llm)
        graph.add_edge(START, "observe_flight")
        graph.add_edge("observe_flight", "observe_weather")
        graph.add_edge("observe_weather", "search_offers")
        graph.add_edge("search_offers", "classify")
        graph.add_edge("classify", "rank")
        graph.add_edge("rank", "outputs")
        graph.add_edge("outputs", "llm_enhance")
        graph.add_edge("llm_enhance", END)
        app = graph.compile()
        result = await app.ainvoke(state)
        return result
    except Exception:
        state.update(await _observe_flight(state))
        state.update(await _observe_weather(state))
        state.update(await _search_offers(state))
        state.update(_classify(state))
        state.update(_rank_options(state))
        state.update(_build_outputs(state))
        state.update(await _enhance_with_llm(state))
        return state


# ── Confirm Graph Nodes ───────────────────────────────────────

def _confirm_precheck(state: AgentConfirmGraphState) -> AgentConfirmGraphState:
    if state.get("requires_user_review") and not state.get("acknowledged_uncertainty"):
        _append_checkpoint(state, node="confirm_precheck_failed", details={"reason": "missing_ack"})
        return {
            "can_apply": False,
            "error_message": "Live disruption status is uncertain. Please verify with the airline, then confirm again with acknowledgment.",
            "checkpoint_events": state.get("checkpoint_events", []),
        }
    selected = state.get("selected_option_id") or ""
    option_ctx = (state.get("options_by_offer_id") or {}).get(selected)
    if not option_ctx:
        _append_checkpoint(state, node="confirm_precheck_failed", details={"reason": "option_missing"})
        return {
            "can_apply": False,
            "error_message": "Selected option not found in proposal.",
            "checkpoint_events": state.get("checkpoint_events", []),
        }
    _append_checkpoint(state, node="confirm_precheck_ok", details={"selected_option_id": selected})
    return {"option_ctx": option_ctx, "can_apply": True, "error_message": None, "checkpoint_events": state.get("checkpoint_events", [])}


async def _confirm_verify_offer(state: AgentConfirmGraphState) -> AgentConfirmGraphState:
    """Skip pre-verification — just attempt the order directly.
    If the offer is stale, create_order will fail with 422 and the retry logic handles it.
    This avoids the double-round-trip (verify then create) that wastes time and causes false stale detections.
    """
    if not state.get("can_apply"):
        return {"checkpoint_events": state.get("checkpoint_events", [])}
    _append_checkpoint(state, node="confirm_verify_offer_skipped", details={"reason": "direct_order_attempt"})
    return {"stale_offer": False, "checkpoint_events": state.get("checkpoint_events", [])}


async def _confirm_create_order(state: AgentConfirmGraphState) -> AgentConfirmGraphState:
    if not state.get("can_apply"):
        return {"checkpoint_events": state.get("checkpoint_events", [])}
    selected = str(state.get("selected_option_id") or "")

    # No-offers mode: cannot create order
    if state.get("booking_mode") == "no_offers":
        return {
            "can_apply": False,
            "error_message": "No flight offers were available to book. Try again later or search a different date.",
            "checkpoint_events": state.get("checkpoint_events", []),
        }

    if state.get("booking_mode") == "mock":
        order_id = f"mock_order_{selected}"
        _append_checkpoint(state, node="confirm_create_order_mock", details={"order_id": order_id})
        return {
            "duffel_order_id": order_id,
            "applied_option_id": selected,
            "checkpoint_events": state.get("checkpoint_events", []),
        }

    option_ctx = state.get("option_ctx") or {}
    duffel_passengers = state.get("duffel_passengers") or []
    passenger_details = state.get("passenger_details") or []
    is_stale = bool(state.get("stale_offer"))

    # If offer is stale, skip straight to fresh search retry
    if not is_stale:
        try:
            from integrations.duffel_client import create_order

            passengers_payload: list[dict[str, Any]] = []
            for i, duffel_p in enumerate(duffel_passengers):
                pid = duffel_p.get("id")
                base = passenger_details[i] if i < len(passenger_details) else (passenger_details[-1] if passenger_details else {})
                passengers_payload.append(_clean_passenger_payload(base=base, pid=pid))

            order_payload = {
                "data": {
                    "selected_offers": [selected],
                    "payments": option_ctx.get("payments") or [],
                    "passengers": passengers_payload,
                }
            }
            order_resp = await create_order(order_payload=order_payload)
            order_id = (order_resp.get("data") or {}).get("id")
            _append_checkpoint(state, node="confirm_create_order_live", details={"order_id": order_id})
            return {
                "duffel_order_id": order_id,
                "applied_option_id": selected,
                "checkpoint_events": state.get("checkpoint_events", []),
            }
        except Exception as e:
            code = _extract_http_status(e)
            err_codes = _extract_http_error_codes(e)

            if code == 422:
                non_retry_codes = {"invalid_phone_number", "invalid_email", "validation_required", "born_on_does_not_match"}
                retry_allowed = not any(c in non_retry_codes for c in err_codes)
                _append_checkpoint(state, node="confirm_create_order_422", details={"status_code": code, "retry": retry_allowed, "codes": err_codes})

                if not retry_allowed:
                    return {
                        "can_apply": False,
                        "error_message": f"Booking rejected by provider: {', '.join(err_codes)}. Check passenger details.",
                        "checkpoint_events": state.get("checkpoint_events", []),
                    }

                # Retry with fresh search
                try:
                    from agent.tools import search_alternatives
                    from integrations.duffel_client import create_order as create_order_retry

                    trip_context = state.get("trip_context") or {}
                    search = await search_alternatives(trip_context=trip_context, simulate_disruption=None)
                    orq = (search.get("orq") or {}).get("data") or {}
                    fresh_offers = orq.get("offers") or []
                    fresh_passengers = orq.get("passengers") or []
                    if not fresh_offers:
                        raise RuntimeError("No fresh offers available")

                    fresh = fresh_offers[0]
                    fresh_id = str(fresh.get("id") or "")
                    fresh_amount = str(fresh.get("total_amount") or "0")
                    fresh_currency = str(fresh.get("total_currency") or "USD")

                    passengers_payload_retry: list[dict[str, Any]] = []
                    for i, duffel_p in enumerate(fresh_passengers):
                        pid = duffel_p.get("id")
                        base = passenger_details[i] if i < len(passenger_details) else (passenger_details[-1] if passenger_details else {})
                        passengers_payload_retry.append(_clean_passenger_payload(base=base, pid=pid))

                    retry_payload = {
                        "data": {
                            "selected_offers": [fresh_id],
                            "payments": [{"type": "balance", "currency": fresh_currency, "amount": fresh_amount}],
                            "passengers": passengers_payload_retry,
                        }
                    }
                    retry_resp = await create_order_retry(order_payload=retry_payload)
                    order_id = (retry_resp.get("data") or {}).get("id")
                    _append_checkpoint(state, node="confirm_create_order_retry_success", details={"order_id": order_id, "fresh_offer_id": fresh_id})
                    return {
                        "duffel_order_id": order_id,
                        "applied_option_id": fresh_id,
                        "checkpoint_events": state.get("checkpoint_events", []),
                    }
                except Exception:
                    _append_checkpoint(state, node="confirm_create_order_retry_failed")
                    return {
                        "can_apply": False,
                        "error_message": "Selected fare expired. Re-run agent for fresh options.",
                        "checkpoint_events": state.get("checkpoint_events", []),
                    }

            _append_checkpoint(state, node="confirm_create_order_error", details={"status_code": code})
            return {
                "can_apply": False,
                "error_message": "Booking provider failed. Please retry shortly.",
                "checkpoint_events": state.get("checkpoint_events", []),
            }

    # Stale offer path: do a fresh search and book the best match
    _append_checkpoint(state, node="confirm_stale_offer_retry")
    try:
        from agent.tools import search_alternatives
        from integrations.duffel_client import create_order as create_order_fresh

        trip_context = state.get("trip_context") or {}
        search = await search_alternatives(trip_context=trip_context, simulate_disruption=None)
        orq = (search.get("orq") or {}).get("data") or {}
        fresh_offers = orq.get("offers") or []
        fresh_passengers = orq.get("passengers") or []
        if not fresh_offers:
            return {
                "can_apply": False,
                "error_message": "Original fare expired and no fresh alternatives found. Try again later.",
                "checkpoint_events": state.get("checkpoint_events", []),
            }

        fresh = sorted(fresh_offers, key=lambda o: _score_offer(o))[0]
        fresh_id = str(fresh.get("id") or "")
        fresh_amount = str(fresh.get("total_amount") or "0")
        fresh_currency = str(fresh.get("total_currency") or "USD")

        pax_payload: list[dict[str, Any]] = []
        for i, dp in enumerate(fresh_passengers):
            pid = dp.get("id")
            base = passenger_details[i] if i < len(passenger_details) else (passenger_details[-1] if passenger_details else {})
            pax_payload.append(_clean_passenger_payload(base=base, pid=pid))

        fresh_order_payload = {
            "data": {
                "selected_offers": [fresh_id],
                "payments": [{"type": "balance", "currency": fresh_currency, "amount": fresh_amount}],
                "passengers": pax_payload,
            }
        }
        resp = await create_order_fresh(order_payload=fresh_order_payload)
        order_id = (resp.get("data") or {}).get("id")
        _append_checkpoint(state, node="confirm_stale_retry_success", details={"order_id": order_id, "fresh_offer_id": fresh_id})
        return {
            "duffel_order_id": order_id,
            "applied_option_id": fresh_id,
            "checkpoint_events": state.get("checkpoint_events", []),
        }
    except Exception:
        _append_checkpoint(state, node="confirm_stale_retry_failed")
        return {
            "can_apply": False,
            "error_message": "Original fare expired. Fresh search also failed. Please re-run the agent.",
            "checkpoint_events": state.get("checkpoint_events", []),
        }


async def run_confirm_graph(
    *,
    booking_mode: str,
    trip_context: dict[str, Any],
    selected_option_id: str,
    options_by_offer_id: dict[str, dict[str, Any]],
    requires_user_review: bool,
    acknowledged_uncertainty: bool,
    duffel_passengers: list[dict[str, Any]],
    passenger_details: list[dict[str, Any]],
) -> AgentConfirmGraphState:
    """Confirm/apply pre-booking graph."""
    state: AgentConfirmGraphState = {
        "booking_mode": booking_mode,
        "trip_context": trip_context,
        "selected_option_id": selected_option_id,
        "options_by_offer_id": options_by_offer_id,
        "requires_user_review": requires_user_review,
        "acknowledged_uncertainty": acknowledged_uncertainty,
        "duffel_passengers": duffel_passengers,
        "passenger_details": passenger_details,
        "checkpoint_events": [],
        "can_apply": True,
    }
    try:
        from langgraph.graph import END, START, StateGraph  # type: ignore

        graph = StateGraph(AgentConfirmGraphState)
        graph.add_node("precheck", _confirm_precheck)
        graph.add_node("verify_offer", _confirm_verify_offer)
        graph.add_node("create_order", _confirm_create_order)
        graph.add_edge(START, "precheck")
        graph.add_edge("precheck", "verify_offer")
        graph.add_edge("verify_offer", "create_order")
        graph.add_edge("create_order", END)
        app = graph.compile()
        return await app.ainvoke(state)
    except Exception:
        state.update(_confirm_precheck(state))
        state.update(await _confirm_verify_offer(state))
        state.update(await _confirm_create_order(state))
        return state

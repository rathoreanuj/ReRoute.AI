"""Chat service — LangChain + OpenAI powered conversational trip builder.

Orchestrates:
  1. NER extraction via OpenAI function calling
  2. Entity accumulation across conversation turns
  3. Trip creation from collected entities
  4. Agent propose/confirm triggering from chat
  5. Quick-reply chip generation per phase
  6. Entity editing + Use My Info
  7. Inline booking confirmation
  8. Airport disambiguation + IATA validation
  9. Amendment handling ("change origin to EWR")
  10. Re-run agent + simulate disruption variants
  11. Cascade impact + compensation in chat
  12. Proactive weather briefing offer
  13. Returning user greeting
  14. Add-another-passenger flow
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from dao.chat_dao import ChatDAO
from integrations.location_resolver import IATA_TO_CITY
from model.chat_session_model import ChatSession
from model.user_model import User
from schema.chat_schemas import (
    ChatActionResponse,
    ChatHistoryResponse,
    ChatMessagePublic,
    ChatReply,
    ChatSessionPublic,
    QuickReplyChip,
)
from schema.trip_schemas import TripCreateRequest
from service import agent_service, trip_service

logger = logging.getLogger(__name__)

# ── IATA validation helpers ───────────────────────────────────

KNOWN_IATA_CODES = set(IATA_TO_CITY.keys())

# City name → possible IATA codes for disambiguation
CITY_ALIASES: dict[str, list[str]] = {
    "new york": ["JFK", "EWR", "LGA"],
    "nyc": ["JFK", "EWR", "LGA"],
    "chicago": ["ORD", "MDW"],
    "los angeles": ["LAX"],
    "la": ["LAX"],
    "london": ["LHR", "LGW", "STN"],
    "paris": ["CDG", "ORY"],
    "tokyo": ["NRT", "HND"],
    "washington": ["IAD", "DCA"],
    "dc": ["IAD", "DCA"],
    "san francisco": ["SFO", "OAK", "SJC"],
    "miami": ["MIA", "FLL"],
    "houston": ["IAH", "HOU"],
    "dallas": ["DFW", "DAL"],
}


def _validate_iata(code: str) -> bool:
    """Check if a 3-letter IATA code is known."""
    return bool(code) and len(code) == 3 and code.upper() in KNOWN_IATA_CODES


def _check_disambiguation(entities: dict[str, Any]) -> str | None:
    """Check if origin/destination need airport disambiguation. Returns prompt text or None."""
    for field in ("origin", "destination"):
        val = (entities.get(field) or "").lower().strip()
        if not val:
            continue
        # Check if it's a city alias needing disambiguation
        for city, airports in CITY_ALIASES.items():
            if val == city or val in city.split():
                if len(airports) > 1:
                    label = "origin" if field == "origin" else "destination"
                    options = ", ".join(f"**{a}** ({IATA_TO_CITY.get(a, a)})" for a in airports)
                    return f"Which {label} airport? {options}"
        # Check if it's a valid IATA code
        if len(val) == 3 and val.upper() not in KNOWN_IATA_CODES:
            label = "origin" if field == "origin" else "destination"
            return f"I don't recognize **{val.upper()}** as an airport code. Could you double-check the {label} airport IATA code (3 letters)?"
    return None

# ── Required fields for a complete trip snapshot ──────────────

REQUIRED_FIELDS = [
    "flight_number",
    "origin",
    "destination",
    "travel_date",
    "passengers",
]

# ── OpenAI function schema for NER extraction ────────────────

EXTRACT_ENTITIES_FUNCTION = {
    "name": "extract_trip_entities",
    "description": (
        "Extract travel-related entities from the user's message. "
        "Only include fields that the user explicitly mentioned. "
        "Do NOT guess or fabricate values."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "flight_number": {
                "type": "string",
                "description": "Airline flight number (e.g. 'AA2117', 'DL891', 'UA512')",
            },
            "origin": {
                "type": "string",
                "description": "Origin airport IATA code (3 letters, e.g. 'JFK', 'LAX')",
            },
            "destination": {
                "type": "string",
                "description": "Destination airport IATA code (3 letters, e.g. 'ATL', 'SFO')",
            },
            "travel_date": {
                "type": "string",
                "description": "Travel date in YYYY-MM-DD format",
            },
            "cabin_class": {
                "type": "string",
                "enum": ["economy", "premium_economy", "business", "first"],
                "description": "Cabin class preference",
            },
            "budget_band": {
                "type": "string",
                "enum": ["low", "mid", "high", "flexible"],
                "description": "Budget preference band",
            },
            "scheduled_departure_time": {
                "type": "string",
                "description": "Scheduled departure time in HH:MM format (24hr local)",
            },
            "scheduled_arrival_time": {
                "type": "string",
                "description": "Scheduled arrival time in HH:MM format (24hr local)",
            },
            "passengers": {
                "type": "array",
                "description": "List of passenger details extracted from user message",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "enum": ["mr", "ms", "mrs", "dr"]},
                        "given_name": {"type": "string"},
                        "family_name": {"type": "string"},
                        "gender": {"type": "string", "enum": ["m", "f", "x"]},
                        "born_on": {"type": "string", "description": "Date of birth YYYY-MM-DD"},
                        "phone_number": {"type": "string", "description": "Phone in E.164 format (e.g. +15551234567)"},
                    },
                },
            },
            "connection_buffer_minutes": {"type": "integer", "description": "Minutes buffer for connecting flights"},
            "hotel_checkin_buffer_minutes": {"type": "integer", "description": "Minutes buffer for hotel check-in after arrival"},
            "meeting_time": {"type": "string", "description": "Meeting/appointment time in ISO 8601 UTC"},
        },
        "additionalProperties": False,
    },
}

# ── System prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are ReRoute AI, a friendly and efficient travel assistant chatbot.

Your job is to conversationally collect trip information from the user so we can monitor their flight for disruptions and automatically find rebooking options.

PERSONALITY:
- Warm, professional, concise
- Use plain language, not jargon
- Confirm what you understood after each message
- Ask for missing information naturally (don't list all fields at once)
- Show excitement about helping them travel smoothly

INFORMATION TO COLLECT (in rough order of importance):
1. Flight number (e.g. AA2117)
2. Origin & destination airports (IATA codes like JFK, LAX)
3. Travel date
4. Passenger details: full name, date of birth, phone number for EACH traveler
5. Cabin class preference (economy/business/first) — default to economy if not mentioned
6. Budget preference — default to mid if not mentioned

OPTIONAL INFO (ask only if relevant):
- Departure/arrival times
- Connecting flight buffer time
- Hotel check-in timing
- Meeting/appointment times at destination

RULES:
- ALWAYS call the extract_trip_entities function to extract entities from each user message
- When you have all required info, summarize it in a clear card format and ask for confirmation
- After confirmation, tell the user you're saving the trip and offer to run the disruption agent
- Be smart about inferring: "NYC to LA" → JFK to LAX, "tomorrow" → calculate the date
- If the user gives multiple passengers at once, capture them all
- Phone numbers should be in E.164 format (+1XXXXXXXXXX for US)
- Dates should be YYYY-MM-DD format internally

CURRENT ENTITIES COLLECTED SO FAR:
{entities_json}

MISSING REQUIRED FIELDS:
{missing_fields}
"""


# ── Helpers ───────────────────────────────────────────────────

def _get_llm():
    """Lazy-load the OpenAI LLM to avoid import errors when langchain isn't installed."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise RuntimeError(
            "langchain-openai is required for chat. Install with: pip install -e '.[agent]'"
        )

    settings = get_settings()
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment/config")

    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.3,
        api_key=api_key,
    )


def _compute_missing_fields(entities: dict[str, Any]) -> list[str]:
    """Return list of required fields still missing."""
    missing = []
    if not entities.get("flight_number"):
        missing.append("flight_number")
    if not entities.get("origin"):
        missing.append("origin")
    if not entities.get("destination"):
        missing.append("destination")
    if not entities.get("travel_date"):
        missing.append("travel_date")

    passengers = entities.get("passengers") or []
    if not passengers:
        missing.append("passengers")
    else:
        for i, p in enumerate(passengers):
            if not p.get("given_name") or not p.get("family_name"):
                missing.append(f"passenger_{i+1}_name")
            if not p.get("born_on"):
                missing.append(f"passenger_{i+1}_dob")
            if not p.get("phone_number"):
                missing.append(f"passenger_{i+1}_phone")

    return missing


def _merge_entities(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge newly extracted entities into the accumulated state."""
    merged = copy.deepcopy(existing)

    scalar_fields = [
        "flight_number", "origin", "destination", "travel_date",
        "cabin_class", "budget_band", "scheduled_departure_time",
        "scheduled_arrival_time", "connection_buffer_minutes",
        "hotel_checkin_buffer_minutes", "meeting_time",
    ]
    for field in scalar_fields:
        if field in new and new[field]:
            merged[field] = new[field]

    new_passengers = new.get("passengers") or []
    existing_passengers = merged.get("passengers") or []

    for new_p in new_passengers:
        if not new_p.get("given_name"):
            continue
        matched = False
        for i, ex_p in enumerate(existing_passengers):
            if (
                ex_p.get("given_name", "").lower() == new_p.get("given_name", "").lower()
                and ex_p.get("family_name", "").lower() == new_p.get("family_name", "").lower()
            ):
                for k, v in new_p.items():
                    if v:
                        existing_passengers[i][k] = v
                matched = True
                break
        if not matched:
            existing_passengers.append(new_p)

    merged["passengers"] = existing_passengers
    return merged


def _build_snapshot_from_entities(entities: dict[str, Any], user_email: str, user_name: str | None) -> dict[str, Any]:
    """Convert accumulated chat entities into the trip snapshot format."""
    date = entities.get("travel_date", "")
    origin = (entities.get("origin") or "").upper()[:3]
    destination = (entities.get("destination") or "").upper()[:3]

    dep_time = entities.get("scheduled_departure_time")
    arr_time = entities.get("scheduled_arrival_time")
    dep_local = f"{date}T{dep_time}:00" if dep_time and date else None
    arr_local = f"{date}T{arr_time}:00" if arr_time and date else None

    passengers = []
    for p in (entities.get("passengers") or []):
        passengers.append({
            "title": p.get("title", "mr"),
            "given_name": p.get("given_name", "Traveler"),
            "family_name": p.get("family_name", ""),
            "gender": p.get("gender", "m"),
            "phone_number": p.get("phone_number", "+14155552671"),
            "email": user_email,
            "born_on": p.get("born_on", "1990-01-01"),
        })

    legs: dict[str, Any] = {
        "primary_flight": {
            "flight_number": entities.get("flight_number", ""),
            "date": date,
            "origin": origin,
            "destination": destination,
            "scheduled_departure_date": date,
        },
        "connection": {
            "departure_after_arrival_minutes": entities.get("connection_buffer_minutes", 90),
        },
        "hotel": {
            "check_in_buffer_minutes": entities.get("hotel_checkin_buffer_minutes", 60),
        },
        "meeting": {
            "scheduled_time_utc": entities.get("meeting_time", ""),
        },
    }
    if dep_local:
        legs["primary_flight"]["scheduled_departure_local"] = dep_local
    if arr_local:
        legs["primary_flight"]["scheduled_arrival_local"] = arr_local

    return {
        "user": {"email": user_email, "full_name": user_name or user_email},
        "passengers": passengers,
        "preferences": {
            "cabin_class": entities.get("cabin_class", "economy"),
            "budget_band": entities.get("budget_band", "mid"),
        },
        "legs": legs,
    }


def _session_to_public(s: ChatSession) -> ChatSessionPublic:
    return ChatSessionPublic(
        id=s.id,
        phase=s.phase,
        entities=s.entities or {},
        trip_id=s.trip_id,
        proposal_id=getattr(s, "_proposal_id", None),
        created_at=s.created_at,
    )


def _message_to_public(m, card_type: str | None = None, card_data: dict | None = None) -> ChatMessagePublic:
    return ChatMessagePublic(
        id=m.id,
        role=m.role,
        content=m.content,
        extracted_entities=m.extracted_entities,
        card_type=card_type,
        card_data=card_data,
        created_at=m.created_at,
    )


# ── Quick Reply Chip Generation ──────────────────────────────

def _compute_quick_replies(
    phase: str,
    entities: dict[str, Any],
    missing: list[str],
    ready_to_save: bool,
    has_proposal: bool = False,
) -> list[QuickReplyChip]:
    """Generate context-aware quick-reply chips based on current state."""
    chips: list[QuickReplyChip] = []

    if phase == "collecting":
        if not entities:
            # Fresh session — starter chips
            chips.append(QuickReplyChip(label="I have a flight to add", value="I have a flight I'd like to add", icon="plane"))
            chips.append(QuickReplyChip(label="Use my info", value="__USE_MY_INFO__", icon="user-check"))
            return chips

        if ready_to_save:
            # All fields collected — confirmation chips
            chips.append(QuickReplyChip(label="Looks good, save it!", value="Yes, save it!", icon="check"))
            chips.append(QuickReplyChip(label="Edit details", value="__EDIT_ENTITIES__", icon="pencil"))
            return chips

        # Mid-collection — suggest what's missing
        if "flight_number" in missing:
            chips.append(QuickReplyChip(label="Enter flight number", value="My flight number is ", icon="plane"))
        if "origin" in missing or "destination" in missing:
            chips.append(QuickReplyChip(label="Enter route", value="I'm flying from ", icon="map-pin"))
        if "travel_date" in missing:
            chips.append(QuickReplyChip(label="Enter travel date", value="I'm traveling on ", icon="calendar"))
        if "passengers" in missing:
            chips.append(QuickReplyChip(label="Use my info as passenger", value="__USE_MY_INFO__", icon="user-check"))
            chips.append(QuickReplyChip(label="Enter passenger details", value="The passenger is ", icon="users"))
        # Per-passenger missing fields
        pax_missing_dob = any(f.endswith("_dob") for f in missing)
        pax_missing_phone = any(f.endswith("_phone") for f in missing)
        if pax_missing_dob:
            chips.append(QuickReplyChip(label="Add date of birth", value="Date of birth is ", icon="calendar"))
        if pax_missing_phone:
            chips.append(QuickReplyChip(label="Add phone number", value="Phone number is ", icon="phone"))

        # Cabin class if not set
        if not entities.get("cabin_class"):
            chips.append(QuickReplyChip(label="Economy", value="Economy class", icon="armchair"))
            chips.append(QuickReplyChip(label="Business", value="Business class", icon="briefcase"))

        # Add another passenger (only if at least one exists)
        if entities.get("passengers") and "passengers" not in missing:
            chips.append(QuickReplyChip(label="Add another passenger", value="Add another passenger", icon="users"))

        return chips[:6]  # Cap at 6 chips

    if phase == "trip_created":
        chips.append(QuickReplyChip(label="Run disruption agent", value="Run the agent and check my flight", icon="radar"))
        chips.append(QuickReplyChip(label="Simulate cancel", value="Simulate a cancellation for testing", icon="alert-triangle"))
        chips.append(QuickReplyChip(label="Simulate delay", value="Simulate a delay for testing", icon="alert-triangle"))
        chips.append(QuickReplyChip(label="Weather briefing", value="What's the weather at my destination?", icon="cloud"))
        chips.append(QuickReplyChip(label="View trip", value="__VIEW_TRIP__", icon="external-link"))
        return chips

    if phase == "agent_running":
        return []  # No chips while agent is running

    if phase == "agent_complete" or (phase == "done" and has_proposal):
        chips.append(QuickReplyChip(label="Book option 1", value="Confirm option 1", icon="check-circle"))
        chips.append(QuickReplyChip(label="Book option 2", value="Confirm option 2", icon="check-circle"))
        chips.append(QuickReplyChip(label="Book option 3", value="Confirm option 3", icon="check-circle"))
        chips.append(QuickReplyChip(label="Check again", value="Check again for new options", icon="radar"))
        chips.append(QuickReplyChip(label="Weather", value="What's the weather at my destination?", icon="cloud"))
        chips.append(QuickReplyChip(label="View trip", value="__VIEW_TRIP__", icon="external-link"))
        return chips

    if phase == "done":
        chips.append(QuickReplyChip(label="Start new trip", value="__NEW_SESSION__", icon="plus"))
        chips.append(QuickReplyChip(label="View trip", value="__VIEW_TRIP__", icon="external-link"))
        return chips

    return chips


# ── Amendment Detection ───────────────────────────────────────

AMENDMENT_PATTERNS = [
    (r"(?:change|update|switch|modify)\s+(?:the\s+)?origin\s+(?:to\s+)?(\w{3})", "origin"),
    (r"(?:change|update|switch|modify)\s+(?:the\s+)?destination\s+(?:to\s+)?(\w{3})", "destination"),
    (r"(?:change|update|switch|modify)\s+(?:the\s+)?flight\s*(?:number)?\s+(?:to\s+)?(\w+\d+)", "flight_number"),
    (r"(?:change|update|switch|modify)\s+(?:the\s+)?date\s+(?:to\s+)?(\d{4}-\d{2}-\d{2})", "travel_date"),
    (r"(?:actually|no|wait),?\s+(?:it'?s|make it|the origin is)\s+(\w{3})", "origin"),
    (r"(?:actually|no|wait),?\s+(?:it'?s|make it|the destination is)\s+(\w{3})", "destination"),
    (r"(?:actually|no|wait),?\s+(?:it'?s|make it|the flight is)\s+(\w+\d+)", "flight_number"),
]


def _detect_amendment(message: str) -> dict[str, str] | None:
    """Detect explicit entity amendments in the message. Returns {field: new_value} or None."""
    msg = message.lower().strip()
    amendments = {}
    for pattern, field in AMENDMENT_PATTERNS:
        m = re.search(pattern, msg, re.IGNORECASE)
        if m:
            val = m.group(1).upper() if field in ("origin", "destination") else m.group(1)
            amendments[field] = val
    return amendments if amendments else None


# ── Cascade & Compensation Formatting ─────────────────────────

def _format_cascade_for_chat(cascade_preview: dict | None) -> str:
    """Format cascade preview into readable chat text."""
    if not cascade_preview:
        return ""
    lines = []
    if cascade_preview.get("disruption_type"):
        lines.append(f"**Disruption:** {cascade_preview['disruption_type']}")
    if cascade_preview.get("connection_risk"):
        lines.append(f"**Connection:** {cascade_preview['connection_risk']}")
    if cascade_preview.get("hotel_impact"):
        lines.append(f"**Hotel:** {cascade_preview['hotel_impact']}")
    if cascade_preview.get("meeting_impact"):
        lines.append(f"**Meeting:** {cascade_preview['meeting_impact']}")
    changes = cascade_preview.get("what_changed")
    if isinstance(changes, list):
        for c in changes[:3]:
            lines.append(f"- {c}")
    return "\n".join(lines)


def _format_compensation_for_chat(comp_draft: dict | None) -> str:
    """Format compensation draft into readable chat text."""
    if not comp_draft:
        return ""
    lines = []
    if comp_draft.get("eligible"):
        lines.append("**You may be eligible for compensation:**")
        if comp_draft.get("regulation"):
            lines.append(f"- Regulation: {comp_draft['regulation']}")
        if comp_draft.get("amount"):
            lines.append(f"- Estimated amount: {comp_draft['amount']}")
        if comp_draft.get("reason"):
            lines.append(f"- Reason: {comp_draft['reason']}")
    return "\n".join(lines)


# ── Weather Briefing ──────────────────────────────────────────

async def _get_weather_briefing(trip_id: str, entities: dict[str, Any]) -> str | None:
    """Try to fetch a quick weather summary for the destination."""
    try:
        from integrations.weather import fetch_openmeteo_hourly
        from integrations.location_resolver import resolve_coords

        dest = entities.get("destination", "")
        if not dest:
            return None

        coords = await resolve_coords(dest)
        if not coords:
            return None

        lat, lon = coords
        data = await fetch_openmeteo_hourly(lat, lon)
        if not data or "hourly" not in data:
            return None

        hourly = data["hourly"]
        temps = hourly.get("temperature_2m", [])
        precips = hourly.get("precipitation_probability", [])
        winds = hourly.get("wind_speed_10m", [])

        if not temps:
            return None

        avg_temp = sum(temps[:24]) / min(len(temps), 24)
        max_precip = max(precips[:24]) if precips else 0
        avg_wind = sum(winds[:24]) / min(len(winds), 24) if winds else 0

        dest_city = IATA_TO_CITY.get(dest.upper(), dest)
        lines = [f"**Weather at {dest_city}:**"]
        lines.append(f"- Temperature: ~{avg_temp:.0f}°C")
        if max_precip > 30:
            lines.append(f"- Rain chance: up to {max_precip}%")
        if avg_wind > 30:
            lines.append(f"- Wind: ~{avg_wind:.0f} km/h (gusty)")
        else:
            lines.append(f"- Wind: ~{avg_wind:.0f} km/h")

        return "\n".join(lines)
    except Exception:
        logger.debug("weather_briefing_failed", exc_info=True)
        return None


# ── Returning User Greeting ───────────────────────────────────

async def _build_greeting(user: User, dao: ChatDAO, session: AsyncSession) -> str | None:
    """Build a personalized greeting for returning users."""
    from dao.trip_dao import TripDAO
    trip_dao = TripDAO(session)
    try:
        trips = await trip_dao.list_for_user(user_id=user.id, limit=3)
    except Exception:
        return None

    name = (user.full_name or "").split()[0] if user.full_name else None
    greeting = f"Welcome back{', ' + name if name else ''}!"

    if trips:
        latest = trips[0]
        snap = latest.snapshot or {}
        legs = snap.get("legs", {})
        pf = legs.get("primary_flight", {})
        origin = pf.get("origin", "")
        dest = pf.get("destination", "")
        if origin and dest:
            greeting += f" Your latest trip is {origin} → {dest}."

    greeting += " Tell me about a new flight, or say **\"check my flight\"** if you want to monitor an existing one."
    return greeting


# ── LLM Extraction ───────────────────────────────────────────

async def _run_llm_extraction(
    messages_history: list[dict[str, str]],
    user_message: str,
    current_entities: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Call OpenAI with function calling to extract entities and generate reply."""
    try:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    except ImportError:
        raise RuntimeError("langchain-openai required. Install with: pip install -e '.[agent]'")

    llm = _get_llm()

    missing = _compute_missing_fields(current_entities)
    system_text = SYSTEM_PROMPT.format(
        entities_json=json.dumps(current_entities, indent=2, default=str),
        missing_fields=", ".join(missing) if missing else "None — all required fields collected!",
    )

    lc_messages = [SystemMessage(content=system_text)]
    for m in messages_history[-20:]:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))

    lc_messages.append(HumanMessage(content=user_message))

    llm_with_tools = llm.bind_tools(
        [EXTRACT_ENTITIES_FUNCTION],
        tool_choice={"type": "function", "function": {"name": "extract_trip_entities"}},
    )

    extraction_response = await llm_with_tools.ainvoke(lc_messages)

    extracted = {}
    if extraction_response.tool_calls:
        for tc in extraction_response.tool_calls:
            if tc["name"] == "extract_trip_entities":
                extracted = tc["args"]
                break

    merged = _merge_entities(current_entities, extracted)
    missing_after = _compute_missing_fields(merged)

    reply_system = SYSTEM_PROMPT.format(
        entities_json=json.dumps(merged, indent=2, default=str),
        missing_fields=", ".join(missing_after) if missing_after else "None — all required fields collected!",
    )
    reply_messages = [SystemMessage(content=reply_system)]
    for m in messages_history[-20:]:
        if m["role"] == "user":
            reply_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            reply_messages.append(AIMessage(content=m["content"]))
    reply_messages.append(HumanMessage(content=user_message))

    if not missing_after:
        reply_messages.append(SystemMessage(
            content=(
                "All required information has been collected! "
                "Summarize ALL the collected trip details in a nice formatted summary and "
                "ask the user to confirm so you can save the trip. "
                "Use a clear layout showing flight, date, route, passengers, and preferences."
            )
        ))

    reply_response = await llm.ainvoke(reply_messages)
    reply_text = reply_response.content if hasattr(reply_response, "content") else str(reply_response)

    return reply_text, extracted


# ── Public API ────────────────────────────────────────────────

async def handle_message(
    *,
    session: AsyncSession,
    user: User,
    message: str,
    session_id: str | None = None,
) -> ChatReply:
    """Process a user chat message: extract entities, generate reply, persist."""
    user_id = user.id
    user_email = user.email
    user_name = user.full_name
    dao = ChatDAO(session)

    if session_id:
        chat_session = await dao.get_session(session_id, user_id)
        if not chat_session:
            chat_session = await dao.get_or_create_active_session(user_id)
    else:
        chat_session = await dao.get_or_create_active_session(user_id)

    history_rows = await dao.list_messages(chat_session.id)
    messages_history = [{"role": m.role, "content": m.content} for m in history_rows]

    await dao.add_message(chat_session.id, "user", message)

    current_entities = chat_session.entities or {}
    msg_lower = message.lower().strip()

    # ── Check for inline booking confirmation ("confirm option 1/2/3") ──
    confirm_option_match = _parse_confirm_option(message)
    if confirm_option_match and chat_session.phase in ("done", "agent_complete"):
        return await _handle_inline_booking(
            session=session,
            dao=dao,
            chat_session=chat_session,
            user_id=user_id,
            option_index=confirm_option_match,
            current_entities=current_entities,
        )

    # ── Check for "add another passenger" intent ──
    add_pax_phrases = ["add another passenger", "add a passenger", "another traveler", "add traveler"]
    if chat_session.phase == "collecting" and any(p in msg_lower for p in add_pax_phrases):
        pax = current_entities.get("passengers") or []
        pax.append({"title": "mr", "given_name": "", "family_name": "", "gender": "m", "born_on": "", "phone_number": ""})
        current_entities["passengers"] = pax
        await dao.update_session(chat_session.id, entities=current_entities)
        reply_msg = await dao.add_message(
            chat_session.id, "assistant",
            f"Added passenger {len(pax)}. What's their full name?",
        )
        await session.commit()
        missing = _compute_missing_fields(current_entities)
        chips = _compute_quick_replies("collecting", current_entities, missing, False)
        return ChatReply(
            session=_session_to_public(chat_session), reply=_message_to_public(reply_msg),
            entities=current_entities, missing_fields=missing, ready_to_save=False, quick_replies=chips,
        )

    # ── Check for explicit entity amendments ("change origin to EWR") ──
    if chat_session.phase == "collecting":
        amendments = _detect_amendment(message)
        if amendments:
            for field, val in amendments.items():
                current_entities[field] = val
            await dao.update_session(chat_session.id, entities=current_entities)
            changed = ", ".join(f"{k} → **{v}**" for k, v in amendments.items())
            reply_msg = await dao.add_message(
                chat_session.id, "assistant",
                f"Updated: {changed}",
            )
            await session.commit()
            missing = _compute_missing_fields(current_entities)
            ready = len(missing) == 0
            chips = _compute_quick_replies("collecting", current_entities, missing, ready)
            return ChatReply(
                session=_session_to_public(chat_session), reply=_message_to_public(reply_msg, card_type="entity_summary" if ready else None, card_data={"entities": current_entities, "editable": True} if ready else None),
                entities=current_entities, missing_fields=missing, ready_to_save=ready, quick_replies=chips,
            )

    # ── Check for weather briefing request ──
    weather_phrases = ["weather", "forecast", "what's the weather", "weather briefing"]
    if chat_session.phase in ("trip_created", "agent_complete", "done") and any(p in msg_lower for p in weather_phrases):
        briefing = await _get_weather_briefing(chat_session.trip_id or "", current_entities)
        text = briefing or "Weather data is not available for this destination right now."
        reply_msg = await dao.add_message(chat_session.id, "assistant", text)
        await session.commit()
        chips = _compute_quick_replies(chat_session.phase, current_entities, [], False, has_proposal=bool(current_entities.get("_proposal_id")))
        return ChatReply(
            session=_session_to_public(chat_session), reply=_message_to_public(reply_msg),
            entities=current_entities, missing_fields=[], ready_to_save=False, quick_replies=chips,
        )

    # ── Check for re-run agent ("check again", "run again", "re-scan") ──
    rerun_phrases = ["check again", "run again", "re-scan", "rescan", "scan again", "recheck"]
    if chat_session.phase in ("agent_complete", "done") and chat_session.trip_id and any(p in msg_lower for p in rerun_phrases):
        # Reset to trip_created so agent can run again
        await dao.update_session(chat_session.id, phase="trip_created")
        chat_session.phase = "trip_created"
        return await _handle_agent_run(
            session=session, dao=dao, chat_session=chat_session, user_id=user_id,
            current_entities=current_entities, simulate_disruption=None,
        )

    # ── Check for trip confirmation phrases ──
    confirm_phrases = ["yes", "confirm", "looks good", "that's correct", "save it", "go ahead", "perfect", "yep", "yeah", "correct", "save"]
    is_confirmation = (
        chat_session.phase == "collecting"
        and not _compute_missing_fields(current_entities)
        and any(p in msg_lower for p in confirm_phrases)
    )

    if is_confirmation:
        snapshot = _build_snapshot_from_entities(current_entities, user_email, user_name)
        flight_num = current_entities.get("flight_number", "")
        origin = current_entities.get("origin", "")
        destination = current_entities.get("destination", "")
        title = f"{flight_num} {origin}-{destination}".strip() or "Chat Trip"

        payload = TripCreateRequest(title=title, snapshot=snapshot)
        trip = await trip_service.create_trip(user=user, payload=payload, session=session)
        await dao.update_session(chat_session.id, phase="trip_created", trip_id=trip.id)
        chat_session.phase = "trip_created"
        chat_session.trip_id = trip.id

        # Proactive weather offer
        dest_city = IATA_TO_CITY.get(destination.upper(), destination)
        reply_text = (
            f"Your trip has been saved! **{title}** is now being tracked.\n\n"
            f"I can:\n"
            f"- **Run the disruption agent** to check flight status & find alternatives\n"
            f"- **Get a weather briefing** for {dest_city}\n"
            f"- **Simulate a disruption** (cancellation/delay) to test the system"
        )
        reply_msg = await dao.add_message(chat_session.id, "assistant", reply_text)
        await session.commit()

        chips = _compute_quick_replies("trip_created", current_entities, [], False)
        return ChatReply(
            session=_session_to_public(chat_session),
            reply=_message_to_public(reply_msg),
            entities=current_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )

    # ── Check for agent trigger phrases ──
    agent_phrases = ["run agent", "check my flight", "scan", "monitor", "check for disruptions", "run the agent", "find alternatives", "simulate"]
    is_agent_trigger = (
        chat_session.phase == "trip_created"
        and chat_session.trip_id
        and any(p in message.lower() for p in agent_phrases)
    )

    if is_agent_trigger:
        simulate = None
        if "simulat" in msg_lower:
            if "delay" in msg_lower:
                simulate = "delay"
            elif "divert" in msg_lower:
                simulate = "divert"
            else:
                simulate = "cancel"
        return await _handle_agent_run(
            session=session,
            dao=dao,
            chat_session=chat_session,
            user_id=user_id,
            current_entities=current_entities,
            simulate_disruption=simulate,
        )

    # ── Normal message — run LLM extraction ──
    try:
        reply_text, extracted = await _run_llm_extraction(
            messages_history, message, current_entities
        )
    except Exception as e:
        logger.exception("chat_llm_failed")
        reply_text = (
            "I'm having trouble processing that right now. "
            "Could you try rephrasing? Tell me your flight number, "
            "where you're flying from and to, and the travel date."
        )
        extracted = {}

    merged = _merge_entities(current_entities, extracted)

    # Airport disambiguation check
    disambiguation = _check_disambiguation(merged)
    if disambiguation:
        reply_text = disambiguation

    missing = _compute_missing_fields(merged)

    await dao.update_session(chat_session.id, entities=merged)
    chat_session.entities = merged

    # Build card data if ready to save
    card_type = None
    card_data = None
    if not missing:
        card_type = "entity_summary"
        card_data = {"entities": merged, "editable": True}

    reply_msg = await dao.add_message(
        chat_session.id, "assistant", reply_text,
        extracted_entities=extracted if extracted else None,
    )

    await session.commit()

    ready = len(missing) == 0
    chips = _compute_quick_replies("collecting", merged, missing, ready)
    return ChatReply(
        session=_session_to_public(chat_session),
        reply=_message_to_public(reply_msg, card_type=card_type, card_data=card_data),
        entities=merged,
        missing_fields=missing,
        ready_to_save=ready,
        quick_replies=chips,
    )


def _parse_confirm_option(message: str) -> int | None:
    """Parse 'confirm option 1', 'book option 2', 'go with 3' etc. Returns 1-indexed option or None."""
    import re
    msg = message.lower().strip()
    patterns = [
        r"(?:confirm|book|select|choose|go with)\s*(?:option\s*)?(\d)",
        r"option\s*(\d)",
        r"^(\d)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return int(m.group(1))
    return None


async def _handle_agent_run(
    *,
    session: AsyncSession,
    dao: ChatDAO,
    chat_session: ChatSession,
    user_id: str,
    current_entities: dict[str, Any],
    simulate_disruption: str | None,
) -> ChatReply:
    """Run the agent and return results with option cards."""
    await dao.update_session(chat_session.id, phase="agent_running")
    chat_session.phase = "agent_running"

    # Progress message
    progress_msg = await dao.add_message(
        chat_session.id, "assistant",
        "Running the disruption agent now...",
    )
    await session.commit()

    try:
        propose_result = await agent_service.propose_for_trip(
            session=session,
            user_id=user_id,
            trip_id=chat_session.trip_id,
            simulate_disruption=simulate_disruption,
        )

        # Build option cards data
        options_data = []
        for i, opt in enumerate(propose_result.ranked_options[:3], 1):
            legs_info = []
            for leg in (opt.legs or []):
                if isinstance(leg, dict):
                    legs_info.append({
                        "origin": leg.get("origin", {}).get("iata_code", ""),
                        "destination": leg.get("destination", {}).get("iata_code", ""),
                        "departing_at": leg.get("departing_at", ""),
                        "arriving_at": leg.get("arriving_at", ""),
                    })
            options_data.append({
                "index": i,
                "option_id": opt.option_id,
                "score": opt.score,
                "summary": opt.summary,
                "legs": legs_info,
                "modality": opt.modality or "flight",
            })

        # Store proposal_id in session entities for inline booking
        updated_entities = copy.deepcopy(current_entities)
        updated_entities["_proposal_id"] = propose_result.proposal_id
        updated_entities["_ranked_options"] = [
            {"index": o["index"], "option_id": o["option_id"], "summary": o["summary"]}
            for o in options_data
        ]
        await dao.update_session(
            chat_session.id,
            phase="agent_complete",
            entities=updated_entities,
        )

        options_text = ""
        for i, opt in enumerate(propose_result.ranked_options[:3], 1):
            options_text += f"\n**Option {i}:** {opt.summary}"

        # Cascade + compensation info
        cascade_text = _format_cascade_for_chat(propose_result.cascade_preview)
        compensation_text = _format_compensation_for_chat(propose_result.compensation_draft)

        result_text = f"**Agent scan complete!**\n\n**Status:** {propose_result.disruption_summary}\n{options_text}"
        if cascade_text:
            result_text += f"\n\n**Impact Analysis:**\n{cascade_text}"
        if compensation_text:
            result_text += f"\n\n{compensation_text}"
        result_text += (
            f"\n\nSay **\"confirm option 1\"** (or 2, 3) to book, or view details in your "
            f"[trip workspace](/trips/{chat_session.trip_id})."
        )

        result_msg = await dao.add_message(chat_session.id, "assistant", result_text)
        await session.commit()

        chips = _compute_quick_replies("agent_complete", updated_entities, [], False, has_proposal=True)
        return ChatReply(
            session=ChatSessionPublic(
                id=chat_session.id,
                phase="agent_complete",
                entities=updated_entities,
                trip_id=chat_session.trip_id,
                proposal_id=propose_result.proposal_id,
                created_at=chat_session.created_at,
            ),
            reply=ChatMessagePublic(
                id=result_msg.id,
                role="assistant",
                content=result_text,
                extracted_entities=None,
                card_type="options",
                card_data={
                    "proposal_id": propose_result.proposal_id,
                    "disruption_summary": propose_result.disruption_summary,
                    "options": options_data,
                    "cascade_preview": propose_result.cascade_preview,
                    "compensation_draft": propose_result.compensation_draft,
                },
                created_at=datetime.now(timezone.utc),
            ),
            entities=updated_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )
    except Exception as e:
        logger.exception("chat_agent_propose_failed")
        error_text = (
            f"I encountered an issue running the agent: {str(e)[:200]}\n\n"
            f"You can still check your trip at [trip workspace](/trips/{chat_session.trip_id})."
        )
        error_msg = await dao.add_message(chat_session.id, "assistant", error_text)
        await dao.update_session(chat_session.id, phase="trip_created")
        await session.commit()

        chips = _compute_quick_replies("trip_created", current_entities, [], False)
        return ChatReply(
            session=_session_to_public(chat_session),
            reply=_message_to_public(error_msg),
            entities=current_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )


async def _handle_inline_booking(
    *,
    session: AsyncSession,
    dao: ChatDAO,
    chat_session: ChatSession,
    user_id: str,
    option_index: int,
    current_entities: dict[str, Any],
) -> ChatReply:
    """Handle inline booking confirmation from chat."""
    from schema.agent_schemas import AgentConfirmRequest

    proposal_id = current_entities.get("_proposal_id")
    ranked_options = current_entities.get("_ranked_options") or []

    if not proposal_id:
        error_msg = await dao.add_message(
            chat_session.id, "assistant",
            "No active proposal found. Please run the agent first.",
        )
        await session.commit()
        chips = _compute_quick_replies(chat_session.phase, current_entities, [], False)
        return ChatReply(
            session=_session_to_public(chat_session),
            reply=_message_to_public(error_msg),
            entities=current_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )

    # Find the option by index
    selected = None
    for opt in ranked_options:
        if opt.get("index") == option_index:
            selected = opt
            break

    if not selected:
        error_msg = await dao.add_message(
            chat_session.id, "assistant",
            f"Option {option_index} not found. Available options: {', '.join(str(o['index']) for o in ranked_options)}.",
        )
        await session.commit()
        chips = _compute_quick_replies(chat_session.phase, current_entities, [], False, has_proposal=True)
        return ChatReply(
            session=_session_to_public(chat_session),
            reply=_message_to_public(error_msg),
            entities=current_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )

    booking_msg = await dao.add_message(
        chat_session.id, "assistant",
        f"Confirming **Option {option_index}**: {selected.get('summary', '')}...",
    )
    await session.commit()

    try:
        body = AgentConfirmRequest(
            proposal_id=proposal_id,
            selected_option_id=selected["option_id"],
            acknowledge_disruption_uncertainty=True,
        )
        result = await agent_service.confirm_and_apply(
            session=session,
            user_id=user_id,
            body=body,
        )

        if result.applied:
            success_text = (
                f"**Booking confirmed!**\n\n"
                f"{result.message}\n\n"
                f"**Duffel Order:** `{result.duffel_order_id or 'N/A'}`\n"
                f"**Itinerary Revision:** {result.itinerary_revision}\n\n"
                f"View your updated trip at [trip workspace](/trips/{chat_session.trip_id})."
            )
            card_type = "booking_confirmed"
            card_data = {
                "duffel_order_id": result.duffel_order_id,
                "itinerary_revision": result.itinerary_revision,
                "option_summary": selected.get("summary", ""),
            }
        else:
            success_text = f"Could not complete booking: {result.message}"
            card_type = None
            card_data = None

        result_msg = await dao.add_message(chat_session.id, "assistant", success_text)
        await dao.update_session(chat_session.id, phase="done")
        await session.commit()

        chips = _compute_quick_replies("done", current_entities, [], False)
        return ChatReply(
            session=ChatSessionPublic(
                id=chat_session.id,
                phase="done",
                entities=current_entities,
                trip_id=chat_session.trip_id,
                proposal_id=proposal_id,
                created_at=chat_session.created_at,
            ),
            reply=ChatMessagePublic(
                id=result_msg.id,
                role="assistant",
                content=success_text,
                extracted_entities=None,
                card_type=card_type,
                card_data=card_data,
                created_at=datetime.now(timezone.utc),
            ),
            entities=current_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )
    except Exception as e:
        logger.exception("chat_inline_booking_failed")
        error_msg = await dao.add_message(
            chat_session.id, "assistant",
            f"Booking failed: {str(e)[:200]}. Try again or use the [trip workspace](/trips/{chat_session.trip_id}).",
        )
        await session.commit()
        chips = _compute_quick_replies(chat_session.phase, current_entities, [], False, has_proposal=True)
        return ChatReply(
            session=_session_to_public(chat_session),
            reply=_message_to_public(error_msg),
            entities=current_entities,
            missing_fields=[],
            ready_to_save=False,
            quick_replies=chips,
        )


async def update_entities(
    *,
    session: AsyncSession,
    user_id: str,
    session_id: str,
    entities: dict[str, Any],
) -> ChatReply:
    """Directly update entities from the editable entity card."""
    dao = ChatDAO(session)
    chat_session = await dao.get_session(session_id, user_id)
    if not chat_session:
        raise ValueError("Session not found")

    # Filter out internal keys from user edits
    clean = {k: v for k, v in entities.items() if not k.startswith("_")}
    # Preserve internal keys
    existing = chat_session.entities or {}
    for k, v in existing.items():
        if k.startswith("_"):
            clean[k] = v

    await dao.update_session(session_id, entities=clean)
    chat_session.entities = clean

    missing = _compute_missing_fields(clean)
    ready = len(missing) == 0

    reply_msg = await dao.add_message(
        session_id, "assistant",
        "Trip details updated! " + ("All required fields are filled. Ready to save?" if ready else f"Still need: {', '.join(missing).replace('_', ' ')}"),
    )
    await session.commit()

    chips = _compute_quick_replies("collecting", clean, missing, ready)
    return ChatReply(
        session=_session_to_public(chat_session),
        reply=_message_to_public(reply_msg, card_type="entity_summary" if ready else None, card_data={"entities": clean, "editable": True} if ready else None),
        entities=clean,
        missing_fields=missing,
        ready_to_save=ready,
        quick_replies=chips,
    )


async def use_my_info(
    *,
    session: AsyncSession,
    user: User,
    session_id: str,
) -> ChatReply:
    """Auto-fill logged-in user as passenger 1."""
    dao = ChatDAO(session)
    chat_session = await dao.get_session(session_id, user.id)
    if not chat_session:
        raise ValueError("Session not found")

    current = chat_session.entities or {}
    passengers = current.get("passengers") or []

    my_info = {
        "title": "mr",
        "given_name": (user.full_name or "").split()[0] if user.full_name else "",
        "family_name": " ".join((user.full_name or "").split()[1:]) if user.full_name and " " in user.full_name else "",
        "gender": "m",
        "born_on": "",
        "phone_number": "",
    }

    if passengers:
        # Update first passenger
        for k, v in my_info.items():
            if v:
                passengers[0][k] = v
    else:
        passengers = [my_info]

    current["passengers"] = passengers
    await dao.update_session(session_id, entities=current)
    chat_session.entities = current

    name = user.full_name or user.email
    reply_msg = await dao.add_message(
        session_id, "assistant",
        f"Added **{name}** as passenger 1. I still need their date of birth and phone number to complete the booking.",
    )
    await session.commit()

    missing = _compute_missing_fields(current)
    ready = len(missing) == 0
    chips = _compute_quick_replies("collecting", current, missing, ready)

    return ChatReply(
        session=_session_to_public(chat_session),
        reply=_message_to_public(reply_msg),
        entities=current,
        missing_fields=missing,
        ready_to_save=ready,
        quick_replies=chips,
    )


async def get_history(
    *,
    session: AsyncSession,
    user_id: str,
    user: User | None = None,
    session_id: str | None = None,
) -> ChatHistoryResponse:
    """Fetch chat history for current/given session."""
    dao = ChatDAO(session)

    if session_id:
        chat_session = await dao.get_session(session_id, user_id)
    else:
        chat_session = await dao.get_or_create_active_session(user_id)

    if not chat_session:
        chat_session = await dao.get_or_create_active_session(user_id)
        await session.commit()

    messages = await dao.list_messages(chat_session.id)

    # Auto-generate greeting for empty sessions
    if not messages and chat_session.phase == "collecting" and user:
        greeting = await _build_greeting(user, dao, session)
        if greeting:
            greeting_msg = await dao.add_message(chat_session.id, "assistant", greeting)
            await session.commit()
            messages = [greeting_msg]

    entities = chat_session.entities or {}
    missing = _compute_missing_fields(entities)
    ready = len(missing) == 0
    has_proposal = bool(entities.get("_proposal_id"))
    chips = _compute_quick_replies(chat_session.phase, entities, missing, ready, has_proposal=has_proposal)

    return ChatHistoryResponse(
        session=_session_to_public(chat_session),
        messages=[_message_to_public(m) for m in messages],
        quick_replies=chips,
    )


async def start_new_session(
    *,
    session: AsyncSession,
    user_id: str,
) -> ChatSessionPublic:
    """Close any active sessions and start a fresh one."""
    dao = ChatDAO(session)
    existing = await dao.get_or_create_active_session(user_id)
    if existing.phase != "done":
        await dao.close_session(existing.id)

    new_session = ChatSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        entities={},
        phase="collecting",
    )
    dao.session.add(new_session)
    await session.flush()
    await session.commit()
    return _session_to_public(new_session)

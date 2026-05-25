"""Extract trip details from uploaded ticket images/PDFs using GPT-4o Vision.

Supports: boarding pass photos, e-ticket PDFs, booking confirmation screenshots.
Returns structured entities compatible with trip snapshot creation.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Analyze this travel ticket/boarding pass/booking confirmation image and extract ALL available flight information.

Return a JSON object with these fields (include only what you can clearly see):
{
  "flight_number": "AA2117",
  "airline": "American Airlines",
  "origin": "JFK",
  "origin_full": "John F. Kennedy International Airport",
  "destination": "ATL",
  "destination_full": "Hartsfield-Jackson Atlanta International Airport",
  "travel_date": "2026-04-15",
  "departure_time": "14:30",
  "arrival_time": "18:45",
  "cabin_class": "economy",
  "booking_reference": "ABC123",
  "seat": "14A",
  "passenger_name": "John Smith",
  "passenger_title": "mr",
  "gate": "B42",
  "terminal": "4",
  "baggage_allowance": "1 checked bag"
}

RULES:
- Use 3-letter IATA codes for airports (e.g. JFK, LAX, ATL)
- Use YYYY-MM-DD for dates
- Use HH:MM (24-hour) for times
- Use 2-letter airline code + number for flight_number (e.g. AA2117, DL891)
- For cabin_class use: economy, premium_economy, business, or first
- If you can't read a field clearly, omit it entirely
- Return ONLY the JSON object, no other text"""


async def extract_from_image_bytes(image_bytes: bytes, content_type: str = "image/jpeg") -> dict[str, Any]:
    """Send image to GPT-4o Vision and extract ticket data."""
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Map content types
    media_type = content_type
    if media_type == "application/pdf":
        media_type = "image/png"  # PDF should be converted to image first

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage

        llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            api_key=settings.OPENAI_API_KEY,
            max_tokens=500,
        )

        message = HumanMessage(
            content=[
                {"type": "text", "text": EXTRACTION_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{b64}",
                        "detail": "high",
                    },
                },
            ],
        )

        response = await llm.ainvoke([message])
        raw = response.content if hasattr(response, "content") else str(response)

        # Parse JSON from response
        import json
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        extracted = json.loads(text)
        logger.info("ticket_extraction_success", extra={"fields": list(extracted.keys())})
        return {"ok": True, "extracted": extracted}

    except json.JSONDecodeError:
        logger.warning("ticket_extraction_json_parse_failed", extra={"raw": raw[:200] if raw else ""})
        return {"ok": False, "error": "Could not parse extracted data", "raw": raw[:500] if raw else ""}
    except Exception as e:
        logger.exception("ticket_extraction_failed")
        return {"ok": False, "error": str(e)[:200]}


async def extract_from_pdf_bytes(pdf_bytes: bytes) -> dict[str, Any]:
    """Extract from PDF — try text extraction first, fall back to image."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(0)

        # Try text extraction first — works for text-based PDFs
        text_content = page.get_text("text").strip()
        if len(text_content) > 50:
            doc.close()
            logger.info("pdf_text_extraction", extra={"chars": len(text_content)})
            return await extract_from_text(text_content)

        # Fall back to image extraction for scanned/image PDFs
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        doc.close()
        return await extract_from_image_bytes(img_bytes, "image/png")
    except ImportError:
        logger.warning("pymupdf_not_installed_trying_direct")
        return await extract_from_image_bytes(pdf_bytes, "image/png")
    except Exception as e:
        logger.exception("pdf_conversion_failed")
        return {"ok": False, "error": f"PDF processing failed: {str(e)[:200]}"}


async def extract_from_text(text: str) -> dict[str, Any]:
    """Send extracted text to GPT-4o-mini for structured extraction."""
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=settings.OPENAI_API_KEY,
            max_tokens=500,
        )

        prompt = f"""Extract flight details from this boarding pass / ticket text.

Return a JSON object with these fields (include only what you find):
{{
  "flight_number": "AA104",
  "airline": "American Airlines",
  "origin": "JFK",
  "destination": "BOM",
  "travel_date": "2026-03-31",
  "departure_time": "22:30",
  "arrival_time": "23:15",
  "cabin_class": "economy",
  "booking_reference": "RRT2026",
  "seat": "24A",
  "passenger_name": "Karthik Sarma",
  "passenger_title": "mr",
  "gate": "B42",
  "terminal": "4"
}}

Use IATA codes for airports. YYYY-MM-DD for dates. HH:MM 24hr for times.
Return ONLY the JSON object.

TICKET TEXT:
{text}"""

        response = await llm.ainvoke([
            SystemMessage(content="You extract structured flight data from ticket text. Return only valid JSON."),
            HumanMessage(content=prompt),
        ])
        raw = response.content if hasattr(response, "content") else str(response)

        import json
        text_clean = raw.strip()
        if text_clean.startswith("```"):
            text_clean = text_clean.split("\n", 1)[1] if "\n" in text_clean else text_clean[3:]
            if text_clean.endswith("```"):
                text_clean = text_clean[:-3]
            text_clean = text_clean.strip()
            if text_clean.startswith("json"):
                text_clean = text_clean[4:].strip()

        extracted = json.loads(text_clean)
        logger.info("text_extraction_success", extra={"fields": list(extracted.keys())})
        return {"ok": True, "extracted": extracted}

    except Exception as e:
        logger.exception("text_extraction_failed")
        return {"ok": False, "error": str(e)[:200]}


def extracted_to_trip_entities(extracted: dict[str, Any]) -> dict[str, Any]:
    """Convert GPT-4o extracted data to chat entity format (compatible with trip creation)."""
    entities: dict[str, Any] = {}

    if extracted.get("flight_number"):
        entities["flight_number"] = str(extracted["flight_number"]).strip().upper().replace(" ", "")
    if extracted.get("origin"):
        entities["origin"] = str(extracted["origin"]).strip().upper()[:3]
    if extracted.get("destination"):
        entities["destination"] = str(extracted["destination"]).strip().upper()[:3]
    if extracted.get("travel_date"):
        entities["travel_date"] = str(extracted["travel_date"]).strip()
    if extracted.get("departure_time"):
        entities["scheduled_departure_time"] = str(extracted["departure_time"]).strip()
    if extracted.get("arrival_time"):
        entities["scheduled_arrival_time"] = str(extracted["arrival_time"]).strip()
    if extracted.get("cabin_class"):
        cc = str(extracted["cabin_class"]).strip().lower()
        if cc in ("economy", "premium_economy", "business", "first"):
            entities["cabin_class"] = cc

    # Build passenger from extracted name
    name = extracted.get("passenger_name", "")
    if name:
        parts = name.strip().split()
        passenger: dict[str, Any] = {
            "given_name": parts[0] if parts else "",
            "family_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "title": extracted.get("passenger_title", "mr"),
            "gender": "m",
            "born_on": "",
            "phone_number": "",
        }
        entities["passengers"] = [passenger]

    return entities

"""Duffel integration: flight offer search + order creation (test-mode booking)."""

from __future__ import annotations

import asyncio

import httpx

from config import get_settings
from integrations.http_timeout import integration_timeout

DUFFEL_OFFER_REQUESTS_URL = "https://api.duffel.com/air/offer_requests"
DUFFEL_OFFERS_URL = "https://api.duffel.com/air/offers"
DUFFEL_ORDERS_URL = "https://api.duffel.com/air/orders"


def _duffel_headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.DUFFEL_API_KEY:
        raise RuntimeError("DUFFEL_API_KEY is missing")
    return {
        "Authorization": f"Bearer {settings.DUFFEL_API_KEY}",
        "Accept": "application/json",
        "Duffel-Version": settings.DUFFEL_VERSION,
        "Content-Type": "application/json",
    }


async def create_offer_request(*, offer_request_data: dict) -> dict:
    """
    Returns the Duffel response JSON for POST /air/offer_requests.
    The response is usually 201 with a `location` header pointing to the ORQ results.
    """
    async with httpx.AsyncClient(timeout=integration_timeout()) as client:
        r = await client.post(
            DUFFEL_OFFER_REQUESTS_URL,
            headers=_duffel_headers(),
            json={"data": offer_request_data},
        )
        r.raise_for_status()
        return {"json": r.json(), "location": r.headers.get("location")}


async def get_offer_request_results(*, results_url: str) -> dict:
    """Fetches ORQ results from a `location` URL returned by create_offer_request()."""
    # Duffel ORQ `location` can contain `:8000` (as in their quickstarts).
    # Some environments block that port, so we normalize back to 443.
    results_url = results_url.replace("https://api.duffel.com:8000", "https://api.duffel.com")
    async with httpx.AsyncClient(timeout=integration_timeout()) as client:
        r = await client.get(results_url, headers=_duffel_headers())
        r.raise_for_status()
        return r.json()


async def search_flight_offers_with_polling(*, offer_request_data: dict, max_polls: int = 6) -> dict:
    """
    Production-friendly helper:
    - POST offer_requests
    - Poll the returned `location` until it contains `data.offers` (or we exhaust polls)
    """
    created = await create_offer_request(offer_request_data=offer_request_data)
    location = created.get("location")
    if not location:
        return created["json"]

    # Poll results_url
    last_json: dict = {}
    for _ in range(max_polls):
        # Duffel ORQ responses may take a moment; wait briefly between polls.
        await asyncio.sleep(1.0)
        last_json = await get_offer_request_results(results_url=location)
        data = last_json.get("data") or {}
        offers = data.get("offers")
        if isinstance(offers, list) and offers:
            return last_json
    return last_json


async def get_offer_latest(*, offer_id: str) -> dict:
    async with httpx.AsyncClient(timeout=integration_timeout()) as client:
        r = await client.get(
            f"{DUFFEL_OFFERS_URL}/{offer_id}",
            headers=_duffel_headers(),
        )
        r.raise_for_status()
        return r.json()


async def create_order(*, order_payload: dict) -> dict:
    """
    POST /air/orders.

    `order_payload` is the JSON body sent to Duffel, including the top-level `data` key,
    e.g. ``{"data": {"selected_offers": [...], "payments": [...], "passengers": [...]}}``.
    """
    async with httpx.AsyncClient(timeout=integration_timeout()) as client:
        r = await client.post(
            DUFFEL_ORDERS_URL,
            headers=_duffel_headers(),
            json=order_payload,
        )
        r.raise_for_status()
        return r.json()


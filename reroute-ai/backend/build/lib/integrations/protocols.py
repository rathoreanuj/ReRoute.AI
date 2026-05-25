"""Contracts for external data — implementations live alongside (mock + real)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FlightStatusClient(Protocol):
    async def get_status(self, flight_number: str, date: str) -> dict[str, Any]: ...


@runtime_checkable
class FlightSearchClient(Protocol):
    async def search_alternatives(self, trip_context: dict[str, Any]) -> dict[str, Any]: ...

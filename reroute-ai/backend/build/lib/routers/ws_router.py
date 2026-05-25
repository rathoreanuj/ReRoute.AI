"""WebSocket endpoint for real-time disruption notifications.

Clients connect at /api/ws/notifications and receive JSON messages when:
- Monitor detects a disruption
- Agent proposes options
- Auto-rebook completes
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])

# In-memory connected clients (user_id -> list of websockets)
_connections: dict[str, list[WebSocket]] = {}


def _add_connection(user_id: str, ws: WebSocket) -> None:
    _connections.setdefault(user_id, []).append(ws)


def _remove_connection(user_id: str, ws: WebSocket) -> None:
    conns = _connections.get(user_id, [])
    try:
        conns.remove(ws)
    except ValueError:
        pass
    if not conns:
        _connections.pop(user_id, None)


async def push_to_user(user_id: str, event: dict[str, Any]) -> int:
    """Send a JSON event to all connected WebSockets for a user. Returns count sent."""
    conns = _connections.get(user_id, [])
    if not conns:
        return 0
    payload = json.dumps(event)
    sent = 0
    dead: list[WebSocket] = []
    for ws in conns:
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_text(payload)
                sent += 1
            else:
                dead.append(ws)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _remove_connection(user_id, ws)
    return sent


@router.websocket("/notifications")
async def ws_notifications(ws: WebSocket):
    """WebSocket for real-time push notifications.

    Client sends initial JSON: {"token": "<jwt>"} for auth.
    Server sends events as JSON: {"type": "disruption_alert"|"agent_propose"|"auto_rebook", "data": {...}}
    """
    await ws.accept()

    # Authenticate via first message
    user_id: str | None = None
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
        token = msg.get("token") or ""

        # Validate JWT
        from utils.jwt_utils import decode_access_token
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            await ws.close(code=4001, reason="Invalid token")
            return

        _add_connection(user_id, ws)
        await ws.send_text(json.dumps({"type": "connected", "data": {"user_id": user_id}}))
        logger.info("ws_connected", extra={"user_id": user_id})

    except asyncio.TimeoutError:
        await ws.close(code=4002, reason="Auth timeout")
        return
    except Exception:
        await ws.close(code=4003, reason="Auth failed")
        return

    # Keep connection alive until client disconnects
    try:
        while True:
            # Client can send pings; we just keep the connection open
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=300.0)
                # Handle ping
                if data.strip() == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive
                try:
                    await ws.send_text(json.dumps({"type": "keepalive"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        if user_id:
            _remove_connection(user_id, ws)
            logger.info("ws_disconnected", extra={"user_id": user_id})

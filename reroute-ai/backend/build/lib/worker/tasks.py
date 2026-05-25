"""Background tasks — monitor, agent propose, email, stale-claim recovery."""

from __future__ import annotations

import asyncio
import logging

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="reroute.ping")
def ping() -> str:
    """Health check for worker connectivity."""
    return "pong"


@celery_app.task(name="reroute.monitor.enqueue_cycle")
def enqueue_monitor_cycle() -> dict:
    """Beat hook: flight + weather scan per trip; writes disruption_events (monitor_scan / monitor_alert)."""
    from database import get_session_factory
    from service.monitor_cycle_service import run_monitor_cycle

    async def _run() -> dict:
        factory = get_session_factory()
        async with factory() as session:
            return await run_monitor_cycle(session=session)

    return asyncio.run(_run())


@celery_app.task(name="reroute.email.send_resend_html", ignore_result=True)
def send_resend_html_email(*, to_email: str, subject: str, html: str) -> dict:
    """Send HTML email via Resend (sync httpx — runs in worker)."""
    from integrations.resend_sync import send_email_html_sync

    return send_email_html_sync(to_email=to_email, subject=subject, html=html)


@celery_app.task(name="reroute.agent.propose")
def run_agent_propose_task(
    user_id: str,
    trip_id: str,
    simulate_disruption: str | None = None,
) -> dict:
    """Run full propose_for_trip in a worker (async DB + integrations via asyncio.run)."""
    from database import get_session_factory
    from service.agent_service import propose_for_trip

    async def _run() -> dict:
        factory = get_session_factory()
        async with factory() as session:
            res = await propose_for_trip(
                session=session,
                user_id=user_id,
                trip_id=trip_id,
                simulate_disruption=simulate_disruption,
            )
            return res.model_dump()

    return asyncio.run(_run())


@celery_app.task(name="reroute.agent.autonomous_disruption", ignore_result=True)
def run_autonomous_disruption_task(
    *,
    user_id: str,
    trip_id: str,
    disruption_type: str,
    flight_status: dict | None = None,
) -> dict:
    """Autonomous loop: detect → propose → auto-confirm (if enabled) → notify."""
    from database import get_session_factory
    from service.agent_service import autonomous_disruption_handler

    async def _run() -> dict:
        factory = get_session_factory()
        async with factory() as session:
            return await autonomous_disruption_handler(
                session=session,
                user_id=user_id,
                trip_id=trip_id,
                disruption_type=disruption_type,
                flight_status=flight_status or {},
            )

    result = asyncio.run(_run())
    logger.info("autonomous_disruption_task_done", extra={"trip_id": trip_id, "result": result})
    return result


@celery_app.task(name="reroute.agent.release_stale_applying")
def release_stale_applying_task() -> int:
    """Periodic: move long-lived `applying` proposals back to `pending` for retry."""
    from database import get_session_factory
    from service import proposal_service

    async def _run() -> int:
        factory = get_session_factory()
        async with factory() as session:
            n = await proposal_service.release_stale_applying(session=session)
            await session.commit()
            return n

    return asyncio.run(_run())

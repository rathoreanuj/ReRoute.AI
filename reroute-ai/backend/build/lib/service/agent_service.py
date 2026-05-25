"""Agent orchestration (Duffel offers + cascade preview + confirm/apply).

This implements the production-grade behavior you requested:
- tools-first: flight status/weather/alternatives are sourced from providers
- ranking: top 3 options with deterministic scoring fallback
- cascade preview: connection/hotel/meeting adjustments
- confirmation gate: no booking until /agent/confirm
- apply: Duffel test-mode order creation (or mock fallback)
- notifications: in-app response + email via Resend (if enabled)
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from integrations.resend_client import send_email_html
from model.proposal_model import RebookingProposal
from schema.agent_schemas import (
    AgentConfirmRequest,
    AgentConfirmResponse,
    AgentProposeJobAccepted,
    AgentProposeJobStatus,
    AgentProposeResponse,
    RankedOptionDTO,
)
from service.agent_graph_service import run_confirm_graph, run_propose_graph
from service import proposal_service, trip_service
from service.itinerary_service import apply_rebooking_plan

logger = logging.getLogger(__name__)


async def _dispatch_agent_email(*, to_email: str, subject: str, html: str) -> dict:
    if get_settings().email_via_celery:
        try:
            from worker.tasks import send_resend_html_email

            send_resend_html_email.delay(to_email=to_email, subject=subject, html=html)
            return {"sent": False, "email_queued": True, "reason": "celery"}
        except Exception:
            logger.exception("email_celery_enqueue_failed_falling_back_inline")
            return await send_email_html(to_email=to_email, subject=subject, html=html)
    return await send_email_html(to_email=to_email, subject=subject, html=html)


def enqueue_async_propose(
    *,
    user_id: str,
    trip_id: str,
    simulate_disruption: str | None,
) -> AgentProposeJobAccepted:
    from utils.job_redis import register_propose_job
    from worker.celery_app import celery_app
    from worker.tasks import run_agent_propose_task

    settings = get_settings()
    try:
        ar = run_agent_propose_task.apply_async(
            args=[user_id, trip_id, simulate_disruption],
        )
    except Exception:
        logger.exception("async_propose_celery_enqueue_failed")
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Async propose unavailable (Redis/Celery must be reachable).",
        ) from None

    try:
        register_propose_job(task_id=ar.id, user_id=user_id)
    except Exception:
        logger.exception("async_propose_redis_register_failed")
        try:
            celery_app.control.revoke(ar.id, terminate=True)
        except Exception:
            logger.exception("async_propose_revoke_after_register_failure_failed")
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Async propose unavailable (job registration failed).",
        ) from None

    poll_path = f"{settings.api_prefix}/agent/propose/jobs/{ar.id}"
    return AgentProposeJobAccepted(task_id=ar.id, poll_path=poll_path)


def get_async_propose_job_status(*, task_id: str, user_id: str) -> AgentProposeJobStatus:
    from celery.result import AsyncResult

    from utils.job_redis import JobRedisUnavailableError, get_propose_job_owner
    from worker.celery_app import celery_app

    try:
        owner = get_propose_job_owner(task_id)
    except JobRedisUnavailableError:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job status temporarily unavailable.",
        ) from None

    if owner != user_id:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")

    ar = AsyncResult(task_id, app=celery_app)
    st = ar.state
    if st == "SUCCESS":
        raw = ar.result
        if isinstance(raw, dict):
            return AgentProposeJobStatus(
                task_id=task_id,
                state=st,
                result=AgentProposeResponse.model_validate(raw),
                error=None,
            )
        return AgentProposeJobStatus(task_id=task_id, state=st, result=None, error=None)
    if st == "FAILURE":
        logger.warning(
            "async_propose_task_failed",
            extra={"task_id": task_id, "celery_info_type": type(ar.info).__name__},
        )
        return AgentProposeJobStatus(task_id=task_id, state=st, result=None, error="task_failed")
    return AgentProposeJobStatus(task_id=task_id, state=st, result=None, error=None)


async def _confirm_terminal_state_response(
    *,
    row: RebookingProposal,
    body: AgentConfirmRequest,
    user_id: str,
    session: AsyncSession,
) -> AgentConfirmResponse | None:
    """Handle applying / applied rows (concurrent confirm or idempotent replay)."""
    if row.status == "applying":
        return AgentConfirmResponse(
            applied=False,
            itinerary_revision=None,
            message="Another confirmation request is in progress for this proposal.",
            duffel_order_id=None,
            email_sent=False,
        )
    if row.status == "applied":
        if row.selected_offer_id == body.selected_option_id:
            trip_ctx = (row.context or {}).get("trip_context") or {}
            tid = trip_ctx.get("trip_id")
            itinerary_revision: int | None = None
            if isinstance(tid, str) and tid:
                trip_pub = await trip_service.get_trip(user_id=user_id, trip_id=tid, session=session)
                itinerary_revision = trip_pub.itinerary_revision
            return AgentConfirmResponse(
                applied=True,
                itinerary_revision=itinerary_revision,
                message="Rebooking was already applied for this option (idempotent replay).",
                duffel_order_id=row.duffel_order_id,
                email_sent=False,
            )
        return AgentConfirmResponse(
            applied=False,
            itinerary_revision=None,
            message="This proposal was already applied with a different option.",
            duffel_order_id=row.duffel_order_id,
            email_sent=False,
        )
    return None


async def propose_for_trip(
    *,
    session: AsyncSession,
    user_id: str,
    trip_id: str,
    simulate_disruption: str | None = None,
) -> AgentProposeResponse:
    """Detect disruption → propose top-3 options → cascade preview → email + proposal persistence."""
    proposal_id = str(uuid.uuid4())
    logger.info("propose_for_trip", extra={"trip_id": trip_id, "proposal_id": proposal_id})

    trip_context = await trip_service.get_snapshot_for_agent(
        user_id=user_id, trip_id=trip_id, session=session
    )

    legs_block = trip_context.get("legs") if isinstance(trip_context.get("legs"), dict) else {}
    primary = legs_block.get("primary_flight")
    if not isinstance(primary, dict) or not primary.get("flight_number") or not primary.get("date"):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Trip snapshot legs.primary_flight is missing required fields",
        )

    graph = await run_propose_graph(
        trip_context=trip_context,
        simulate_disruption=simulate_disruption,
    )
    duffel_passengers = graph.get("duffel_passengers") or []
    tool_trace_summary = [str(x) for x in (graph.get("tool_trace_summary") or [])]
    disruption_type = str(graph.get("disruption_type") or "unknown")
    flight_source = str((graph.get("flight_status") or {}).get("source") or "")
    booking_mode = str(graph.get("booking_mode") or "mock")
    options_by_offer_id = graph.get("options_by_offer_id") or {}
    options = [RankedOptionDTO.model_validate(o) for o in (graph.get("options") or [])]
    cascade_preview = graph.get("cascade_preview") if isinstance(graph.get("cascade_preview"), dict) else {}
    compensation_draft = (
        graph.get("compensation_draft") if isinstance(graph.get("compensation_draft"), dict) else {}
    )
    search_meta = graph.get("search_meta") if isinstance(graph.get("search_meta"), dict) else {}
    # New LLM + enhanced fields
    llm_disruption_narrative = graph.get("llm_disruption_narrative")
    cascade_narrative = graph.get("cascade_narrative")
    offers_expired_at = graph.get("offers_expired_at")
    price_comparison = graph.get("price_comparison")
    passenger_validation = graph.get("passenger_validation")

    # 7) Persist proposal context for confirm/apply
    # We store Duffel booking context (passengers ids + payments per offer).
    # Passenger mapping: Duffel returns passenger ids; we attach passenger details from trip_context.
    # Only force manual review when provider responded but status is still unknown.
    # Do not block all trips when status feed is unavailable/misconfigured.
    requires_user_review = False  # disabled for demo
    graph_checkpoints = [dict(x) for x in (graph.get("checkpoint_events") or []) if isinstance(x, dict)]
    proposal_context = {
        "owner_user_id": user_id,
        "booking_mode": booking_mode,
        "trip_context": trip_context,
        "duffel_passengers": duffel_passengers,
        "passengers_details": trip_context.get("passengers", []),
        "options_by_offer_id": options_by_offer_id,
        "disruption_type": disruption_type,
        "disruption_source": flight_source,
        "requires_user_review": requires_user_review,
        "search_meta": search_meta,
        "graph_checkpoint": {
            "propose": {
                "phase": "await_user_review" if requires_user_review else "await_confirm",
                "events": graph_checkpoints,
            }
        },
    }
    tid = trip_context.get("trip_id")
    if not isinstance(tid, str) or not tid:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Trip snapshot missing trip_id",
        )
    await proposal_service.persist_new_proposal(
        session=session,
        proposal_id=proposal_id,
        trip_id=tid,
        user_id=user_id,
        context=proposal_context,
        disruption_type=disruption_type,
        tool_trace_summary=tool_trace_summary,
        ranked_option_ids=[o.option_id for o in options],
        commit=False,
    )
    await session.commit()

    # 8) Notifications (in-app via response + email via Resend)
    notification_status: dict[str, object] = {"email_sent": False, "channel": ["in-app"]}
    to_email = trip_context.get("user", {}).get("email")
    if to_email and booking_mode:  # always attempt; resend_client disables if key missing
        subject = "ReRoute.AI: We found flight rebooking options"
        html = _build_propose_email_html(
            to_name=trip_context["user"].get("full_name", "Traveler"),
            disruption_type=disruption_type,
            top_options=options,
        )
        res = await _dispatch_agent_email(to_email=to_email, subject=subject, html=html)
        notification_status["email_sent"] = bool(res.get("sent"))
        notification_status["email_reason"] = res.get("reason")
        if res.get("email_queued"):
            notification_status["email_queued"] = True

    disruption_summary = (
        "Flight cancelled."
        if disruption_type == "cancelled"
        else "Flight delayed."
        if disruption_type == "delayed"
        else "Flight diverted."
        if disruption_type == "diverted"
        else "Live disruption status unavailable right now."
    )
    phase = "await_user_review" if requires_user_review else "await_confirm"
    return AgentProposeResponse(
        proposal_id=proposal_id,
        phase=phase,
        requires_user_review=requires_user_review,
        disruption_summary=disruption_summary,
        llm_disruption_narrative=llm_disruption_narrative,
        ranked_options=options,
        tool_trace_summary=tool_trace_summary,
        cascade_preview=cascade_preview,
        cascade_narrative=cascade_narrative,
        compensation_draft=compensation_draft,
        notification_status=notification_status,
        search_meta=search_meta,
        offers_expired_at=offers_expired_at,
        price_comparison=price_comparison,
        passenger_validation=passenger_validation,
    )


async def confirm_and_apply(
    *,
    session: AsyncSession,
    user_id: str,
    body: AgentConfirmRequest,
) -> AgentConfirmResponse:
    """Confirmation gate: only here we create a Duffel test-mode order + simulate itinerary update."""
    logger.info(
        "confirm_and_apply",
        extra={"proposal_id": body.proposal_id, "option": body.selected_option_id},
    )

    row = await proposal_service.get_proposal_row(
        session=session,
        proposal_id=body.proposal_id,
        user_id=user_id,
    )
    if not row:
        return AgentConfirmResponse(
            applied=False,
            itinerary_revision=None,
            message="Proposal not found. Please propose again.",
            duffel_order_id=None,
            email_sent=False,
        )

    term = await _confirm_terminal_state_response(row=row, body=body, user_id=user_id, session=session)
    if term is not None:
        return term

    claimed = await proposal_service.try_claim_confirm(
        session=session,
        proposal_id=body.proposal_id,
        user_id=user_id,
    )
    if not claimed:
        row2 = await proposal_service.get_proposal_row(
            session=session,
            proposal_id=body.proposal_id,
            user_id=user_id,
        )
        if not row2:
            return AgentConfirmResponse(
                applied=False,
                itinerary_revision=None,
                message="Proposal not found. Please propose again.",
                duffel_order_id=None,
                email_sent=False,
            )
        term2 = await _confirm_terminal_state_response(row=row2, body=body, user_id=user_id, session=session)
        if term2 is not None:
            return term2
        if row2.status == "pending":
            return AgentConfirmResponse(
                applied=False,
                itinerary_revision=None,
                message="Could not acquire confirmation lock. Please retry.",
                duffel_order_id=None,
                email_sent=False,
            )
        return AgentConfirmResponse(
            applied=False,
            itinerary_revision=None,
            message="Unexpected proposal state during confirmation.",
            duffel_order_id=None,
            email_sent=False,
        )

    duffel_order_id: str | None = None
    email_sent = False
    to_email: str | None = None
    trip_context: dict[str, Any] = {}
    proposal: dict[str, Any] = {}
    tid: object = None
    itinerary_revision: int | None = None

    try:
        proposal = copy.deepcopy(row.context)
        trip_context = proposal["trip_context"]
        booking_mode = proposal.get("booking_mode", "live")
        requires_user_review = bool(proposal.get("requires_user_review"))
        options_by_offer_id = proposal.get("options_by_offer_id") or {}
        to_email = trip_context.get("user", {}).get("email")
        confirm_graph = await run_confirm_graph(
            booking_mode=str(booking_mode),
            trip_context=trip_context,
            selected_option_id=body.selected_option_id,
            options_by_offer_id=options_by_offer_id,
            requires_user_review=requires_user_review,
            acknowledged_uncertainty=body.acknowledge_disruption_uncertainty,
            duffel_passengers=proposal.get("duffel_passengers") or [],
            passenger_details=proposal.get("passengers_details") or [],
        )
        checkpoints = [dict(x) for x in (confirm_graph.get("checkpoint_events") or []) if isinstance(x, dict)]
        ctx = copy.deepcopy(row.context or {})
        cp = ctx.get("graph_checkpoint") if isinstance(ctx.get("graph_checkpoint"), dict) else {}
        cp["confirm"] = {"events": checkpoints}
        ctx["graph_checkpoint"] = cp
        row.context = ctx
        await session.flush()

        if not confirm_graph.get("can_apply"):
            await proposal_service.release_confirm_claim(
                session=session,
                proposal_id=body.proposal_id,
                user_id=user_id,
            )
            await session.flush()
            return AgentConfirmResponse(
                applied=False,
                itinerary_revision=None,
                message=str(confirm_graph.get("error_message") or "Could not confirm this option."),
                duffel_order_id=None,
                email_sent=False,
            )

        applied_option_id = str(confirm_graph.get("applied_option_id") or body.selected_option_id)
        option_ctx = confirm_graph.get("option_ctx") or options_by_offer_id.get(body.selected_option_id) or {}
        duffel_order_id = str(confirm_graph.get("duffel_order_id") or "") or None
        apply_res = await apply_rebooking_plan(trip_context=trip_context, option=option_ctx)
        arrival_raw = apply_res.get("arrival_time") or option_ctx.get("arrival_time")
        arrival_s = str(arrival_raw) if arrival_raw else None

        tid = trip_context.get("trip_id")

        if isinstance(tid, str) and tid:
            await trip_service.bump_itinerary_revision(
                user_id=user_id, trip_id=tid, session=session, commit=False
            )
        marked = await proposal_service.mark_proposal_applied(
            session=session,
            proposal_id=body.proposal_id,
            user_id=user_id,
            disruption_type=proposal.get("disruption_type"),
            selected_offer_id=applied_option_id,
            duffel_order_id=duffel_order_id,
            commit=False,
        )
        if not marked:
            await proposal_service.release_confirm_claim(
                session=session,
                proposal_id=body.proposal_id,
                user_id=user_id,
            )
            await session.rollback()
            return AgentConfirmResponse(
                applied=False,
                itinerary_revision=None,
                message="Could not finalize rebooking (proposal may have expired).",
                duffel_order_id=duffel_order_id,
                email_sent=False,
            )
        if isinstance(tid, str) and tid:
            await trip_service.merge_applied_rebooking_to_snapshot(
                user_id=user_id,
                trip_id=tid,
                session=session,
                selected_offer_id=applied_option_id,
                duffel_order_id=duffel_order_id,
                arrival_time=arrival_s,
                commit=False,
            )
        await session.commit()

        if isinstance(tid, str) and tid:
            trip_pub = await trip_service.get_trip(user_id=user_id, trip_id=tid, session=session)
            itinerary_revision = trip_pub.itinerary_revision

        email_queued = False
        if to_email:
            subject = "ReRoute.AI: Rebooking confirmed"
            html = _build_confirm_email_html(
                to_name=trip_context["user"].get("full_name", "Traveler"),
                disruption_type=proposal.get("disruption_type"),
                order_id=duffel_order_id or "N/A",
                option_id=applied_option_id,
            )
            res = await _dispatch_agent_email(to_email=to_email, subject=subject, html=html)
            email_sent = bool(res.get("sent"))
            email_queued = bool(res.get("email_queued"))

        return AgentConfirmResponse(
            applied=True,
            itinerary_revision=itinerary_revision,
            message=f"Rebooking applied (order_id={duffel_order_id}).",
            duffel_order_id=duffel_order_id,
            email_sent=email_sent,
            email_queued=email_queued if email_queued else None,
        )
    except Exception:
        await proposal_service.release_confirm_claim(
            session=session,
            proposal_id=body.proposal_id,
            user_id=user_id,
        )
        await session.rollback()
        raise


def _build_propose_email_html(
    *,
    to_name: str,
    disruption_type: str,
    top_options: list[RankedOptionDTO],
    frontend_url: str | None = None,
    trip_id: str | None = None,
    proposal_id: str | None = None,
) -> str:
    option_items = ""
    for i, o in enumerate(top_options[:3], 1):
        confirm_link = ""
        if frontend_url and proposal_id:
            confirm_link = (
                f' — <a href="{frontend_url}/confirm-booking?proposal_id={proposal_id}'
                f'&option_id={o.option_id}" style="color:#3b82f6;">Confirm this option</a>'
            )
        option_items += f"<li><b>Option {i}:</b> {o.summary}{confirm_link}</li>"
    trip_link = f'<p><a href="{frontend_url}/trips/{trip_id}" style="color:#3b82f6;">View trip in app</a></p>' if frontend_url and trip_id else ""
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #333;">
        <div style="max-width: 600px; margin: 0 auto;">
          <h2 style="color: #1e293b;">Hi {to_name},</h2>
          <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 16px; margin: 16px 0; border-radius: 4px;">
            <strong>Disruption detected:</strong> Your flight has been <b>{disruption_type}</b>.
          </div>
          <p>ReRoute.AI found rebooking alternatives for you:</p>
          <ul style="line-height: 2;">{option_items}</ul>
          {trip_link}
          <p style="color: #6b7280; font-size: 13px;">Offers expire ~30 minutes after detection. Confirm soon for best availability.</p>
          <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;" />
          <p style="color: #9ca3af; font-size: 12px;">ReRoute.AI — Autonomous Travel Disruption Management</p>
        </div>
      </body>
    </html>
    """.strip()


def _build_confirm_email_html(
    *, to_name: str, disruption_type: str, order_id: str, option_id: str
) -> str:
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #333;">
        <div style="max-width: 600px; margin: 0 auto;">
          <h2 style="color: #1e293b;">Hi {to_name},</h2>
          <div style="background: #d1fae5; border-left: 4px solid #10b981; padding: 12px 16px; margin: 16px 0; border-radius: 4px;">
            <strong>Rebooking confirmed!</strong>
          </div>
          <p>Your flight was <b>{disruption_type}</b>. We've rebooked you:</p>
          <ul>
            <li><b>Selected option:</b> {option_id}</li>
            <li><b>Booking reference:</b> {order_id}</li>
          </ul>
          <p>Your itinerary has been updated automatically. Check the app for full details.</p>
          <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;" />
          <p style="color: #9ca3af; font-size: 12px;">ReRoute.AI — Autonomous Travel Disruption Management</p>
        </div>
      </body>
    </html>
    """.strip()


def _build_auto_rebook_email_html(
    *, to_name: str, disruption_type: str, order_id: str, option_summary: str, trip_id: str, frontend_url: str
) -> str:
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #333;">
        <div style="max-width: 600px; margin: 0 auto;">
          <h2 style="color: #1e293b;">Hi {to_name},</h2>
          <div style="background: #d1fae5; border-left: 4px solid #10b981; padding: 12px 16px; margin: 16px 0; border-radius: 4px;">
            <strong>We've automatically rebooked your flight!</strong>
          </div>
          <p>Your original flight was <b>{disruption_type}</b>. Our AI agent detected the disruption, found alternatives, and booked the best option for you:</p>
          <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 16px; border-radius: 8px; margin: 16px 0;">
            <p style="margin: 0; font-size: 15px;"><b>{option_summary}</b></p>
            <p style="margin: 8px 0 0; color: #6b7280;">Booking ref: {order_id}</p>
          </div>
          <p><a href="{frontend_url}/trips/{trip_id}" style="color: #3b82f6; font-weight: 600;">View updated itinerary →</a></p>
          <p style="color: #6b7280; font-size: 13px;">This was an automatic rebooking based on your preferences. You can disable auto-rebook in Settings.</p>
          <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;" />
          <p style="color: #9ca3af; font-size: 12px;">ReRoute.AI — Autonomous Travel Disruption Management</p>
        </div>
      </body>
    </html>
    """.strip()


# ── Autonomous Disruption Handler ─────────────────────────────

async def autonomous_disruption_handler(
    *,
    session: AsyncSession,
    user_id: str,
    trip_id: str,
    disruption_type: str,
    flight_status: dict,
) -> dict:
    """Called by monitor cycle when a real disruption is detected.

    1. Runs propose_for_trip to get options
    2. If user has auto_rebook=True → auto-confirms best option
    3. If not → sends email with options
    4. Returns summary of actions taken
    """
    from dao.user_dao import UserDAO

    user_dao = UserDAO(session)
    user = await user_dao.get_by_id(user_id)
    if not user:
        logger.warning("autonomous_handler_user_not_found", extra={"user_id": user_id})
        return {"action": "skipped", "reason": "user_not_found"}

    settings = get_settings()
    frontend_url = settings.frontend_url.rstrip("/")
    to_name = user.full_name or user.email
    to_email = user.email

    # Step 1: Run propose
    try:
        propose_result = await propose_for_trip(
            session=session,
            user_id=user_id,
            trip_id=trip_id,
        )
    except Exception as e:
        logger.exception("autonomous_propose_failed", extra={"trip_id": trip_id})
        return {"action": "propose_failed", "error": str(e)[:200]}

    if not propose_result.ranked_options:
        # No options found — notify user
        if to_email:
            await _dispatch_agent_email(
                to_email=to_email,
                subject=f"ReRoute.AI: Flight {disruption_type} — no alternatives found",
                html=f"""
                <html><body style="font-family: Arial, sans-serif;">
                  <h2>Hi {to_name},</h2>
                  <p>We detected a <b>{disruption_type}</b> on your flight but couldn't find alternatives right now.</p>
                  <p><a href="{frontend_url}/trips/{trip_id}">Check your trip</a> for updates.</p>
                </body></html>
                """,
            )
        return {"action": "notified_no_options", "proposal_id": propose_result.proposal_id}

    # Step 2: Check auto_rebook preference
    auto_rebook = bool(getattr(user, "auto_rebook", False))

    if auto_rebook:
        # Auto-confirm the best option (first ranked)
        best = propose_result.ranked_options[0]
        try:
            confirm_body = AgentConfirmRequest(
                proposal_id=propose_result.proposal_id,
                selected_option_id=best.option_id,
                acknowledge_disruption_uncertainty=True,
            )
            confirm_result = await confirm_and_apply(
                session=session,
                user_id=user_id,
                body=confirm_body,
            )

            if confirm_result.applied:
                # Send auto-rebook confirmation email
                if to_email:
                    await _dispatch_agent_email(
                        to_email=to_email,
                        subject="ReRoute.AI: Your flight was auto-rebooked!",
                        html=_build_auto_rebook_email_html(
                            to_name=to_name,
                            disruption_type=disruption_type,
                            order_id=confirm_result.duffel_order_id or "N/A",
                            option_summary=best.summary,
                            trip_id=trip_id,
                            frontend_url=frontend_url,
                        ),
                    )
                logger.info(
                    "autonomous_auto_rebook_success",
                    extra={
                        "trip_id": trip_id,
                        "option_id": best.option_id,
                        "order_id": confirm_result.duffel_order_id,
                    },
                )
                # Push WebSocket notification
                try:
                    from routers.ws_router import push_to_user
                    import asyncio
                    await push_to_user(user_id, {
                        "type": "auto_rebook",
                        "data": {
                            "trip_id": trip_id,
                            "disruption_type": disruption_type,
                            "option_summary": best.summary,
                            "duffel_order_id": confirm_result.duffel_order_id,
                            "message": f"Your flight was {disruption_type}. We auto-rebooked you!",
                        },
                    })
                except Exception:
                    pass  # WS push is best-effort

                return {
                    "action": "auto_rebooked",
                    "proposal_id": propose_result.proposal_id,
                    "option_id": best.option_id,
                    "duffel_order_id": confirm_result.duffel_order_id,
                }
            else:
                logger.warning("autonomous_auto_rebook_not_applied", extra={"message": confirm_result.message})
                # Fall through to manual notification
        except Exception as e:
            logger.exception("autonomous_auto_confirm_failed", extra={"trip_id": trip_id})
            # Fall through to manual notification

    # Step 3: Send email with options (manual mode or auto-rebook failed)
    if to_email:
        await _dispatch_agent_email(
            to_email=to_email,
            subject=f"ReRoute.AI: Flight {disruption_type} — {len(propose_result.ranked_options)} alternatives found",
            html=_build_propose_email_html(
                to_name=to_name,
                disruption_type=disruption_type,
                top_options=propose_result.ranked_options,
                frontend_url=frontend_url,
                trip_id=trip_id,
                proposal_id=propose_result.proposal_id,
            ),
        )

    # Push WebSocket notification
    try:
        from routers.ws_router import push_to_user
        await push_to_user(user_id, {
            "type": "disruption_alert",
            "data": {
                "trip_id": trip_id,
                "disruption_type": disruption_type,
                "proposal_id": propose_result.proposal_id,
                "options_count": len(propose_result.ranked_options),
                "message": f"Your flight was {disruption_type}. We found {len(propose_result.ranked_options)} alternatives.",
            },
        })
    except Exception:
        pass

    logger.info(
        "autonomous_notified_with_options",
        extra={"trip_id": trip_id, "proposal_id": propose_result.proposal_id, "options": len(propose_result.ranked_options)},
    )
    return {
        "action": "notified_with_options",
        "proposal_id": propose_result.proposal_id,
        "options_count": len(propose_result.ranked_options),
        "auto_rebook": auto_rebook,
    }

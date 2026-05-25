"""Bind Celery propose task ids to user ids for authorized polling."""

from __future__ import annotations

import logging

import redis

from config import get_settings

logger = logging.getLogger(__name__)

_PREFIX = "reroute:propose_job:"


class JobRedisUnavailableError(Exception):
    """Raised when Redis cannot be reached for job ownership (distinct from missing key)."""


def _client() -> redis.Redis:
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def register_propose_job(*, task_id: str, user_id: str, ttl_sec: int = 3600) -> None:
    _client().setex(f"{_PREFIX}{task_id}", ttl_sec, user_id)


def get_propose_job_owner(task_id: str) -> str | None:
    """Return owning user_id or None if key missing. Raises JobRedisUnavailableError on Redis errors."""
    try:
        return _client().get(f"{_PREFIX}{task_id}")
    except redis.RedisError as e:
        logger.warning("job_redis_get_owner_failed", extra={"error": str(e)})
        raise JobRedisUnavailableError(str(e)) from e


def verify_propose_job_user(*, task_id: str, user_id: str) -> bool:
    try:
        return get_propose_job_owner(task_id) == user_id
    except JobRedisUnavailableError:
        return False


def delete_propose_job(*, task_id: str) -> None:
    try:
        _client().delete(f"{_PREFIX}{task_id}")
    except redis.RedisError:
        pass

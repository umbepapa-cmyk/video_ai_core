"""
FASE 4: Daily API budget circuit breaker.

Tracks Fal.ai spend against DAILY_API_BUDGET_USD with automatic UTC midnight reset.
Uses Redis when REDIS_URL is available, otherwise local file cache.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from exceptions import BudgetExceededError

load_dotenv()
logger = logging.getLogger(__name__)

DAILY_API_BUDGET_USD = float(os.getenv("DAILY_API_BUDGET_USD", "20.0"))
REDIS_URL = os.getenv("REDIS_URL")
CACHE_FILE = Path(".cache/daily_budget.json")
REDIS_KEY_PREFIX = "appvideoai:daily_budget:"


def _utc_date_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _redis_key() -> str:
    return f"{REDIS_KEY_PREFIX}{_utc_date_key()}"


class _BudgetStorage:
    """Redis or file-backed daily spend storage."""

    def __init__(self) -> None:
        self._redis = None
        if REDIS_URL:
            try:
                import redis

                self._redis = redis.from_url(REDIS_URL, decode_responses=True)
                self._redis.ping()
                logger.debug("Budget tracker using Redis backend")
            except Exception as exc:
                logger.warning("Redis unavailable for budget tracker: %s", exc)
                self._redis = None

    def get_spend(self) -> float:
        if self._redis:
            value = self._redis.get(_redis_key())
            return float(value) if value else 0.0
        return self._read_file_spend()

    def add_spend(self, usd: float) -> float:
        if self._redis:
            total = self._redis.incrbyfloat(_redis_key(), usd)
            self._redis.expire(_redis_key(), 86400 * 2)
            return float(total)
        return self._write_file_spend(self._read_file_spend() + usd)

    def _read_file_spend(self) -> float:
        if not CACHE_FILE.exists():
            return 0.0
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if data.get("date") != _utc_date_key():
                return 0.0
            return float(data.get("spend", 0.0))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return 0.0

    def _write_file_spend(self, spend: float) -> float:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"date": _utc_date_key(), "spend": round(spend, 6)}
        CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")
        return spend


_storage: Optional[_BudgetStorage] = None


def _get_storage() -> _BudgetStorage:
    global _storage
    if _storage is None:
        _storage = _BudgetStorage()
    return _storage


def get_daily_spend() -> float:
    """Return current UTC-day API spend in USD."""
    return round(_get_storage().get_spend(), 4)


def record_spend(usd: float) -> float:
    """Record API spend and return updated daily total."""
    if usd <= 0:
        return get_daily_spend()
    total = _get_storage().add_spend(usd)
    logger.info(
        "API spend recorded: +$%.4f (daily total: $%.4f / $%.2f)",
        usd,
        total,
        DAILY_API_BUDGET_USD,
    )
    return round(total, 4)


def check_budget(estimated_cost: float) -> bool:
    """
    Verify estimated cost fits within daily budget.

    Raises:
        BudgetExceededError: if daily spend + estimate exceeds budget.
    """
    current = get_daily_spend()
    projected = current + estimated_cost

    if projected > DAILY_API_BUDGET_USD:
        remaining = max(0.0, DAILY_API_BUDGET_USD - current)
        raise BudgetExceededError(
            f"Daily API budget exceeded: "
            f"spent=${current:.2f}, estimate=${estimated_cost:.2f}, "
            f"budget=${DAILY_API_BUDGET_USD:.2f}, remaining=${remaining:.2f}"
        )

    logger.debug(
        "Budget check passed: spent=$%.2f + estimate=$%.2f <= $%.2f",
        current,
        estimated_cost,
        DAILY_API_BUDGET_USD,
    )
    return True

"""Daily API spend tracker with configurable limit."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from exceptions import BudgetExceededError

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parent / "tmpfs" / "budget_state.json"
_DEFAULT_LIMIT = float(os.getenv("DAILY_BUDGET_USD", "20"))


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"spent_usd": 0.0}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"spent_usd": 0.0}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def check_budget(estimated_usd: float) -> None:
    state = _load_state()
    spent = float(state.get("spent_usd", 0.0))
    limit = _DEFAULT_LIMIT
    if spent + estimated_usd > limit:
        raise BudgetExceededError(
            f"Daily budget exceeded: spent=${spent:.2f} + estimate=${estimated_usd:.2f} > ${limit:.2f}",
            spent_usd=spent,
            limit_usd=limit,
        )
    logger.debug(
        "Budget check passed: spent=$%.2f + estimate=$%.2f <= $%.2f",
        spent,
        estimated_usd,
        limit,
    )


def record_spend(amount_usd: float) -> None:
    state = _load_state()
    state["spent_usd"] = round(float(state.get("spent_usd", 0.0)) + float(amount_usd), 4)
    _save_state(state)

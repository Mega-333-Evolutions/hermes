"""Autonomous-mode boot wiring for GatewayRunner.

Split out of ``gateway/run_startup.py``: restart disables autonomous mode
(spec) and the auto-resume drain must never restart an autonomous turn.
Module functions take the runner, the same shape as
``gateway.shutdown_flush.recover_gateway_pending``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("gateway.run")

_AUTONOMOUS_AUTO_RESUME_BLOCK_STATES = frozenset({"running", "failed", "stopped"})


def autonomous_auto_resume_blocker(home: str | Path, session_key: str) -> Optional[dict[str, Any]]:
    """The autonomous run record blocking an auto-resume for this session, if any.

    A record in ``running`` (reconnect path), ``failed`` (what the boot
    reset leaves behind) or ``stopped`` (an owner-ended run with a leftover
    resume mark) means the interrupted turn belonged to the run: restarting
    it as an ordinary turn would rerun the goal's work outside the run
    record, and the owner decides via the resume ask on re-enable. Any other
    state — ``done``, ``idle``, or no record — leaves the drain alone.
    """
    from gateway import autonomous_state

    record = autonomous_state.find_run_for_session(home, session_key)
    if record is not None and str(record.get("state")) in _AUTONOMOUS_AUTO_RESUME_BLOCK_STATES:
        return record
    return None


async def reset_autonomous_on_boot(runner) -> None:
    """Restart disables autonomous mode: clear the live flag and the leftover pin.

    Runs BEFORE the auto-resume drain in ``_start_finish_wiring``. The
    interrupted run is marked ``failed`` by :func:`gateway.autonomous_state.reset_on_boot`
    so the resume ask can find it; the leftover pinned message is unpinned +
    deleted through the delivery adapter resolved from the run's session
    (the drain's own route — never ``runner.adapters[platform]`` for a
    source). Best-effort throughout: a failure here must never abort startup.
    """
    try:
        from gateway import autonomous_state
        from hermes_constants import get_routing_process_hermes_home
        from plugins.platforms.telegram.autonomous_callback import unpin_and_delete_pinned

        home = get_routing_process_hermes_home()
        state = autonomous_state.load_state(home)
        pinned = autonomous_state.reset_on_boot(home)
        if pinned is None:
            return
        adapter = None
        run_id = state.get("current_run_id")
        if isinstance(run_id, str) and run_id:
            record = autonomous_state.load_run(home, run_id)
            if record is not None:
                entry = runner.session_store._entries.get(record.get("session_key", ""))
                if entry is not None:
                    source = runner._restored_source(entry)
                    if source is not None:
                        adapter = runner._delivery_adapter_for(source)
        if adapter is None:
            logger.warning(
                "Autonomous boot reset: no delivery adapter for the leftover pin "
                "(chat %s, message %s); the pin is left in place",
                pinned.get("chat_id"), pinned.get("pinned_message_id"),
            )
            return
        await unpin_and_delete_pinned(adapter, pinned)
    except Exception:
        logger.exception("Autonomous boot reset failed; the live flag is already cleared")

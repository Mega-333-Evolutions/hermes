"""``hm:autonomous:*`` callbacks: the Disable button on the pinned enable message, and the
Resume / Start fresh buttons on the resume-ask card.

Owner-only + DM-only, silent on failure: a tapper who is not the owner in
the owner's DM gets a bare answer (the loading spinner dismissed, nothing
said) — the mode's existence never leaks to anyone else. Disabling clears
the live flag, stops the run (the record is kept as the resume source),
and unpins + deletes the pinned message; the callback answer is the
confirmation, since the button lives on the message being deleted.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: DM chat types, same set as the admission-branch pattern in
#: ``plugins/platforms/telegram/telegram_controls.py``.
_AUTONOMOUS_DM_CHAT_TYPES = frozenset({"dm", "private", "direct"})


def clear_master_setting(home: Any) -> bool:
    """Clear the master ``platforms.telegram.autonomous_mode`` config key.

    Best-effort: a command-disable has already cleared the live flag and
    stopped the run by the time this runs, so a failed write must not
    turn the disable into an error — it is logged and the disable still
    stands. Returns whether the write landed. Both disable paths (the
    ``/autonomous disable`` handler and this callback) call it, so the
    settings row agrees with the live state after a command-disable.
    """
    try:
        from plugins.platforms.telegram.personal_settings_ui import TelegramPersonalSettingsUI

        TelegramPersonalSettingsUI._set_autonomous_mode(home, False)
    except Exception:
        logger.warning("Could not clear autonomous_mode setting on disable", exc_info=True)
        return False
    return True


async def unpin_and_delete_pinned(adapter: Any, pinned: Optional[dict[str, Any]]) -> None:
    """Unpin + delete a pinned message, best-effort; never raises."""
    if not pinned:
        return
    chat_id = pinned.get("chat_id")
    message_id = pinned.get("pinned_message_id")
    if chat_id is None or message_id is None:
        return
    try:
        await adapter.pin_chat_message(chat_id=chat_id, message_id=message_id, unpin=True)
    except Exception:
        logger.debug("Autonomous unpin failed", exc_info=True)
    try:
        await adapter.delete_message(str(chat_id), str(message_id))
    except Exception:
        logger.debug("Autonomous pinned-message delete failed", exc_info=True)


async def stop_run_for_stop(runner: Any, source: Any, *, stopped_by: str = "stop_command") -> None:
    """End the autonomous run after /stop, best-effort; never raises.

    Only the owner's DM can end the run this way: any other platform, chat
    type, or actor is a silent no-op (the mode's existence never leaks).
    The run record is kept as ``stopped`` (the resume source), the live
    flag clears, the pin is unpinned + deleted, and the master setting
    clears so the settings row agrees with the live state — the same
    end-state as /autonomous disable. When the mode is already off there
    is nothing to end and the config is left untouched.
    """
    try:
        from gateway import autonomous_state
        from hermes_constants import get_routing_process_hermes_home

        home = get_routing_process_hermes_home()
        if not autonomous_state.is_active(home):
            return
        raw_platform = getattr(source, "platform", None)
        platform = str(getattr(raw_platform, "value", raw_platform)).lower()
        if platform != "telegram":
            return
        actor_id = str(getattr(source, "user_id", "") or "")
        if (str(getattr(source, "chat_type", "")) not in _AUTONOMOUS_DM_CHAT_TYPES
                or str(getattr(source, "chat_id", "")) != actor_id
                or not actor_id):
            return
        adapter = runner._delivery_adapter_for(source)
        checker = getattr(adapter, "_is_owner_user", None) if adapter is not None else None
        if not callable(checker) or not checker(actor_id, source):
            return
        pinned = autonomous_state.disable(home, stopped_by=stopped_by)
        await unpin_and_delete_pinned(adapter, pinned)
        clear_master_setting(home)
    except Exception:
        logger.debug("Autonomous /stop cleanup failed", exc_info=True)


async def send_enable_card(adapter: Any, source: Any, *, reply_to: Any = None) -> Optional[str]:
    """Send the enable confirmation card; the sent message id, or None.

    The card body is Rich HTML, so the green tag rides as an in-body
    button row atop it; the Disable button is an inline-keyboard button
    (``hm:autonomous:disable``) carried by the same send. Shared by the
    ``/autonomous enable`` handler and the resume-ask choice taps.
    """
    from agent.i18n import t
    from plugins.platforms.telegram.command_cards import lines_to_rich_html
    from plugins.platforms.telegram.mtproto_rich import rich_button, rich_button_row

    tag_row = rich_button_row(
        [rich_button("autonomous", kind="disabled", style="success")])
    html = (tag_row + "\n" + lines_to_rich_html(
        t("gateway.autonomous.enabled_title"), [t("gateway.autonomous.enabled_body")]))
    send_card = getattr(adapter, "send_command_card", None)
    if not callable(send_card):
        return None
    try:
        return await send_card(
            source=source, html=html,
            buttons=(("Disable", "hm:autonomous:disable"),),
            reply_to=reply_to)
    except Exception:
        logger.debug("Autonomous enable card send failed", exc_info=True)
        return None


async def handle_autonomous_callback(adapter: Any, event: Any, source: Any, data: str) -> bool:
    """Handle ``hm:autonomous:*``. True when the callback was handled."""
    text = str(data or "")
    if text == "hm:autonomous:disable":
        return await _handle_autonomous_disable(adapter, event, source)
    if text.startswith("hm:autonomous:resume:"):
        return await _handle_autonomous_choice(
            adapter, event, source, text[len("hm:autonomous:resume:"):], fresh=False)
    if text.startswith("hm:autonomous:fresh:"):
        return await _handle_autonomous_choice(
            adapter, event, source, text[len("hm:autonomous:fresh:"):], fresh=True)
    return False


async def _handle_autonomous_disable(adapter: Any, event: Any, source: Any) -> bool:
    actor_id = str(getattr(source, "user_id", "") or "")
    if (str(getattr(source, "chat_type", "")) not in _AUTONOMOUS_DM_CHAT_TYPES
            or str(getattr(source, "chat_id", "")) != actor_id
            or not actor_id):
        await event.answer()
        return True
    checker = getattr(adapter, "_is_owner_user", None)
    if not callable(checker):
        await event.answer()
        return True
    try:
        if not checker(actor_id, source):
            await event.answer()
            return True
    except Exception:  # health: allow BLE001 -- the owner check is an adapter-provided callable; any failure denies silently
        await event.answer()
        return True

    from gateway import autonomous_state
    from hermes_constants import get_routing_process_hermes_home

    home = get_routing_process_hermes_home()
    pinned = autonomous_state.disable(home, stopped_by="autonomous_disable")
    await unpin_and_delete_pinned(adapter, pinned)
    clear_master_setting(home)
    await event.answer("Autonomous mode disabled.")
    return True


async def _handle_autonomous_choice(adapter: Any, event: Any, source: Any,
                                    run_id: str, *, fresh: bool) -> bool:
    """Resume-ask choice: Resume re-opens the old run, Start fresh starts a new one.

    Same gates as Disable (owner DM only, silent otherwise). The master
    setting flips on first — a failed write fails closed with a toast and
    starts nothing. The ask message is deleted, the enable card is sent +
    pinned, and the run record is the session source: the old record
    carries the owner's DM session key, so the resumed (or fresh) run
    attaches to the same session. A missing or no-longer-resumable record
    answers that the run is unavailable instead of starting anything.
    """
    from agent.i18n import t
    from gateway import autonomous_state
    from hermes_constants import get_routing_process_hermes_home

    actor_id = str(getattr(source, "user_id", "") or "")
    if (str(getattr(source, "chat_type", "")) not in _AUTONOMOUS_DM_CHAT_TYPES
            or str(getattr(source, "chat_id", "")) != actor_id
            or not actor_id):
        await event.answer()
        return True
    checker = getattr(adapter, "_is_owner_user", None)
    if not callable(checker):
        await event.answer()
        return True
    try:
        if not checker(actor_id, source):
            await event.answer()
            return True
    except Exception:  # health: allow BLE001 -- the owner check is an adapter-provided callable; any failure denies silently
        await event.answer()
        return True

    home = get_routing_process_hermes_home()
    record = autonomous_state.load_run(home, run_id)
    if record is None or str(record.get("state")) not in {"stopped", "failed", "needs_input"}:
        await event.answer(t("gateway.autonomous.resume_unavailable"))
        return True
    if not str(record.get("session_key") or ""):
        # A record without a session cannot attach a run to the owner's DM.
        await event.answer(t("gateway.autonomous.start_failed"))
        return True
    try:
        from plugins.platforms.telegram.personal_settings_ui import TelegramPersonalSettingsUI

        TelegramPersonalSettingsUI._set_autonomous_mode(home, True)
    except Exception:
        logger.warning("Could not persist autonomous_mode setting on resume choice", exc_info=True)
        await event.answer(t("gateway.autonomous.save_failed"))
        return True

    try:
        if fresh:
            new_record = autonomous_state.create_run(
                home, str(record.get("session_key") or ""), str(record.get("goal") or ""))
            autonomous_state.start_run(home, new_record["run_id"])
        else:
            new_record = autonomous_state.reopen_run(home, run_id)
    except Exception:
        logger.warning("Could not start the autonomous run from the resume choice", exc_info=True)
        await event.answer(t("gateway.autonomous.start_failed"))
        return True

    # The ask message has served its purpose; delete it best-effort.
    message_id = getattr(event, "message_id", None)
    if message_id is not None:
        try:
            await adapter.delete_message(str(source.chat_id), str(message_id))
        except Exception:
            logger.debug("Autonomous resume-ask delete failed", exc_info=True)
    card_id = await send_enable_card(adapter, source)
    if card_id is not None:
        try:
            await adapter.pin_chat_message(chat_id=source.chat_id, message_id=card_id)
        except Exception:
            logger.debug("Autonomous resume pin failed", exc_info=True)
        autonomous_state.set_active(home, True, run_id=new_record["run_id"],
                                    pinned_message_id=card_id, chat_id=source.chat_id)
        await event.answer()
    else:
        await event.answer(t("gateway.autonomous.status_on"))
    return True

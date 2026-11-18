"""Exec-approval card states for the Telegram adapter.

The guard's approval prompt is one card. While pending, its state rides on
a red in-body badge ("Awaiting approval") above the real action keyboard.
Once decided, the card is edited in place — no new "approved, now
proceeding" message — and the decided state rides on ONE re-attached
highlighted keyboard button (green "Command approved", red "Denied"): a
small badge, never a duplicate heading plus a full-width pill. Tapping the
settled button answers a plain toast naming the state. An approved card is
deleted a minute later so the group sees the decision without keeping the
clutter.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Any

logger = logging.getLogger(__name__)

#: How long an approved card stays visible after the edit, so the group sees
#: who approved what, before it is deleted (same transient treatment as
#: working messages).
APPROVED_CARD_DELETE_AFTER_S = 60.0

#: Badge ``(label, style)`` per card state.
_BADGES = {
    "pending": ("Awaiting approval", "danger"),
    "approved": ("Command approved", "success"),
    "denied": ("Denied", "danger"),
}


def _badge_row(state: str) -> str:
    """One disabled pill badge for *state* (``pending`` / ``approved`` / ``denied``)."""
    from .mtproto_rich import rich_button, rich_button_row

    label, style = _BADGES[state]
    return rich_button_row([rich_button(label, kind="disabled", style=style)])


def pending_approval_html(prompt_text: str) -> str:
    """Pending card: red badge on top, then the prompt text as rich HTML.

    The shared renderer builds the prompt as Markdown (header, fenced
    command, reason, deadline); the converter keeps the fence verbatim, so
    the command survives the trip, and the badge row on top makes the whole
    card take the HTML route.
    """
    from .command_cards import lines_to_rich_html

    body = lines_to_rich_html("", str(prompt_text or "").split("\n"))
    return _badge_row("pending") + "\n" + body


def settled_approval_markup(*, approved: bool, approval_id=None) -> Any:
    """One highlighted keyboard button carrying the decided state.

    The settled card's badge is a real inline button, not an in-body pill:
    green "Command approved" or red "Denied". Its callback (``ea:settled-…``)
    is answered with a plain toast naming the state — the approval is
    already decided and its state popped, so there is nothing to resolve.
    """
    from .mtproto_rich import inline_markup

    state = "approved" if approved else "denied"
    label, style = _BADGES[state]
    return inline_markup([[(label, f"ea:settled-{state}:{approval_id or ''}", style)]])


def settled_approval_html(
    *, approved: bool, command: str = "", approver_name: str = "", approver_id: str = "",
) -> str:
    """Decided card body: the command + ``Approved/Denied by <person>``.

    No heading and no in-body pill — the state lives on the re-attached
    highlighted keyboard button (see ``settled_approval_markup``), so the
    card stays a small badge instead of a full-width bar. A denied card
    never looks approved: "Denied by" and no green anywhere.
    """
    from .mtproto_rich import rich_button

    verb = "Approved by" if approved else "Denied by"
    parts = []
    if str(command or "").strip():
        parts.append(f"<pre>{escape(str(command))}</pre>")
    name = " ".join(str(approver_name or "").split()) or str(approver_id or "")
    if str(approver_id or "").strip().isdecimal():
        person = rich_button(name or str(approver_id), kind="profile", value=str(approver_id).strip())
    elif name:
        person = escape(name)
    else:
        person = "the owner"
    parts.append(f"<p>{verb} {person}</p>")
    return "\n".join(parts)


async def settle_approval_card(
    adapter, *, chat_id: str, message_id, command: str, choice: str,
    approver_name: str = "", approver_id: str = "", approval_id=None,
) -> bool:
    """Edit a decided approval card in place (see ``settled_approval_html``);
    the state rides on the re-attached highlighted keyboard button, and an
    approved card is deleted a minute later so the group sees the decision
    without keeping the clutter. False when the edit itself failed."""
    from .message_self_delete import schedule_sent_message_self_delete
    from .mtproto_rich import edit_request

    approved = str(choice) != "deny"
    html = settled_approval_html(
        approved=approved, command=str(command or ""),
        approver_name=str(approver_name or ""), approver_id=str(approver_id or ""))
    try:
        await adapter._mtproto_rich_request(edit_request(
            adapter._peer_for(str(chat_id)), int(message_id), html,
            reply_markup=settled_approval_markup(
                approved=approved, approval_id=approval_id),
        ))
    except Exception:
        logger.debug("Could not update the handled Telegram approval card", exc_info=True)
        return False
    if approved:
        schedule_sent_message_self_delete(
            adapter, {"telegram_self_delete_after_s": APPROVED_CARD_DELETE_AFTER_S},
            str(chat_id), message_id)
    return True


async def settle_exec_approval_cards(
    adapter, session_key: str, request_ids, choice: str, *,
    approver_name: str = "", approver_id: str = "",
) -> int:
    """Edit pending approval cards in place for approvals answered by typing
    instead of tapping (``/approve`` / ``/deny``). Matches the pending
    entries by request id within the session; cards the adapter never sent
    (or already settled) settle nothing, and the caller then falls back to
    its text confirmation. Returns how many cards were edited."""
    if adapter._client is None:
        return 0
    wanted = {str(rid) for rid in (request_ids or [])}
    if not wanted:
        return 0
    settled = 0
    for approval_id, state in list(adapter._approval_state.items()):
        if str(state.get("session_key") or "") != str(session_key):
            continue
        if str(state.get("request_id") or "") not in wanted:
            continue
        message_id = state.get("message_id")
        adapter._approval_state.pop(approval_id, None)
        if not message_id:
            continue
        if await settle_approval_card(
            adapter, chat_id=str(state.get("chat_id") or ""), message_id=message_id,
            command=str(state.get("command") or ""), choice=choice,
            approver_name=str(approver_name or ""), approver_id=str(approver_id or ""),
            approval_id=approval_id,
        ):
            settled += 1
    return settled

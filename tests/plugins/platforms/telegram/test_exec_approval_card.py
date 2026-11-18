"""Regression tests for the exec-approval card states (item 7).

Approving edits the original card in place — never a new "approved, now
proceeding" message. While pending, the card carries a red in-body badge
("Awaiting approval") above the action keyboard. Once decided, the state
rides on ONE re-attached highlighted keyboard button — green "Command
approved", red "Denied" — with no duplicate heading and no full-width pill,
and "Approved/Denied by <person>" at the bottom. Tapping the settled button
answers a plain toast naming the state. An approved card is deleted a minute
later.
"""

import itertools
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gateway.platforms.base import Platform
from gateway.platforms.base_exec_approval import render_exec_approval
from plugins.platforms.telegram import exec_approval_card as card
from plugins.platforms.telegram import telethon_adapter as adapter_module
from plugins.platforms.telegram.telethon_adapter import TelethonAdapter

COMMAND = "rm -rf /tmp/stale-cache && systemctl restart hermes"
DESCRIPTION = "dangerous command: removes files outside the project"
CHAT_ID = "-1001234567890"
OWNER_ID = "6802929470"


class FakeClient:
    def __init__(self):
        self.sent_messages = []

    async def __call__(self, request):
        self.sent_messages.append(request)
        return SimpleNamespace(id=101)


def _pending_text(adapter):
    text, _truncated = render_exec_approval(adapter, COMMAND, DESCRIPTION, False, True)
    return text


@pytest.fixture
def adapter():
    adapter = TelethonAdapter.__new__(TelethonAdapter)
    adapter._client = FakeClient()
    adapter.config = SimpleNamespace(extra={})
    adapter.platform = Platform.TELEGRAM
    adapter.gateway_runner = Mock()
    adapter._approval_state = {}
    adapter._approval_counter = itertools.count(1)
    adapter._peer_for = lambda _chat_id: f"peer:{_chat_id}"
    adapter._authorized = lambda _src: True
    adapter._settings_home = lambda _src: "/tmp/test-home"
    adapter._sent_message_id = lambda res, **kw: str(res.id)
    return adapter


def _pending_prompt(adapter):
    return SimpleNamespace(
        text=_pending_text(adapter),
        actions=[("Allow Once", "once", "primary"), ("Allow Session", "session", None),
                 ("Always Allow", "always", None), ("Deny", "deny", "danger")],
        session_key="agent:main:telegram:group:-1001234567890",
        chat_id=CHAT_ID,
        metadata={"owner_user_id": OWNER_ID, "profile_home": "/tmp/test-home",
                  "user_id": OWNER_ID},
        request_id="req-1",
        command=COMMAND,
    )


def _approval_state(message_id="7"):
    return {
        "session_key": "agent:main:telegram:group:-1001234567890",
        "request_id": "req-1",
        "chat_id": CHAT_ID,
        "thread_id": None,
        "profile_home": "/tmp/test-home",
        "owner_user_id": OWNER_ID,
        "intended_user_id": OWNER_ID,
        "owner_session": True,
        "allowed_choices": {"once", "session", "always", "deny"},
        "created_at": time.monotonic(),
        "message_id": message_id,
        "command": COMMAND,
    }


def _tap_event(message_id=7):
    sender = SimpleNamespace(id=int(OWNER_ID), first_name="Kailash", last_name=None,
                             username="kailashhh", bot=False)
    chat = SimpleNamespace(broadcast=False, megagroup=True, gigagroup=False, forum=False,
                           first_name=None, title="Test Group")
    message = SimpleNamespace(id=message_id, reply_to=None, action=None)
    return SimpleNamespace(message_id=message_id, chat_id=CHAT_ID, sender=sender,
                           chat=chat, message=message)


# ── Pure card builders ────────────────────────────────────────────────

def test_pending_card_has_red_badge_and_keeps_the_command(adapter):
    """The pending card shows a red badge and keeps header, command, reason, deadline."""
    html = card.pending_approval_html(_pending_text(adapter))
    assert html.startswith("<tg-button-row>")
    assert "Awaiting approval" in html
    assert 'style="danger"' in html
    assert 'style="success"' not in html
    assert "Hermes wants to run a command that needs your OK" in html
    assert "<pre>rm -rf /tmp/stale-cache &amp;&amp; systemctl restart hermes</pre>" in html
    assert "Why it was flagged: dangerous command" in html
    assert "If you don't answer within 5 minutes it will NOT run." in html


def test_settled_approved_card_has_no_heading_or_inbody_pill():
    """Approved: no duplicate heading, no full-width in-body pill — the state
    rides on the re-attached highlighted keyboard button, not a huge bar.
    The body keeps the command and the approver as a profile pill."""
    html = card.settled_approval_html(
        approved=True, command=COMMAND, approver_name="Kailash", approver_id=OWNER_ID)
    assert "<h1>" not in html
    assert "<tg-button-row>" not in html
    assert 'style="success"' not in html
    assert "<pre>rm -rf /tmp/stale-cache &amp;&amp; systemctl restart hermes</pre>" in html
    assert "Approved by" in html
    assert f'tg://user?id={OWNER_ID}' in html
    assert "Kailash" in html
    assert "Denied" not in html


def test_settled_denied_card_has_no_heading_or_inbody_pill():
    """Denied: no duplicate heading, no in-body pill, no green anywhere, no
    'approved' wording — the red state button carries the verdict."""
    html = card.settled_approval_html(
        approved=False, command=COMMAND, approver_name="Kailash", approver_id=OWNER_ID)
    assert "<h1>" not in html
    assert "<tg-button-row>" not in html
    assert 'style="danger"' not in html
    assert 'style="success"' not in html
    assert "Command approved" not in html
    assert "Approved by" not in html
    assert "Denied by" in html


def test_settled_card_without_command_or_name():
    """No command: no <pre>. No name or id: the approver falls back to the owner."""
    html = card.settled_approval_html(approved=True)
    assert "<pre>" not in html
    assert "Approved by the owner" in html


# ── Adapter: tap path ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tap_approve_reattaches_highlighted_state_button(adapter, monkeypatch):
    """A tap settles the card with a small highlighted keyboard button carrying
    the state (green "Command approved") — no duplicate heading, no in-body
    pill — and still schedules the 1-minute delete."""
    answers = []
    deletions = []

    async def _answer(event, text=None, show_alert=False):
        answers.append({"text": text, "alert": show_alert})

    def _schedule_delete(_adapter, metadata, chat_id, message_id):
        deletions.append({"delay": metadata.get("telegram_self_delete_after_s"),
                          "chat_id": chat_id, "message_id": message_id})

    monkeypatch.setattr(adapter_module, "answer_callback", _answer)
    monkeypatch.setattr(
        "plugins.platforms.telegram.message_self_delete.schedule_sent_message_self_delete",
        _schedule_delete)
    monkeypatch.setattr("tools.approval.resolve_gateway_approval", lambda *a, **k: 1)
    adapter._approval_state[1] = _approval_state()

    await adapter._handle_exec_approval_callback(_tap_event(), "ea:once:1")

    assert 1 not in adapter._approval_state  # popped: a second tap cannot re-resolve
    assert len(adapter._client.sent_messages) == 1  # one edit, no new message
    request = adapter._client.sent_messages[0]
    assert request.id == 7
    assert "<h1>" not in request.message
    assert "<tg-button-row>" not in request.message
    assert request.reply_markup is not None
    button = request.reply_markup.rows[0].buttons[0]
    assert button.text == "Command approved"
    assert button.style is not None and button.style.bg_success is True
    assert button.type.data == b"ea:settled-approved:1"
    assert "Approved by" in request.message and "Kailash" in request.message
    assert f'tg://user?id={OWNER_ID}' in request.message
    assert request.rich_message is not None
    assert answers and answers[-1]["text"] == "Processing approval…"
    assert deletions == [{"delay": 60.0, "chat_id": CHAT_ID, "message_id": 7}]


@pytest.mark.asyncio
async def test_tap_on_settled_button_answers_the_state_without_alert(adapter, monkeypatch):
    """Tapping the settled state button answers a plain toast naming the
    state — never the expired-approval alert — and resolves nothing."""
    answers = []

    async def _answer(event, text=None, show_alert=False):
        answers.append({"text": text, "alert": show_alert})

    monkeypatch.setattr(adapter_module, "answer_callback", _answer)
    # No approval state at all: the card was already settled and popped.
    await adapter._handle_exec_approval_callback(_tap_event(), "ea:settled-approved:1")
    assert answers == [{"text": "Command approved", "alert": False}]
    assert adapter._client.sent_messages == []

    await adapter._handle_exec_approval_callback(_tap_event(), "ea:settled-denied:1")
    assert answers[-1] == {"text": "Command denied", "alert": False}
    assert adapter._client.sent_messages == []


@pytest.mark.asyncio
async def test_tap_deny_edits_card_in_place_without_delete(adapter, monkeypatch):
    """A deny edits the same card to the denied state — red "Denied" state
    button, no heading, no pill — and leaves it standing."""
    deletions = []

    async def _answer(event, text=None, show_alert=False):
        return None

    monkeypatch.setattr(adapter_module, "answer_callback", _answer)
    monkeypatch.setattr(
        "plugins.platforms.telegram.message_self_delete.schedule_sent_message_self_delete",
        lambda *a: deletions.append(a))
    monkeypatch.setattr("tools.approval.resolve_gateway_approval", lambda *a, **k: 1)
    adapter._approval_state[1] = _approval_state()

    await adapter._handle_exec_approval_callback(_tap_event(), "ea:deny:1")

    assert len(adapter._client.sent_messages) == 1
    request = adapter._client.sent_messages[0]
    assert "<h1>" not in request.message
    assert "<tg-button-row>" not in request.message
    assert 'style="success"' not in request.message
    assert request.reply_markup is not None
    button = request.reply_markup.rows[0].buttons[0]
    assert button.text == "Denied"
    assert button.style is not None and button.style.bg_danger is True
    assert button.type.data == b"ea:settled-denied:1"
    assert "Denied by" in request.message
    assert deletions == []


@pytest.mark.asyncio
async def test_pending_send_carries_red_badge_and_stores_command(adapter):
    """The pending card goes out as rich HTML with the red badge, keyboard intact."""
    result = await adapter._send_exec_approval_prompt(_pending_prompt(adapter))

    assert result.success
    assert len(adapter._client.sent_messages) == 1
    request = adapter._client.sent_messages[0]
    assert "Awaiting approval" in request.message
    assert 'style="danger"' in request.message
    assert "<pre>rm -rf /tmp/stale-cache" in request.message
    assert request.reply_markup is not None  # the four action buttons ride along
    assert request.rich_message is not None
    state = adapter._approval_state[1]
    assert state["command"] == COMMAND  # the decided card records what was approved


# ── Adapter: typed /approve settle ────────────────────────────────────

@pytest.mark.asyncio
async def test_typed_settle_edits_only_the_matching_card(adapter, monkeypatch):
    """Typed settle edits the card whose request id matches, in its own chat only."""
    deletions = []
    monkeypatch.setattr(
        "plugins.platforms.telegram.message_self_delete.schedule_sent_message_self_delete",
        lambda *a: deletions.append(a))
    adapter._approval_state[1] = _approval_state(message_id="11")
    other = _approval_state(message_id="22")
    other["session_key"] = "agent:main:telegram:group:-1009999999999"
    other["chat_id"] = "-1009999999999"
    adapter._approval_state[2] = other

    settled = await adapter.settle_exec_approval_cards(
        "agent:main:telegram:group:-1001234567890", ["req-1"], "once",
        approver_name="Kailash", approver_id=OWNER_ID)

    assert settled == 1
    assert 1 not in adapter._approval_state
    assert 2 in adapter._approval_state  # another session's card is untouched
    assert len(adapter._client.sent_messages) == 1
    request = adapter._client.sent_messages[0]
    assert request.id == 11
    assert "<h1>" not in request.message
    assert "<tg-button-row>" not in request.message
    assert request.reply_markup is not None
    button = request.reply_markup.rows[0].buttons[0]
    assert button.text == "Command approved"
    assert button.style is not None and button.style.bg_success is True
    assert button.type.data == b"ea:settled-approved:1"
    assert deletions and deletions[0][2] == "-1001234567890"

    settled = await adapter.settle_exec_approval_cards(
        "agent:main:telegram:group:-1001234567890", ["req-9"], "once",
        approver_name="Kailash", approver_id=OWNER_ID)
    assert settled == 0
    assert len(adapter._client.sent_messages) == 1  # nothing else was edited

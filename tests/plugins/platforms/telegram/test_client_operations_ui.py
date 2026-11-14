"""Expired vs mismatched client-operations callbacks answer differently.

A callback whose token is gone (consumed, or reaped past CONFIRM_TTL) is
expired; a callback whose token is still live but bound to another
message/actor/chat/thread/profile mismatches — and the live token survives
for a correct retry.
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from plugins.platforms.telegram.client_operations_ui import (
    CONFIRM_TTL,
    TelegramClientOperationsUI,
)


@pytest.fixture
def ui(tmp_path):
    adapter = SimpleNamespace(
        _client=object(),
        _authorized=lambda _source: True,
        _is_owner_user=lambda _user, source=None, **_kw: True,
        _settings_home=lambda _source: tmp_path / "alpha",
        _input_peer_for=AsyncMock(return_value="resolved-peer"),
        _mtproto_rich_request=AsyncMock(return_value=SimpleNamespace(updates=[])),
        _sent_message_id=lambda *args, **kwargs: 555,
        _message_thread_id=lambda _message: None,
    )
    return TelegramClientOperationsUI(adapter)


def _source():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="-10042", chat_type="group",
                         user_id="100", thread_id="77")


class _Event:
    def __init__(self, message_id="500"):
        self.message_id = message_id
        self.answers = []

    async def answer(self, text=None, alert=False):
        self.answers.append((text or "", alert))


def _seed(ui, token, *, message_id="500", age=0.0):
    state = ui._new_state(_source(), token, "confirm", action="send_message", params={})
    state["message_id"] = message_id
    if age:
        state["created_at"] = time.monotonic() - age
    ui._states[token] = state
    return state


@pytest.mark.asyncio
async def test_aged_confirmation_callback_is_expired_and_reaped(ui):
    _seed(ui, "tok-exp", age=CONFIRM_TTL + 20)
    event = _Event(message_id="500")

    assert await ui.handle_callback(event, _source(), "tgo:c:tok-exp:yes") is True
    assert event.answers == [("This card expired.", True)]
    assert "tok-exp" not in ui._states


@pytest.mark.asyncio
async def test_live_confirmation_callback_from_another_message_is_mismatched_and_survives(ui):
    _seed(ui, "tok-live")
    event = _Event(message_id="999")

    assert await ui.handle_callback(event, _source(), "tgo:c:tok-live:yes") is True
    assert event.answers == [("This card does not match this message.", False)]
    assert "tok-live" in ui._states

    # The token survives a mismatched tap: the bound message still works.
    event = _Event(message_id="500")
    assert await ui.handle_callback(event, _source(), "tgo:c:tok-live:yes") is True
    assert event.answers[0][0] == "Rechecking authorization and applying the confirmed payload."

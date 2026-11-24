"""Repro (streamed lane): footer ON + beta ON renders NOTHING.

Live 2026-11-24: /footer on, beta footer on in /settings, "Hey" -> reply
"Hey" with no footer at all. The non-streamed final-send path carries the
badges (proven by the sibling repro), so the streamed lane is the suspect:
the trailing footer must send the footer line with the badge keyboard.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _source():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="123", chat_type="dm",
                         user_id="6802929470", is_bot=False)


def _real_adapter():
    """Real TelethonAdapter whose MTProto client is a recorder."""
    from plugins.platforms.telegram.telethon_adapter import TelethonAdapter

    adapter = TelethonAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))

    class Client:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return SimpleNamespace(id=777)

    adapter._client = Client()
    return adapter


BADGES = [("gpt-6-luna", "fb:m:openrouter|openrouter/gpt-6-luna"),
          ("9%", "fb:c:8000|272000"),
          ("~", "fb:p:~")]


@pytest.mark.asyncio
async def test_streamed_turn_with_badges_sends_trailing_footer_with_keyboard():
    """A streamed turn with badges on the event must send the trailing footer
    line WITH the badge keyboard. Regression: live it sent nothing."""
    from gateway.run import GatewayRunner

    adapter = _real_adapter()
    runner = object.__new__(GatewayRunner)
    runner._delivery_adapter_for = MagicMock(return_value=adapter)
    runner._should_send_voice_reply = MagicMock(return_value=False)
    event = MessageEvent(text="Hey", source=_source(), message_id="101")
    event._footer_badges = list(BADGES)
    session_entry = SimpleNamespace(session_id="s1")
    agent_result = {"already_sent": True, "model": "openrouter/gpt-6-luna",
                    "provider": "openrouter", "last_prompt_tokens": 8000,
                    "context_length": 272000}
    out = await runner._hmwa_deliver_turn_response(
        event, _source(), session_entry, "agent:main:telegram:dm:123", 1,
        agent_result, [], "Hey \U0001f60a I'm here.",
        "gpt-6-luna · 9% · ~", False)
    assert out is None  # streamed body already delivered; no duplicate send
    assert len(adapter._client.requests) == 1, (
        "trailing footer was never sent: footer ON + beta ON renders nothing")
    request = adapter._client.requests[0]
    assert "gpt-6-luna" in request.message
    assert request.reply_markup is not None, "trailing footer has no badge keyboard"
    buttons = request.reply_markup.rows[0].buttons
    assert [button.text for button in buttons] == ["gpt-6-luna", "9%", "~"]


@pytest.mark.asyncio
async def test_streamed_turn_with_badges_falls_back_to_plain_footer_when_keyboard_send_fails():
    """Footer ON + beta ON, streamed turn, live shape: the badge-keyboarded
    trailing send is refused server-side (``BUTTON_DATA_INVALID``) — the
    footer line must still land as plain text. Regression: the refused send
    was swallowed at DEBUG and the user got nothing at all."""
    from telethon.errors.rpcerrorlist import ButtonDataInvalidError

    from gateway.run import GatewayRunner

    adapter = _real_adapter()

    class RejectingClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            if getattr(request, "reply_markup", None) is not None:
                raise ButtonDataInvalidError(request=request)
            return SimpleNamespace(id=778)

    adapter._client = RejectingClient()
    runner = object.__new__(GatewayRunner)
    runner._delivery_adapter_for = MagicMock(return_value=adapter)
    runner._should_send_voice_reply = MagicMock(return_value=False)
    event = MessageEvent(text="Hey", source=_source(), message_id="101")
    event._footer_badges = list(BADGES)
    session_entry = SimpleNamespace(session_id="s1")
    agent_result = {"already_sent": True, "model": "openrouter/gpt-6-luna",
                    "provider": "openrouter", "last_prompt_tokens": 8000,
                    "context_length": 272000}
    out = await runner._hmwa_deliver_turn_response(
        event, _source(), session_entry, "agent:main:telegram:dm:123", 1,
        agent_result, [], "Hey \U0001f60a I'm here.",
        "gpt-6-luna · 9% · ~", False)
    assert out is None
    plain = [req for req in adapter._client.requests
             if getattr(req, "reply_markup", None) is None]
    assert plain, (
        "footer ON + beta ON rendered nothing: the keyboarded send failed "
        "and no plain-text fallback was sent")
    assert "gpt-6-luna" in plain[-1].message

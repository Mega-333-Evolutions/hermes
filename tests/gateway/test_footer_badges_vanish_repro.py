"""Repro: footer ON + beta ON renders NOTHING on a real (non-streamed) final send.

Live 2026-11-24: /footer on, beta footer on in /settings, "Hey" -> reply
"Hey" with no footer at all — no text footer, no badge buttons. The text
footer is suppressed (badges should replace it) and the badges never render.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import delivery_ledger as dl
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from tests.fixtures.storage_holder import raw_holder
from tests.gateway.fixtures.storage_handles import close_storage_handles, storage_handle
from persistence_assembly import profile_scope


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(dl, "_scope", lambda: profile_scope(home=home))
    monkeypatch.setattr(dl, "_owner_stamp", lambda: (os.getpid(), 202))
    monkeypatch.setattr(dl, "ledger_enabled", lambda config=None: True)
    yield


def _source():
    return SimpleNamespace(platform=Platform.TELEGRAM, chat_id="123", thread_id=None,
                           chat_type="dm", parent_chat_id=None, user_id="6802929470")


def _real_adapter():
    """Real TelethonAdapter whose MTProto client is a recorder (like
    tests/plugins/platforms/telegram/test_footer_badges.py)."""
    from plugins.platforms.telegram.telethon_adapter import TelethonAdapter

    adapter = TelethonAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))

    class Client:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return SimpleNamespace(id=777)

    adapter._client = Client()
    runner = MagicMock()
    runner._delivery_adapter_for = MagicMock(return_value=adapter)
    runner._schedule_flood_redelivery = MagicMock(return_value=187.0)
    adapter.gateway_runner = runner
    adapter.edit_message = AsyncMock(
        return_value=SendResult(success=False, error="message to edit not found"))
    return adapter


BADGES = [("gpt-6-luna", "fb:m:openrouter|openrouter/gpt-6-luna"),
          ("9%", "fb:c:8000|272000"),
          ("~", "fb:p:~")]


@pytest.mark.asyncio
async def test_nonstreamed_final_send_with_badges_on_event_carries_the_keyboard():
    """Badges set on the event by the turn runner must reach the adapter as
    the keyboard on the final message. Regression: live they vanished."""
    adapter = _real_adapter()
    event = MessageEvent(text="Hey", source=_source(), message_id="101")
    event._footer_badges = list(BADGES)
    await adapter._send_final_text(
        event, "agent:main:telegram:dm:123", "Hey \U0001f60a I'm here.",
        {}, False, 0, lambda _r: None)
    assert len(adapter._client.requests) == 1
    request = adapter._client.requests[0]
    assert request.reply_markup is not None, "badge keyboard missing from the final send"
    buttons = request.reply_markup.rows[0].buttons
    assert [button.text for button in buttons] == ["gpt-6-luna", "9%", "~"]

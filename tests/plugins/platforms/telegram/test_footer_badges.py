"""Footer-badge taps answer their detail toast — for anyone, with no guard.

The badges sit on a message anyone in the chat can already see, so the tap
carries no owner, user, group or admission check: click and you get the
answer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.callback_answer import answer_footer_badge_callback
from plugins.platforms.telegram.telethon_adapter import TelethonAdapter


class _Event:
    """Minimal Telethon callback-query event: records every answer."""

    def __init__(self, data, sender_id=1, chat_id=1, message_id=101):
        self.data = data
        self.message_id = message_id
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.sender = SimpleNamespace(id=sender_id, first_name="Mallory", bot=False)
        self.chat = SimpleNamespace(id=chat_id, first_name="Mallory")
        self.message = SimpleNamespace(id=101, chat_id=chat_id, reply_to=None)
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def _adapter():
    adapter = TelethonAdapter(PlatformConfig(enabled=True, token="t"))

    class Client:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return SimpleNamespace(id=777)

    adapter._client = Client()
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("data,toast", [
    ("fb:m:agentrouter|nousresearch/hermes-4-405b",
     "nousresearch/hermes-4-405b · agentrouter"),
    ("fb:m:|hermes-4-405b", "hermes-4-405b"),
    ("fb:c:38400|256000", "38,400 tokens of 256,000 tokens"),
    ("fb:s:nousresearch/hermes-4-405b|hermes-4-405b-prod",
     "requested nousresearch/hermes-4-405b, served as hermes-4-405b-prod"),
    ("fb:l:22s", "turn took 22s"),
    ("fb:p:/data/home/.hermes", "/data/home/.hermes"),
])
async def test_footer_badge_answers_its_detail(data, toast):
    event = _Event(data)
    assert await answer_footer_badge_callback(event, data) is True
    assert event.answers == [((toast,), {})]


@pytest.mark.asyncio
async def test_footer_badge_cwd_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    event = _Event("fb:p:~/projects/hermes")
    assert await answer_footer_badge_callback(event, "fb:p:~/projects/hermes") is True
    assert event.answers == [((str(tmp_path / "projects" / "hermes"),), {})]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    "fb:x:whatever", "fb:c:not-a-number|256000", "fb:m:", "badge:pending", "",
])
async def test_footer_badge_unknown_or_malformed_answers_empty(data):
    event = _Event(data)
    assert await answer_footer_badge_callback(event, data) is True
    assert event.answers == [((), {})]


@pytest.mark.asyncio
async def test_footer_badge_tap_dispatched_through_callback_query():
    adapter = _adapter()
    event = _Event("fb:m:agentrouter|nousresearch/hermes-4-405b")
    await adapter._on_callback_query(event)
    assert event.answers == [(("nousresearch/hermes-4-405b · agentrouter",), {})]


def _unauthorized_group_stubs(monkeypatch, adapter):
    """Multiuser on; the tapper is a group member the bot never admitted."""
    import gateway.mongo_multiuser as multiuser
    monkeypatch.setattr(multiuser, "enabled", lambda home: True)

    async def _group_source(_event):
        return SimpleNamespace(user_id=999, chat_type="group", chat_id=-100123,
                               is_bot=False, thread_id=None)

    monkeypatch.setattr(adapter, "_source", _group_source)
    monkeypatch.setattr(adapter, "_authorized", lambda _source: False)
    monkeypatch.setattr(adapter, "_multiuser_home", lambda _source: "/tmp/home")


@pytest.mark.asyncio
async def test_footer_badge_tap_by_unauthorized_group_member_still_answers(monkeypatch):
    """No owner/user/group/admission guard: a never-admitted group member
    tapping a badge on a message they can see still gets the answer."""
    adapter = _adapter()
    _unauthorized_group_stubs(monkeypatch, adapter)
    event = _Event("fb:m:agentrouter|nousresearch/hermes-4-405b",
                   sender_id=999, chat_id=-100123)
    await adapter._on_callback_query(event)
    assert event.answers == [(("nousresearch/hermes-4-405b · agentrouter",), {})]


@pytest.mark.asyncio
async def test_gate_still_refuses_other_callbacks_for_the_same_user(monkeypatch):
    """The bypass is fb:-only: the same unauthorized member tapping a
    session control still gets the refusal alert."""
    adapter = _adapter()
    _unauthorized_group_stubs(monkeypatch, adapter)
    event = _Event("ms:model", sender_id=999, chat_id=-100123)
    await adapter._on_callback_query(event)
    assert event.answers == [(("This control is not authorized for your account or chat scope.",),
                              {"alert": True})]


@pytest.mark.asyncio
async def test_send_attaches_footer_badges_to_the_last_part_only():
    adapter = _adapter()
    badges = [["hermes-4-405b", "fb:m:agentrouter|nousresearch/hermes-4-405b"],
              ["15%", "fb:c:38400|256000"]]
    result = await adapter.send("42", "The answer.", metadata={"footer_badges": badges})
    assert result.success is True
    assert len(adapter._client.requests) == 1
    request = adapter._client.requests[0]
    assert type(request).__name__ == "SendMessageRequest"
    rows = request.reply_markup.rows
    assert len(rows) == 1
    buttons = rows[0].buttons
    assert [button.text for button in buttons] == ["hermes-4-405b", "15%"]
    assert [button.type.data for button in buttons] == [
        b"fb:m:agentrouter|nousresearch/hermes-4-405b", b"fb:c:38400|256000"]


@pytest.mark.asyncio
async def test_send_attaches_footer_badges_only_to_the_final_part():
    adapter = _adapter()
    badges = [["hermes-4-405b", "fb:m:agentrouter|nousresearch/hermes-4-405b"]]
    long_text = "word " * 8000  # over one 32k message part, so the reply splits
    result = await adapter.send("42", long_text, metadata={"footer_badges": badges})
    assert result.success is True
    assert len(adapter._client.requests) > 1
    assert all(request.reply_markup is None for request in adapter._client.requests[:-1])
    last = adapter._client.requests[-1]
    assert [button.text for button in last.reply_markup.rows[0].buttons] == ["hermes-4-405b"]


@pytest.mark.asyncio
async def test_send_without_badges_attaches_no_keyboard():
    adapter = _adapter()
    result = await adapter.send("42", "The answer.")
    assert result.success is True
    assert len(adapter._client.requests) == 1
    assert adapter._client.requests[0].reply_markup is None


class _SettingsAdapter:
    """Minimal adapter for the settings card: records rich requests."""

    def __init__(self):
        self._client = object()
        self.edits = []
        self._next_message_id = 100

    def _peer_for(self, chat_id):
        return chat_id

    async def _mtproto_rich_request(self, request):
        self.edits.append(request)
        self._next_message_id += 1
        return SimpleNamespace(id=self._next_message_id)

    @staticmethod
    def _sent_message_id(response):
        return str(response.id)


@pytest.mark.asyncio
async def test_settings_toggle_flips_footer_badges_through_the_card(tmp_path):
    """The Footer badges row on the settings card is a live toggle: tapping
    it flips the persisted setting — on, then off again on a fresh card."""
    from plugins.platforms.telegram.personal_settings_ui import TelegramPersonalSettingsUI

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("platforms:\n  telegram: {}\n", encoding="utf-8")
    adapter = _SettingsAdapter()
    adapter.gateway_runner = SimpleNamespace(
        _resolve_profile_home_for_source=lambda _source: home,
    )
    ui = TelegramPersonalSettingsUI(adapter)
    source = SimpleNamespace(chat_id="123", user_id="123", user_name="Test User",
                             thread_id=None, chat_type="dm", profile=None)
    state = ui._new_state(source, home, message_id=55)
    state["user_name"] = source.user_name
    assert TelegramPersonalSettingsUI._footer_badges_enabled(str(home)) is False
    data = f"ps:{state['id']}:footer-badges-toggle"
    assert await ui.handle_callback(_Event(data, message_id=55), source, data)
    assert TelegramPersonalSettingsUI._footer_badges_enabled(str(home)) is True
    # The card rotates its callback state on every tap, so the off-tap goes
    # through a fresh card.
    state = ui._new_state(source, home, message_id=56)
    state["user_name"] = source.user_name
    data = f"ps:{state['id']}:footer-badges-toggle"
    assert await ui.handle_callback(_Event(data, message_id=56), source, data)
    assert TelegramPersonalSettingsUI._footer_badges_enabled(str(home)) is False

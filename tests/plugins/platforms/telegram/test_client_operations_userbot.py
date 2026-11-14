"""Owner-only user-account helper: import, status and Telethon script execution."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("telethon")

import telethon
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from gateway.config import Platform
from gateway.session import SessionSource
from plugins.platforms.telegram.client_operations import TelegramClientOperations


class FakeTelegramClient:
    instances = []

    def __init__(self, session, api_id, api_hash, **kwargs):
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self.connected = False
        self.disconnected = False
        self.me = SimpleNamespace(id=321, username="helper", first_name="Helper", bot=False)
        FakeTelegramClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return self.me

    async def disconnect(self):
        self.disconnected = True

    async def ping(self):
        return "pong"


@pytest.fixture
def ops(tmp_path, monkeypatch):
    FakeTelegramClient.instances = []
    monkeypatch.setattr(telethon, "TelegramClient", FakeTelegramClient)
    monkeypatch.setattr(
        "agent.secret_scope.get_secret",
        lambda name, default=None: {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "abc123"}.get(name, default),
    )
    adapter = SimpleNamespace(
        _client=object(),
        _authorized=lambda _source: True,
        _is_owner_user=lambda _user, source=None, **_kw: True,
        _settings_home=lambda _source: tmp_path / "alpha",
        _input_peer_for=AsyncMock(return_value="resolved-peer"),
        _mtproto_rich_request=AsyncMock(return_value=SimpleNamespace(updates=[])),
        _message_thread_id=lambda _message: None,
        _bot_username="hermes_bot",
        _bot_id=777,
    )
    return TelegramClientOperations(adapter), adapter, tmp_path


def _source(user_id="100"):
    return SessionSource(platform=Platform.TELEGRAM, chat_id="-10042", chat_type="group",
                         user_id=user_id, thread_id="77")


def _session_string() -> str:
    """A structurally valid Telethon StringSession; the fake client owns auth."""
    session = StringSession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(b"\x01" * 256)
    return session.save()


def _session_path(tmp_path):
    return tmp_path / "alpha" / "telegram-userbot" / "session"


@pytest.mark.asyncio
async def test_userbot_import_is_owner_only(ops):
    operations, adapter, tmp_path = ops
    adapter._is_owner_user = lambda *_args, **_kwargs: False

    result = await operations.request(_source(user_id="200"), "userbot_import",
                                      {"session": _session_string()})

    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"
    assert not _session_path(tmp_path).exists()


@pytest.mark.asyncio
async def test_userbot_import_stores_a_private_session_without_echoing_it(ops):
    operations, _adapter, tmp_path = ops
    session_string = _session_string()

    result = await operations.request(_source(), "userbot_import", {"session": session_string})

    assert result["ok"] is True, result
    assert result["data"]["configured"] is True
    assert result["data"]["user_id"] == 321
    assert result["data"]["username"] == "helper"
    assert session_string not in json.dumps(result)
    path = _session_path(tmp_path)
    assert path.read_text(encoding="utf-8").strip() == session_string
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_userbot_status_reports_unconfigured_without_a_session(ops):
    operations, _adapter, _tmp_path = ops

    result = await operations.request(_source(), "userbot_status", {})

    assert result["ok"] is True, result
    assert result["data"]["configured"] is False
    assert "session" in result["data"]["hint"].lower()


@pytest.mark.asyncio
async def test_userbot_status_verifies_the_stored_account(ops):
    operations, _adapter, _tmp_path = ops
    await operations.request(_source(), "userbot_import", {"session": _session_string()})

    result = await operations.request(_source(), "userbot_status", {})

    assert result["ok"] is True, result
    assert result["data"]["configured"] is True
    assert result["data"]["authorized"] is True
    assert result["data"]["user_id"] == 321
    assert result["data"]["username"] == "helper"


@pytest.mark.asyncio
async def test_userbot_script_requires_an_imported_session(ops):
    operations, _adapter, _tmp_path = ops

    result = await operations.request(_source(), "userbot_script", {"code": "return 1"})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "import" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_userbot_script_runs_async_telethon_code_and_disconnects(ops):
    operations, _adapter, _tmp_path = ops
    await operations.request(_source(), "userbot_import", {"session": _session_string()})
    FakeTelegramClient.instances.clear()

    result = await operations.request(_source(), "userbot_script", {
        "code": "print('hello from userbot')\nvalue = await client.ping()\nreturn value",
    })

    assert result["ok"] is True, result
    assert result["data"]["result"] == "pong"
    assert "hello from userbot" in result["data"]["output"]
    client = FakeTelegramClient.instances[-1]
    assert client.connected is True
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_userbot_script_is_owner_only(ops):
    operations, adapter, _tmp_path = ops
    await operations.request(_source(), "userbot_import", {"session": _session_string()})
    adapter._is_owner_user = lambda *_args, **_kwargs: False

    result = await operations.request(_source(user_id="200"), "userbot_script", {"code": "return 1"})

    assert result["ok"] is False
    assert result["error"]["code"] == "FORBIDDEN"

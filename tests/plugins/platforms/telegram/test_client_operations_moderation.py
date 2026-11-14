"""Moderation actions: timed mute, unmute, promote/demote, kick and target resolution."""
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("telethon")

from telethon import functions, types

from gateway.config import Platform
from gateway.session import SessionSource
from plugins.platforms.telegram.client_operations import TelegramClientOperations


@pytest.fixture
def ops(tmp_path):
    adapter = SimpleNamespace(
        _client=SimpleNamespace(
            get_input_entity=AsyncMock(return_value=types.InputPeerUser(user_id=100, access_hash=999)),
            get_entity=AsyncMock(return_value=SimpleNamespace(id=100, access_hash=999, username="target")),
        ),
        _authorized=lambda _source: True,
        _is_owner_user=lambda _user, source=None, **_kw: True,
        _settings_home=lambda _source: tmp_path / "alpha",
        _input_peer_for=AsyncMock(return_value=types.InputPeerChannel(channel_id=42, access_hash=123)),
        _mtproto_rich_request=AsyncMock(return_value=SimpleNamespace(updates=[])),
        _message_thread_id=lambda _message: None,
        _bot_username="hermes_bot",
        _bot_id=777,
    )
    return TelegramClientOperations(adapter), adapter


def _source(message_id=None):
    return SessionSource(platform=Platform.TELEGRAM, chat_id="-10042", chat_type="group",
                         user_id="100", thread_id="77", message_id=message_id)


@pytest.mark.asyncio
async def test_user_mute_applies_timed_send_restriction(ops):
    operations, adapter = ops
    before = datetime.datetime.now(datetime.UTC)
    result = await operations.request(
        _source(), "user_mute", {"user_id": "100", "duration": "10m"})

    assert result["ok"] is True, result
    assert result["data"]["user_id"] == 100
    assert result["data"]["duration"] == "10m"
    request = adapter._mtproto_rich_request.await_args.args[0]
    assert isinstance(request, functions.channels.EditBannedRequest)
    assert isinstance(request.participant, types.InputPeerUser)
    assert request.participant.user_id == 100
    assert request.banned_rights.send_messages is True
    assert request.banned_rights.view_messages is False
    assert isinstance(request.banned_rights.until_date, datetime.datetime)
    until = request.banned_rights.until_date
    if until.tzinfo is None:
        until = until.replace(tzinfo=datetime.UTC)
    assert datetime.timedelta(minutes=9, seconds=50) < until - before < datetime.timedelta(minutes=10, seconds=10)


@pytest.mark.asyncio
async def test_user_mute_resolves_the_replied_to_sender(ops):
    operations, adapter = ops
    source = _source(message_id=50)
    operations.observe(source, SimpleNamespace(id=50, message="mute him", sender_id=100,
                                               reply_to_msg_id=49, entities=[]))
    operations.observe(source, SimpleNamespace(id=49, message="spam", sender_id=321,
                                               reply_to_msg_id=None, entities=[]))
    adapter._client.get_input_entity = AsyncMock(
        return_value=types.InputPeerUser(user_id=321, access_hash=654))

    result = await operations.request(source, "user_mute", {"duration": "1h"})

    assert result["ok"] is True, result
    assert result["data"]["user_id"] == 321
    request = adapter._mtproto_rich_request.await_args.args[0]
    assert request.participant.user_id == 321


@pytest.mark.asyncio
async def test_user_mute_resolves_a_text_mention_in_the_triggering_message(ops):
    operations, adapter = ops
    source = _source(message_id=50)
    operations.observe(source, SimpleNamespace(
        id=50, message="mute @target for 5m", sender_id=100, reply_to_msg_id=None,
        entities=[types.MessageEntityMention(offset=5, length=7)]))
    adapter._client.get_entity = AsyncMock(return_value=SimpleNamespace(id=321, access_hash=654))
    adapter._client.get_input_entity = AsyncMock(
        return_value=types.InputPeerUser(user_id=321, access_hash=654))

    result = await operations.request(source, "user_mute", {"duration": "5m"})

    assert result["ok"] is True, result
    assert result["data"]["user_id"] == 321
    request = adapter._mtproto_rich_request.await_args.args[0]
    assert request.participant.user_id == 321


@pytest.mark.asyncio
async def test_user_mute_refuses_a_basic_group(ops):
    operations, adapter = ops
    adapter._input_peer_for = AsyncMock(return_value=types.InputPeerChat(chat_id=42))

    result = await operations.request(_source(), "user_mute", {"user_id": 100, "duration": "5m"})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "supergroup" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_user_promote_grants_moderator_rights(ops):
    operations, adapter = ops
    result = await operations.request(_source(), "user_promote", {"user_id": 100})

    assert result["ok"] is True, result
    request = adapter._mtproto_rich_request.await_args.args[0]
    assert isinstance(request, functions.channels.EditAdminRequest)
    assert request.user_id.user_id == 100
    assert request.admin_rights.delete_messages is True
    assert request.admin_rights.ban_users is True
    assert request.admin_rights.add_admins is False


@pytest.mark.asyncio
async def test_user_demote_clears_admin_rights(ops):
    operations, adapter = ops
    result = await operations.request(_source(), "user_demote", {"user_id": 100})

    assert result["ok"] is True, result
    request = adapter._mtproto_rich_request.await_args.args[0]
    assert isinstance(request, functions.channels.EditAdminRequest)
    assert request.admin_rights.delete_messages is False
    assert request.admin_rights.ban_users is False


@pytest.mark.asyncio
async def test_user_kick_bans_then_unbans_a_supergroup_member(ops):
    operations, adapter = ops
    result = await operations.request(_source(), "user_kick", {"user_id": 100})

    assert result["ok"] is True, result
    calls = adapter._mtproto_rich_request.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0].banned_rights.view_messages is True
    assert calls[1].args[0].banned_rights.view_messages is False


@pytest.mark.asyncio
async def test_user_mute_requires_a_target_instead_of_guessing(ops):
    operations, _adapter = ops
    result = await operations.request(_source(message_id=50), "user_mute", {"duration": "5m"})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "target" in result["error"]["message"].lower()

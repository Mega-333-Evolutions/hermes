"""Telegram clarify polls: a single-select clarify goes out as a native poll,
the first vote from the configured respondent resolves it and stops the poll,
and votes from anyone else are ignored.

No Telegram connections or real sends: the MTProto transport is a fake that
serializes the real Telethon requests and returns the sent message.
"""
import pytest
from telethon import events, functions, types
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource
from plugins.platforms.telegram.telethon_adapter import TelethonAdapter
from tools import clarify_gateway as cm

OWNER_ID = "100"
OTHER_ID = "200"
CHAT_ID = "-1000000000042"
THREAD_ID = "77"
RESPONDENT = OWNER_ID


class _FakeRichTransport:
    """Serializes real Telethon requests; returns the sent message like the server would."""

    def __init__(self):
        self.requests: list = []
        self.next_message_id = 100

    async def rich_request(self, request):
        assert bytes(request)
        self.requests.append(request)
        if isinstance(request, functions.messages.SendMediaRequest):
            self.next_message_id += 1
            media = types.MessageMediaPoll(
                poll=request.media.poll,
                results=types.PollResults(results=[], total_voters=0))
            reply = getattr(request, "reply_to", None)
            message = types.Message(
                id=self.next_message_id, peer_id=types.PeerChannel(42), message="",
                media=media, date=None, out=True,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=reply.reply_to_msg_id,
                    reply_to_top_id=getattr(reply, "top_msg_id", None),
                    forum_topic=bool(getattr(reply, "top_msg_id", None))) if reply else None)
            return SimpleNamespace(updates=[SimpleNamespace(message=message)])
        if isinstance(request, functions.messages.EditMessageRequest):
            return SimpleNamespace(updates=[])
        return SimpleNamespace(updates=[])


@pytest.fixture
def transport(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "OWNER_TELEGRAM_ID=100\nTELEGRAM_ALLOWED_USERS=100,200\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = TelethonAdapter(PlatformConfig(extra={"require_mention": False}))
    adapter._bot_id = 900
    adapter._bot_username = "HermesPrivateBot"
    adapter._client = SimpleNamespace()
    adapter.gateway_runner = SimpleNamespace(
        _resolve_profile_home_for_source=lambda source: tmp_path)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id=CHAT_ID, chat_type="group",
                           user_id=OWNER_ID, thread_id=THREAD_ID, message_id="12")
    adapter._input_peer_for = AsyncMock(return_value=types.InputPeerChannel(42, 123))
    fake = _FakeRichTransport()
    adapter._mtproto_rich_request = fake.rich_request
    adapter.send = AsyncMock()
    adapter._observe_telegram_message(source, SimpleNamespace(id=12))
    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()
        cm._polls.clear()
        cm._poll_index.clear()
    return adapter, source, fake


def _send_kwargs(clarify_id, question, choices):
    return {"chat_id": CHAT_ID, "question": question, "choices": choices,
            "clarify_id": clarify_id, "session_key": "sk1",
            "metadata": {"thread_id": THREAD_ID}}


def _question(raw):
    return raw + "\nReply \"skip\" to skip this question."


def _poll_request(fake):
    return next(r for r in fake.requests if isinstance(r, functions.messages.SendMediaRequest))


def _edit_requests(fake):
    return [r for r in fake.requests if isinstance(r, functions.messages.EditMessageRequest)]


@pytest.mark.asyncio
async def test_single_select_clarify_sends_non_anonymous_poll_with_footer(transport):
    adapter, _source, fake = transport
    entry = cm.register("c1", "sk1", "Which VC should I join?",
                        ["Testing Agents", "Test3", "Both"], respondent_id=RESPONDENT)
    result = await adapter.send_clarify(**_send_kwargs(
        "c1", _question("Which VC should I join?"), ["Testing Agents", "Test3", "Both"]))
    assert result.success and result.message_id
    request = _poll_request(fake)
    assert request.media.poll.question.text == (
        "❓ Which VC should I join?\n\n—\nReply to this poll to send your answer.")
    assert request.message == ""
    assert [a.text.text for a in request.media.poll.answers] == ["Testing Agents", "Test3", "Both"]
    assert [a.option for a in request.media.poll.answers] == [bytes([0]), bytes([1]), bytes([2])]
    assert request.media.poll.public_voters is True  # non-anonymous: votes reach the bot
    assert request.media.poll.multiple_choice is False
    assert request.media.poll.revoting_disabled is True
    assert request.media.poll.close_period == cm.get_clarify_timeout()
    poll = cm.get_clarify_poll("c1")
    assert poll is not None
    assert poll.poll_id == request.media.poll.id
    assert poll.message_id == int(result.message_id)
    assert poll.chat_id == CHAT_ID and poll.thread_id == THREAD_ID
    assert entry.awaiting_text is True  # typed replies stay open as the custom-answer path
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_question_puts_footer_in_description(transport):
    adapter, _source, fake = transport
    cm.register("c2", "sk1", "Q?", ["A", "B"], respondent_id=RESPONDENT)
    long_q = "x" * 230
    result = await adapter.send_clarify(**_send_kwargs("c2", _question(long_q), ["A", "B"]))
    assert result.success
    request = _poll_request(fake)
    # Question alone fits (255), question + footer does not: the footer rides as the caption.
    assert request.media.poll.question.text == "❓ " + long_q
    assert request.message == "\n\n—\nReply to this poll to send your answer."


@pytest.mark.asyncio
async def test_multi_select_clarify_stays_text_card(transport):
    adapter, _source, fake = transport
    entry = cm.register("c3", "sk1", "Pick some", ["A", "B"],
                        multi_select=True, respondent_id=RESPONDENT)
    result = await adapter.send_clarify(**_send_kwargs(
        "c3", _question("Pick some"), ["A", "B"]))
    assert result.success
    assert not [r for r in fake.requests if isinstance(r, functions.messages.SendMediaRequest)]
    assert cm.get_clarify_poll("c3") is None
    adapter.send.assert_awaited_once()
    text = adapter.send.await_args.kwargs["content"]
    assert "1. A" in text and "2. B" in text
    assert entry.awaiting_text is True


@pytest.mark.asyncio
async def test_oversize_question_falls_back_to_text_card(transport):
    adapter, _source, fake = transport
    cm.register("c4", "sk1", "Q?", ["A", "B"], respondent_id=RESPONDENT)
    result = await adapter.send_clarify(**_send_kwargs(
        "c4", _question("y" * 300), ["A", "B"]))
    assert result.success
    assert not [r for r in fake.requests if isinstance(r, functions.messages.SendMediaRequest)]
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_create_return_carries_poll_id(transport):
    adapter, source, fake = transport
    ops = adapter._client_operations_ui().operations
    result = await ops.request(source, "poll_create",
                               {"question": "Pick one", "options": ["A", "B"]})
    assert result["ok"] and result["status"] == "complete", result
    assert result["data"]["poll_id"] == _poll_request(fake).media.poll.id


async def _send_poll(transport, clarify_id="c5"):
    adapter, _source, fake = transport
    entry = cm.register(clarify_id, "sk1", "Which VC should I join?",
                        ["Testing Agents", "Test3", "Both"], respondent_id=RESPONDENT)
    result = await adapter.send_clarify(**_send_kwargs(
        clarify_id, _question("Which VC should I join?"), ["Testing Agents", "Test3", "Both"]))
    assert result.success
    return adapter, fake, entry, cm.get_clarify_poll(clarify_id)


def _vote(poll_id, user_id, positions, options=None):
    return types.UpdateMessagePollVote(
        poll_id=poll_id, peer=types.PeerUser(int(user_id)),
        options=list(options) if options is not None else [bytes([p]) for p in positions],
        positions=list(positions), qts=1)


@pytest.mark.asyncio
async def test_vote_from_respondent_resolves_and_stops_poll(transport):
    adapter, fake, entry, poll = await _send_poll(transport)
    await adapter._on_poll_vote(_vote(poll.poll_id, RESPONDENT, [1]))
    assert entry.response == "Test3"
    assert entry.event.is_set()
    edits = _edit_requests(fake)
    assert len(edits) == 1
    assert edits[0].id == poll.message_id
    assert edits[0].media.poll.closed is True
    assert cm.get_clarify_poll("c5") is None


@pytest.mark.asyncio
async def test_vote_from_other_user_is_ignored_poll_stays_open(transport):
    adapter, fake, entry, poll = await _send_poll(transport)
    await adapter._on_poll_vote(_vote(poll.poll_id, OTHER_ID, [0]))
    assert entry.response is None
    assert not entry.event.is_set()
    assert _edit_requests(fake) == []
    assert cm.get_clarify_poll("c5") is poll


@pytest.mark.asyncio
async def test_retracted_vote_is_ignored(transport):
    adapter, fake, entry, poll = await _send_poll(transport)
    await adapter._on_poll_vote(_vote(poll.poll_id, RESPONDENT, [], options=[]))
    assert entry.response is None
    assert _edit_requests(fake) == []
    assert cm.get_clarify_poll("c5") is poll


@pytest.mark.asyncio
async def test_retire_stops_poll_and_unknown_clarify_is_a_noop(transport):
    adapter, fake, _entry, _poll = await _send_poll(transport)
    await adapter.retire_clarify_card("c5", "expired")
    edits = _edit_requests(fake)
    assert len(edits) == 1 and edits[0].media.poll.closed is True
    assert cm.get_clarify_poll("c5") is None
    await adapter.retire_clarify_card("never-registered", "expired")
    assert len(_edit_requests(fake)) == 1  # no second stop attempted


def test_vote_handler_registered_for_poll_vote_updates(transport):
    adapter, _source, _fake = transport
    calls: list = []
    adapter._client = SimpleNamespace(
        add_event_handler=lambda cb, ev: calls.append((cb, ev)))
    adapter._handlers_registered = False
    adapter._register_handlers(events)
    vote_handlers = [(cb, ev) for cb, ev in calls
                     if getattr(ev, "types", None) is types.UpdateMessagePollVote]
    assert len(vote_handlers) == 1
    assert vote_handlers[0][0] == adapter._on_poll_vote

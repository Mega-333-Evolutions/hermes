"""Dedicated routing for Telegram client controls and supervised bot peers."""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

CLIENT_COMMANDS = frozenset({"poll", "tgmessages", "tgfiles", "tginfo", "tgadmin", "tgprofile"})
BOT_COMMANDS = frozenset({"managedbots", "botpeers", "b2b"})
TELEGRAM_OPERATION_COMMANDS = CLIENT_COMMANDS | BOT_COMMANDS

# Telegram polls cap the question at 255 characters (the operations layer allows
# 300, but the server rejects longer questions). The clarify footer is a divider
# line plus the locale string; when question + footer no longer fits, the footer
# rides as the poll's description (SendMedia caption) instead.
CLARIFY_POLL_MAX_QUESTION_CHARS = 255
CLARIFY_POLL_FOOTER_DIVIDER = "\n\n—\n"


def build_clarify_poll_text(question: str, choices: Any) -> tuple[str, str] | None:
    """Render a single-select clarify as ``(poll question, description)``.

    The poll question carries the ``❓`` heading plus the footer (divider +
    "Reply to this poll to send your answer.") when it fits in 255 characters;
    otherwise the footer goes in the description caption below the poll. Returns
    None when the question cannot be a poll at all — no choices (open-ended),
    a question that alone exceeds 255 characters, or options that break the
    poll limits (1–12 unique non-empty strings, each ≤ 100 characters) — so the
    caller falls back to the numbered-text card instead of truncating anything.
    """
    from agent.i18n import t

    raw = str(question or "").strip()
    # The runner appends the skip hint for messaging platforms; the poll footer
    # replaces it (skip still works: replying "skip" to the poll skips it).
    skip_hint = t("gateway.clarify.skip_hint")
    if skip_hint and raw.endswith("\n" + skip_hint):
        raw = raw[: -len("\n" + skip_hint)].rstrip()
    headed = t("gateway.clarify.question", question=raw)
    if not raw or len(headed) > CLARIFY_POLL_MAX_QUESTION_CHARS:
        return None
    labels = [str(choice).strip() for choice in (choices or [])]
    if (not labels or len(labels) > 12 or any(not label or len(label) > 100 for label in labels)
            or len(set(labels)) != len(labels)):
        return None
    footer = CLARIFY_POLL_FOOTER_DIVIDER + t("gateway.clarify.poll_footer")
    if len(headed) + len(footer) <= CLARIFY_POLL_MAX_QUESTION_CHARS:
        return headed + footer, ""
    return headed, footer


class TelegramOperationsRoutingMixin:
    """Keep client control dispatch separate from setup and conversational commands."""

    def _client_operations_ui(self):
        ui = getattr(self, "_telegram_client_operations_ui", None)
        if ui is None:
            from .client_operations_ui import TelegramClientOperationsUI
            ui = self._telegram_client_operations_ui = TelegramClientOperationsUI(self)
        return ui

    def _bot_operations_ui(self):
        ui = getattr(self, "_telegram_bot_operations_ui", None)
        if ui is None:
            from .bot_operations_ui import TelegramBotOperationsUI
            ui = self._telegram_bot_operations_ui = TelegramBotOperationsUI(self)
        return ui

    async def _on_managed_bot_update(self, update: Any) -> None:
        # Raw manager updates belong to this transport, never to a child creator's
        # potentially different session/profile route. Tokens are not retrieved.
        home = getattr(self, "_telegram_transport_home", None)
        if home is None or getattr(self, "_client", None) is None:
            return
        try:
            result = await self._bot_operations_ui().operations.handle_managed_update(update, profile_home=home)
        except Exception as exc:
            logger.warning("Telegram managed-bot update was not recorded (%s)", type(exc).__name__)
            return
        if not result.get("ok"):
            return
        # The roster changed: re-render any open managed card for this profile so
        # the new child appears without the owner re-running the command.
        try:
            await self._bot_operations_ui().refresh_managed_cards(str(home), result.get("bot_id"))
        except Exception as exc:
            logger.warning("Managed-bot card refresh failed (%s)", type(exc).__name__)

    def _observe_telegram_message(self, source: Any, message: Any) -> None:
        from gateway.session_identity import replace_source
        from gateway.session import SessionSource
        ui = self._client_operations_ui()
        ui.observe(source, message)
        if not isinstance(source, SessionSource):
            return  # synthetic sources have no native event identity to retain
        # F4: the same admitted inbound message settles a correlated-reply
        # waiter on this chat and refreshes the conversation registry's
        # address row. Best-effort and fail-open: observation never breaks
        # on it, and the message still routes to its own session's turn.
        try:
            raw = getattr(message, "raw_message", None) or message
            msg_id = getattr(raw, "id", None)
            if msg_id is not None:
                date = getattr(raw, "date", None)
                from tools.conversation_reply_wait import notify_inbound
                notify_inbound(
                    self._settings_home(source), "telegram", source.chat_id,
                    getattr(source, "thread_id", None) or None,
                    message_id=int(msg_id),
                    sender_id=getattr(raw, "sender_id", None),
                    is_bot=bool(getattr(source, "is_bot", False)),
                    text=str(getattr(raw, "message", "") or ""),
                    date=date.isoformat() if hasattr(date, "isoformat") else date,
                    reply_to_msg_id=getattr(raw, "reply_to_msg_id", None),
                )
            from tools.conversation_registry import record_conversation
            record_conversation(
                "telegram", source.chat_id, thread_id=getattr(source, "thread_id", None) or None,
                type=str(getattr(source, "chat_type", "") or "") or None,
                name=getattr(source, "chat_name", None) or getattr(source, "user_name", None),
                last_message_id=int(msg_id) if msg_id is not None else None,
            )
        except Exception:  # health: allow BLE001 -- the F4 hook never breaks observation
            logger.debug("conversation reply hook failed", exc_info=True)
        sources = getattr(self, "_telegram_observed_sources", None)
        if sources is None:
            sources = self._telegram_observed_sources = OrderedDict()
        key = (str(self._settings_home(source)), str(source.chat_id), str(source.thread_id or ""))
        sources[key] = replace_source(source)
        sources.move_to_end(key)
        while len(sources) > 256:
            sources.popitem(last=False)

    async def _on_poll_update(self, update: Any) -> None:
        # Never enumerate chats or request user-only poll methods. A verified
        # Telegram update can refresh only a poll already in this transport's cache.
        ui = getattr(self, "_telegram_client_operations_ui", None)
        if ui is None:
            return
        for source in list(getattr(self, "_telegram_observed_sources", {}).values()):
            try:
                ui.operations.observe_poll_update(source, update)
            except Exception as exc:
                logger.debug("Telegram poll cache scope is unavailable (%s)", type(exc).__name__)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key, metadata=None):
        """Single-select clarify as a native Telegram poll.

        Multi-select stays the numbered-text card (one vote expires the poll, which
        cannot collect several selections), and anything that cannot be a poll —
        an oversize question, an unusable option, no observed source to send as —
        falls back to the base text card. The poll is non-anonymous: Telegram only
        delivers ``updateMessagePollVote`` for polls the bot itself sent that are
        non-anonymous, so an anonymous poll could never resolve. Its ``open_period``
        matches the clarify timeout, so a poll the process never retires still
        closes on the server at the same moment the wait expires.
        """
        from tools import clarify_gateway as clarify_mod

        entry = clarify_mod.get_pending(clarify_id)
        if entry is not None and entry.multi_select:
            return await super().send_clarify(
                chat_id=chat_id, question=question, choices=choices, clarify_id=clarify_id,
                session_key=session_key, metadata=metadata)
        built = build_clarify_poll_text(question, choices)
        if built is None:
            return await super().send_clarify(
                chat_id=chat_id, question=question, choices=choices, clarify_id=clarify_id,
                session_key=session_key, metadata=metadata)
        thread_id = str((metadata or {}).get("thread_id") or "")
        source = self._clarify_poll_source(chat_id, thread_id)
        if source is None:
            logger.debug("Clarify poll has no observed source for chat %s; using the text card", chat_id)
            return await super().send_clarify(
                chat_id=chat_id, question=question, choices=choices, clarify_id=clarify_id,
                session_key=session_key, metadata=metadata)
        poll_question, description = built
        params: dict[str, Any] = {
            "question": poll_question, "options": list(choices), "description": description,
            "anonymous": False, "multiple_choice": False, "revoting": False, "quiz": False,
        }
        timeout = clarify_mod.get_clarify_timeout()
        if 5 <= timeout <= 2628000:
            params["open_period"] = timeout
        result = await self._client_operations_ui().request(source, "poll_create", params)
        if not result.get("ok"):
            error = result.get("error") or {}
            logger.warning("Clarify poll could not be created (%s); using the text card",
                           error.get("message") or result.get("status"))
            return await super().send_clarify(
                chat_id=chat_id, question=question, choices=choices, clarify_id=clarify_id,
                session_key=session_key, metadata=metadata)
        data = result.get("data") or {}
        message_id = data.get("message_id")
        poll_id = data.get("poll_id")
        if not message_id or not poll_id:
            logger.warning("Clarify poll creation returned no message/poll id; using the text card")
            return await super().send_clarify(
                chat_id=chat_id, question=question, choices=choices, clarify_id=clarify_id,
                session_key=session_key, metadata=metadata)
        clarify_mod.register_clarify_poll(
            clarify_id, int(poll_id), int(message_id), chat_id, thread_id)
        # Typed replies stay open: a reply to the poll is the "your own answer" path.
        clarify_mod.mark_awaiting_text(clarify_id)
        from gateway.platforms.base import SendResult

        return SendResult(success=True, message_id=str(message_id))

    async def retire_clarify_card(self, clarify_id: str, notice: str) -> None:
        """Stop a clarify poll (timeout, answered by text, cancelled); the closed
        poll is the retired card, so the notice is not posted anywhere."""
        from tools import clarify_gateway as clarify_mod

        poll = clarify_mod.get_clarify_poll(clarify_id)
        if poll is None:
            return
        await self._stop_clarify_poll(poll)

    def _clarify_poll_source(self, chat_id: Any, thread_id: str) -> Any | None:
        """The observed source for the turn's chat/thread — the identity the poll
        send runs as. The turn's triggering message is observed before the turn
        runs, so this is normally present; None means the text card instead."""
        for source in (getattr(self, "_telegram_observed_sources", None) or {}).values():
            if (str(getattr(source, "chat_id", "")) == str(chat_id)
                    and str(getattr(source, "thread_id", "") or "") == str(thread_id or "")):
                return source
        return None

    async def _stop_clarify_poll(self, poll: Any) -> None:
        """Best-effort poll_stop for a clarify poll; the registry entry is dropped
        either way, so a failed stop cannot wedge the next vote."""
        from tools import clarify_gateway as clarify_mod

        try:
            source = self._clarify_poll_source(poll.chat_id, poll.thread_id)
            if source is not None:
                await self._client_operations_ui().request(
                    source, "poll_stop", {"message_id": int(poll.message_id)})
        except Exception:
            logger.debug("Clarify poll stop failed", exc_info=True)
        finally:
            clarify_mod.drop_clarify_poll(poll.clarify_id)

    async def _on_poll_vote(self, update: Any) -> None:
        """A vote on a clarify poll: the first vote from the configured respondent
        resolves the clarify and stops the poll. Votes from anyone else are
        ignored — they do not count and do not expire the poll. A retracted vote
        (no options) carries no answer and is ignored too."""
        from tools import clarify_gateway as clarify_mod

        try:
            poll_id = int(getattr(update, "poll_id", 0) or 0)
        except (TypeError, ValueError):
            return
        if not poll_id:
            return
        found = clarify_mod.get_clarify_poll_for_poll_id(poll_id)
        if found is None:
            return
        poll, entry = found
        if entry is None:
            # Resolved or expired before the vote arrived: stop the stale poll.
            await self._stop_clarify_poll(poll)
            return
        try:
            from telethon import utils as tg_utils

            voter_id = str(tg_utils.get_peer_id(getattr(update, "peer", None)))
        except Exception:
            return
        if entry.respondent_id is not None and voter_id != str(entry.respondent_id):
            logger.debug("Clarify poll vote from %s ignored (respondent is %s); poll stays open",
                         voter_id, entry.respondent_id)
            return
        try:
            positions = [int(p) for p in (getattr(update, "positions", None) or [])]
        except (TypeError, ValueError):
            return
        options = list(getattr(update, "options", None) or [])
        index = None
        if positions:
            index = positions[0]
        elif options:
            for i in range(len(entry.choices or [])):
                if bytes([i]) in options:
                    index = i
                    break
        if index is None or not entry.choices or not 0 <= index < len(entry.choices):
            return
        clarify_mod.resolve_gateway_clarify(entry.clarify_id, entry.choices[index])
        await self._stop_clarify_poll(poll)

    async def _handle_telegram_operation_command(self, message: Any, source: Any,
                                                 command: str, arguments: str,
                                                 *, reply_to=None) -> bool:
        if command not in TELEGRAM_OPERATION_COMMANDS:
            return False
        # Any admitted user may run these controls; there is no launch-owner
        # gate. Bots, rejected routes and unadmitted senders are denied
        # silently, like every other denial.
        if (getattr(source, "is_bot", False) or getattr(source, "profile_route_rejected", False)
                or not self._authorized(source)):
            return True
        ui = self._client_operations_ui() if command in CLIENT_COMMANDS else self._bot_operations_ui()
        await ui.handle_command(message, source, command, arguments, reply_to=reply_to)
        return True

    async def request_telegram_operation(self, source: Any, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Agent tool entry point; any admitted user may request any operation, and mutations execute immediately on call."""
        if (getattr(source, "is_bot", False) or getattr(source, "profile_route_rejected", False)
                or not self._authorized(source)):
            return {"error": "Telegram operations are not available for this sender."}
        if action == "capabilities":
            from .bot_operations import BOT_OPERATION_PARAMETERS
            client = await self._client_operations_ui().request(source, action, {})
            bots = await self._bot_operations_ui().request(source, action, {})
            bot_parameters = {name: fields for name, fields in BOT_OPERATION_PARAMETERS.items()
                              if name not in {"capabilities", "managed.set_info"}}
            bots.update(actions=sorted(bot_parameters), parameters=bot_parameters,
                        manager_mode_enabled=bool(getattr(self, "_bot_can_manage_bots", False)),
                        token_export_exposed=False,
                        bot_to_bot="DM requires BOTH bots' BotFather bot-to-bot mode. Groups require supported mention/reply or admin/privacy visibility; local exact-chat grants remain mandatory. Incoming bots are inbox-only, never autonomous model turns.")
            return {"success": True, "client": client, "bots": bots,
                    "commands": sorted(TELEGRAM_OPERATION_COMMANDS),
                    "scope": "current profile and current chat; separately approved bot-peer routes only",
                    "confirmation": "Mutating requests execute immediately on call; there is no confirmation step."}
        from .client_operations import ALL_ACTIONS
        if action in ALL_ACTIONS:  # bot_profile* are client self-configuration, not bot-peer aliases
            return await self._client_operations_ui().request(source, action, params)
        if action.startswith(("bot_", "managed_", "peer_", "managed.", "peers.", "peer.", "inbox.")):
            return await self._bot_operations_ui().request(source, action, params)
        return await self._client_operations_ui().request(source, action, params)

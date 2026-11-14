"""Structured Telegram operations through the active bot transport and native approvals."""
from __future__ import annotations

import json
import logging
from typing import Any

from gateway.config import Platform
from gateway.session_context import get_session_env
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

TELEGRAM_CLIENT_SCHEMA = {
    "name": "telegram_client",
    "description": (
        "Operate Telegram through the current conversation's bot identity. Any admitted human may use "
        "every action; use action=capabilities to discover the operations and their parameters. Reads "
        "cover accessible messages, polls, files and chat/user metadata. gateway_logs reads only the "
        "active profile's gateway.log or errors.log (up to 100 recent lines, bounded and forcibly "
        "secret/token-redacted); it accepts no paths and cannot write or control services. "
        "Administrative writes, moderation, customization, managed bots and approved bot peers run "
        "immediately on call. pin/unpin, reactions, ephemeral DM sends/deletes, and cross-chat "
        "replies to observed messages execute on call. "
        "send_message sends to a validated numeric chat/user ID or @username; the recipient is validated "
        "and resolved server-side. It takes reply_to, thread_id, silent, buttons, quote, effect, pin, "
        "media, as_voice, video_note, force_document, and location, so one call covers the generic "
        "message send: a reply or topic send, a silent send, a media attachment with the body as its "
        "caption, a voice or video note, a standalone location or venue, and pinning the sent message. "
        "request runs one structured Telethon TL request — any method, read or write — through the same live "
        "client (method plus JSON args, and kwargs for nested TL objects), with the peer defaulting to the current chat; it is a structured descriptor, not code "
        "evaluation. "
        "Widget actions send, resend, upload, answer, edit, and read layer-233 HTML widgets "
        "in the current chat only, with no confirmation card and no chat target, and only inside a DM: "
        "widget_send and widget_send_by_id refuse any chat that is not a DM, and a group gets the same "
        "content as a rich message instead. widget_show builds one pre-made precise widget from a "
        "template (metric, progress, checklist, flow, chart, status) and data: in a DM it sends the "
        "widget, plus a rich companion message carrying the one-way buttons when a companion or buttons "
        "are given; in any other chat it sends the same content as one precise rich message with the "
        "buttons. widget_send and "
        "widget_upload take html plus optional caption, dimensions, allow_queries, shared_widget, "
        "public_data, chat_data, reply_to, and a classic inline keyboard; widget_send_by_id resends an "
        "existing widget by widget_id; widget_answer answers a tap query_id with an optional short "
        "result; widget_edit_message, widget_edit_shared, and widget_edit_inline edit widget state by "
        "message, shared widget_id, or inline message id; widget_get_public_data reads a shared "
        "widget's public state. Widget methods are not expressible through request. "
        "rich_message_send and quote_reply send one native RichMessage "
        "immediately, with optional one-way URL, copy, or profile buttons rendered as "
        "in-body pills for Rich HTML bodies (or PageButton rows for Markdown/blocks bodies), with "
        "optional primary/success/danger styles; they support "
        "no callbacks. quote_reply replies to one message already observed in the "
        "same chat/topic and quotes a verbatim excerpt of that message's text. Params carry no tokens "
        "or credentials. send_message to a validated numeric ID or @username is the direct-send "
        "action for other destinations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Any admitted human may call any action; use capabilities to discover the operations and their parameters."},
            "params": {"type": "object", "description": "Parameters for the discovered operation; no credential or path parameters. For request, use method as a Telethon TL function name (e.g. channels.GetFullChannel), args as JSON scalar fields, and kwargs for nested TL objects; any TL method may be called, read or write. The peer defaults to the current chat, never supplied in args. For gateway_logs, choose only gateway or errors and an optional 1–100 line limit. For rich_message_send, use content, optional format, and optional one-way url/copy/profile buttons with optional styles; Rich HTML content may also carry <tg-button> pills inline. For quote_reply, use message_id of an already-observed message in this chat/topic, quote as a verbatim excerpt of its text, content, and the same optional format/buttons. For pin_message/unpin_message, use message_id of an already-observed message in this chat/topic with optional silent; unpin_message also takes pm_oneside for a one-sided unpin in a DM only. For reaction, use emoji as one of Telegram's standard reaction emojis, optional big true for a big reaction (default false), and optional message_id of an already-observed message — omit message_id to react to the current message. For leave, use no parameters — it leaves the current chat (supergroup/channel, or basic group by removing the bot itself); a DM is refused. For ephemeral_send, use content and optional format (DM only, best-effort delivery); for ephemeral_delete, message_id of an observed ephemeral message. For cross_chat_reply, use message_id of a message already observed in any chat/topic this profile has seen — the reply target comes from that observed message, never from params — plus content and the same optional format/buttons.         For send_message, use recipient as a numeric Telegram chat/user ID or @username, content, and optional format; "
        "the recipient is validated and resolved server-side. Optional reply_to answers a message in the recipient chat, "
        "thread_id targets a forum topic there, silent sends without notifying, buttons adds up to 8 native one-way "
        "buttons, quote quotes a verbatim excerpt of the replied-to message, effect sets a message effect id, pin pins "
        "the sent message (pin_silent, default true), media sends one http(s) URL or base64/data-URL attachment with "
        "content as its caption (filename, content_type), as_voice sends audio media as a voice note, video_note sends "
        "one video as a round video note, force_document (as_document is an alias) sends media uncompressed, and "
        "location sends a standalone pin or venue as {latitude, longitude, accuracy, name, address} with no text or media. For chat_topics, use limit/offset_topic for a page, or topic_ids to fetch exactly those topics. For widget_show, use template as one of metric, progress, checklist, flow, chart, status, title, data as the template's object, and optional companion Rich HTML and buttons as up to 8 native one-way buttons; in a DM it sends the widget plus a rich companion message carrying the buttons when a companion or buttons are given, in any other chat it sends the same content as one precise rich message with the buttons. For widget_send, use html, optional caption, width, height, allow_queries, shared_widget, public_data, chat_data, reply_to, and reply_markup as rows of [label, callback payload, style] triples; widget_send and widget_send_by_id send only inside a DM and refuse any other chat. For widget_send_by_id, use widget_id with optional chat_data and caption. For widget_upload, use html and the same widget options. For widget_answer, use query_id and an optional result of at most 1,024 bytes. For widget_edit_message, use message_id with public_data or chat_data (at least one). For widget_edit_shared, use widget_id, public_data, and optional cache_time. For widget_edit_inline, use inline_message_id as {dc_id, id, access_hash} with public_data or chat_data (at least one). For widget_get_public_data, use message_id and an optional hash, default 0."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def telegram_client(args: dict[str, Any], **_kwargs: Any) -> str:
    """Resolve the trusted session route, then invoke its adapter's operation controller."""
    if (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower() != Platform.TELEGRAM.value:
        return tool_error("Telegram operations are available only inside a Telegram conversation.")
    chat_id = (get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()
    user_id = (get_session_env("HERMES_SESSION_USER_ID", "") or "").strip()
    if not chat_id or not user_id.isdecimal():
        return tool_error("The current Telegram chat and human actor are required; no action was taken.")
    action = str(args.get("action") or "").strip()
    params = args.get("params", {})
    if not action or not isinstance(params, dict):
        return tool_error("An operation name and an object of parameters are required.")
    if any(key in params for key in ("user_is_owner", "confirmed", "confirmation_token")):
        return tool_error("Approval fields cannot be supplied as operation parameters.")
    # Turn taint (PROVISIONAL, wave 5): a tainted turn cannot send messages to
    # another chat — send_message only to this chat by numeric id, never
    # cross_chat_reply. Same-chat sends, reads, reactions, and pins are untouched.
    # Direct callers (conversations_turn's _send_to_chat) pass no parent_agent,
    # so they are gated at their own entry point instead; this check covers the
    # registry-dispatched path, which is the model's path.
    from agent.turn_taint import refuse_tainted_telegram_send

    refusal = refuse_tainted_telegram_send(
        _kwargs.get("parent_agent"), action=action, params=params, current_chat_id=chat_id)
    if refusal is not None:
        return tool_error(refusal)
    try:
        from tools.send_message_senders import _live_adapter
        runner, adapter = _live_adapter(Platform.TELEGRAM)
        if runner is not None:
            from gateway.session_identity import identity_of
            cached = getattr(runner, "_get_cached_session_source", None)
            session_key = get_session_env("HERMES_SESSION_KEY", "") or ""
            source = cached(session_key) if callable(cached) and session_key else None
            identity = identity_of(source)
            if identity is not None:
                adapter = identity.adapter()  # receiving transport may differ from runtime profile
        if runner is None or adapter is None or getattr(adapter, "_client", None) is None:
            return tool_error("The current profile's connected Telegram bot is unavailable; no action was taken.")
        from model_tools import _run_async
        from tools.send_message_tool import _dispatch_on_gateway_loop
        result = _run_async(_dispatch_on_gateway_loop(
            runner, lambda: _request_for_session(runner, adapter, chat_id, user_id, action, params),
            "Telegram operation dispatch failed",
        ))
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        # RPC exception text can contain secrets or untrusted peer-provided strings.
        logger.warning("Telegram operation failed (%s)", type(exc).__name__)
        return tool_error(f"Telegram operation failed: {type(exc).__name__}")


async def _request_for_session(runner: Any, adapter: Any, chat_id: str, user_id: str,
                               action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute on the owning loop; deny stale or cross-profile routes before any RPC."""
    from hermes_constants import get_hermes_home
    from pathlib import Path
    from gateway.session_identity import identity_of
    session_key = get_session_env("HERMES_SESSION_KEY", "") or ""
    cached = getattr(runner, "_get_cached_session_source", None)
    source = cached(session_key) if callable(cached) and session_key else None
    thread_id = get_session_env("HERMES_SESSION_THREAD_ID", "") or None
    if source is None:
        source = adapter.build_source(
            chat_id, chat_type=get_session_env("HERMES_SESSION_CHAT_TYPE", "") or "dm",
            user_id=user_id, thread_id=thread_id,
            message_id=get_session_env("HERMES_SESSION_MESSAGE_ID", "") or None,
        )
    identity = identity_of(source)
    if identity is not None and identity.adapter() is not adapter:
        return {"error": "The Telegram conversation's receiving bot changed or is unavailable."}
    if (str(source.chat_id) != chat_id or str(source.user_id) != user_id
            or str(getattr(source, "thread_id", "") or "") != str(thread_id or "")
            or source.platform != Platform.TELEGRAM
            or getattr(source, "is_bot", False) or getattr(source, "profile_route_rejected", False)):
        return {"error": "The Telegram session route is stale or does not match this actor and topic."}
    home = adapter._settings_home(source)
    if home is None or Path(home).resolve() != Path(get_hermes_home()).resolve():
        return {"error": "The Telegram route does not belong to the active profile."}
    if not adapter._authorized(source):
        return {"error": "This Telegram account is not admitted in the active chat."}
    return await adapter.request_telegram_operation(source, action, params)


registry.register(name="telegram_client", toolset="telegram_admin", schema=TELEGRAM_CLIENT_SCHEMA,
                  handler=telegram_client)

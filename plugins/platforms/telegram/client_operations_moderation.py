"""Telegram moderation actions: timed mute, unmute, promote, demote and kick.

The bot account can perform these directly when it is an administrator. Targets
are resolved from an explicit ``user_id``/``username``/``message_id``, or from
the triggering message's replied-to sender or mentioned member. The current
chat is the only chat these actions can touch.
"""
from __future__ import annotations

import datetime
import re
from typing import Any

MODERATION_ACTIONS = frozenset({
    "user_mute", "user_unmute", "user_promote", "user_demote", "user_kick",
})

_TARGET_KEYS = {"user_id", "username", "message_id"}
_MIN_DURATION_SECONDS = 30
_MAX_DURATION_SECONDS = 366 * 24 * 60 * 60
_DURATION_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_DURATION_PART_RE = re.compile(r"(\d+)\s*([a-z]+)")
_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{3,32}$")

_MUTE_RIGHT_KEYS = frozenset({
    "send_messages", "send_media", "send_stickers", "send_gifs", "send_games",
    "send_inline", "embed_links", "send_polls", "send_photos", "send_videos",
    "send_roundvideos", "send_audios", "send_voices", "send_docs", "send_plain",
    "send_reactions", "change_info", "invite_users", "pin_messages", "manage_topics",
})
_DEFAULT_MUTE_RIGHTS = (
    "send_messages", "send_media", "send_stickers", "send_gifs", "send_games",
    "send_inline", "embed_links", "send_polls",
)
_ADMIN_RIGHT_KEYS = frozenset({
    "change_info", "post_messages", "edit_messages", "delete_messages", "ban_users",
    "invite_users", "pin_messages", "add_admins", "anonymous", "manage_call",
    "manage_topics",
})
_ADMIN_PRESETS = {
    "moderator": {
        "change_info": True, "post_messages": False, "edit_messages": False,
        "delete_messages": True, "ban_users": True, "invite_users": True,
        "pin_messages": True, "add_admins": False, "anonymous": False,
        "manage_call": True, "manage_topics": True,
    },
    "admin": {key: key != "anonymous" for key in _ADMIN_RIGHT_KEYS},
}

MODERATION_PARAMETERS = {
    "user_mute": {
        "user_id": "optional positive 64-bit user ID of the member to restrict",
        "username": "optional @username of the member to restrict",
        "message_id": "optional positive message ID whose sender is the member to restrict",
        "duration": "optional duration such as 30s, 10m, 2h, 3d, or 1w; omit for a mute until unmuted",
        "until_date": "optional unix timestamp or ISO-8601 datetime for the restriction end; cannot be combined with duration",
        "rights": "optional object of restriction booleans (true restricts): send_messages, send_media, send_stickers, send_gifs, send_games, send_inline, embed_links, send_polls, send_photos, send_videos, send_roundvideos, send_audios, send_voices, send_docs, send_plain, send_reactions, change_info, invite_users, pin_messages, manage_topics; defaults to all send rights",
    },
    "user_unmute": {
        "user_id": "optional positive 64-bit user ID of the member to restore",
        "username": "optional @username of the member to restore",
        "message_id": "optional positive message ID whose sender is the member to restore",
    },
    "user_promote": {
        "user_id": "optional positive 64-bit user ID of the member to promote",
        "username": "optional @username of the member to promote",
        "message_id": "optional positive message ID whose sender is the member to promote",
        "preset": "optional moderator, admin, or custom; default moderator, or custom when rights is given",
        "rights": "optional object of admin-right booleans for preset=custom: change_info, post_messages, edit_messages, delete_messages, ban_users, invite_users, pin_messages, add_admins, anonymous, manage_call, manage_topics",
        "title": "optional admin rank/title, at most 16 characters; channels and supergroups only",
    },
    "user_demote": {
        "user_id": "optional positive 64-bit user ID of the administrator to demote",
        "username": "optional @username of the administrator to demote",
        "message_id": "optional positive message ID whose sender is the administrator to demote",
    },
    "user_kick": {
        "user_id": "optional positive 64-bit user ID of the member to remove",
        "username": "optional @username of the member to remove",
        "message_id": "optional positive message ID whose sender is the member to remove",
    },
}


def validate_moderation_payload(action: str, params: dict[str, Any], int_value: Any) -> dict[str, Any]:
    """Validate one moderation action without guessing or silently widening it."""
    p = dict(params)
    allowed = set(_TARGET_KEYS)
    if action == "user_mute":
        allowed |= {"duration", "until_date", "rights"}
    elif action == "user_promote":
        allowed |= {"preset", "rights", "title"}
    extra = set(p) - allowed
    if extra:
        raise ValueError(f"{action} does not accept: {', '.join(sorted(extra))}")

    selectors = [key for key in _TARGET_KEYS if p.get(key) not in (None, "")]
    if len(selectors) > 1:
        raise ValueError("Supply only one of user_id, username, or message_id")
    clean: dict[str, Any] = {}
    if "user_id" in p and p["user_id"] not in (None, ""):
        clean["user_id"] = int_value(p["user_id"], "user_id", maximum=2**63 - 1)
    if "username" in p and p["username"] not in (None, ""):
        username = str(p["username"]).strip()
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError("username must be a Telegram @username of 3–32 letters, digits, or underscores")
        clean["username"] = username.lstrip("@")
    if "message_id" in p and p["message_id"] not in (None, ""):
        clean["message_id"] = int_value(p["message_id"], "message_id")

    if action == "user_mute":
        clean.update(_validate_mute_parameters(p))
    elif action == "user_promote":
        clean.update(_validate_promote_parameters(p))
    return clean


def _validate_mute_parameters(p: dict[str, Any]) -> dict[str, Any]:
    duration = p.get("duration")
    until_date = p.get("until_date")
    if duration not in (None, "") and until_date not in (None, ""):
        raise ValueError("duration and until_date cannot be combined")
    result: dict[str, Any] = {}
    if duration not in (None, ""):
        seconds = parse_mute_duration(duration)
        result["duration"] = str(duration).strip()
        result["duration_seconds"] = seconds
        result["until"] = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)
    elif until_date not in (None, ""):
        result["until"] = parse_until_date(until_date)
    else:
        result["until"] = None
    result["rights"] = _mute_rights(p.get("rights"))
    return result


def _validate_promote_parameters(p: dict[str, Any]) -> dict[str, Any]:
    raw_rights = p.get("rights")
    preset = str(p.get("preset") or ("custom" if raw_rights is not None else "moderator")).strip().lower()
    if preset not in {*_ADMIN_PRESETS, "custom"}:
        raise ValueError("preset must be moderator, admin, or custom")
    if preset == "custom":
        if not isinstance(raw_rights, dict) or not raw_rights:
            raise ValueError("preset=custom needs a non-empty rights object")
        rights = _admin_rights(raw_rights)
    else:
        if raw_rights is not None:
            raise ValueError("rights requires preset=custom; presets already define their rights")
        rights = dict(_ADMIN_PRESETS[preset])
    title = str(p.get("title") or "")
    if len(title) > 16:
        raise ValueError("title must be at most 16 characters")
    return {"preset": preset, "rights": rights, "title": title}


def parse_mute_duration(value: Any) -> int:
    """Parse ``10m``/``1 hour 30 minutes``/integer seconds into 30s–366d."""
    if isinstance(value, bool):
        raise ValueError("duration must be a duration or an integer number of seconds")
    if isinstance(value, (int, float)):
        seconds = int(value)
    else:
        text = str(value).strip().lower().replace(",", "")
        compact = re.sub(r"\s+", "", text)
        if compact.isdigit():
            seconds = int(compact)
        else:
            parts = _DURATION_PART_RE.findall(compact)
            if not parts or "".join(f"{number}{unit}" for number, unit in parts) != compact:
                raise ValueError("duration must look like 30s, 10m, 2h, 3d, or 1w")
            seconds = 0
            for number, unit in parts:
                if unit not in _DURATION_UNITS:
                    raise ValueError(f"unknown duration unit: {unit}")
                seconds += int(number) * _DURATION_UNITS[unit]
    if not _MIN_DURATION_SECONDS <= seconds <= _MAX_DURATION_SECONDS:
        raise ValueError("duration must be between 30 seconds and 366 days")
    return seconds


def parse_until_date(value: Any) -> datetime.datetime:
    """Parse a unix timestamp or ISO-8601 instant and bound it like Telegram does."""
    if isinstance(value, bool):
        raise ValueError("until_date must be a unix timestamp or ISO-8601 datetime")
    if isinstance(value, (int, float)):
        moment = datetime.datetime.fromtimestamp(float(value), tz=datetime.UTC)
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            moment = datetime.datetime.fromtimestamp(float(text), tz=datetime.UTC)
        else:
            try:
                moment = datetime.datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError("until_date must be a unix timestamp or ISO-8601 datetime") from exc
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=datetime.UTC)
    now = datetime.datetime.now(datetime.UTC)
    seconds = (moment - now).total_seconds()
    if not _MIN_DURATION_SECONDS <= seconds <= _MAX_DURATION_SECONDS:
        raise ValueError("until_date must be between 30 seconds and 366 days from now")
    return moment


def _mute_rights(raw: Any) -> dict[str, bool]:
    if raw is None:
        return {key: True for key in _DEFAULT_MUTE_RIGHTS}
    if not isinstance(raw, dict):
        raise ValueError("rights must be an object of restriction booleans")
    unknown = set(raw) - _MUTE_RIGHT_KEYS
    if unknown:
        raise ValueError(f"unsupported mute rights: {', '.join(sorted(unknown))}")
    if any(type(value) is not bool for value in raw.values()):
        raise ValueError("mute right values must be booleans")
    return dict(raw)


def _admin_rights(raw: dict[str, Any]) -> dict[str, bool]:
    unknown = set(raw) - _ADMIN_RIGHT_KEYS
    if unknown:
        raise ValueError(f"unsupported admin rights: {', '.join(sorted(unknown))}")
    if any(type(value) is not bool for value in raw.values()):
        raise ValueError("admin right values must be booleans")
    return {key: bool(raw.get(key, False)) for key in _ADMIN_RIGHT_KEYS}


def _utf16_slice(text: str, offset: int, length: int) -> str:
    """Telegram entity offsets are UTF-16 code units, not Python characters."""
    data = text.encode("utf-16-le")
    return data[offset * 2:(offset + length) * 2].decode("utf-16-le", "ignore")


class ModerationOperationsMixin:
    """Moderation half of ``TelegramClientOperations`` (see the facade module)."""

    async def request_moderation(self, source: Any, action: str,
                                 params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and run one moderation action, returning the tool response envelope."""
        try:
            clean_params = self._validate_payload(action, dict(params or {}))
            result = await self._execute_moderation(source, action, clean_params)
        except (TypeError, ValueError) as exc:
            return self._error("INVALID_ARGUMENT", str(exc))
        except Exception as exc:  # health: allow BLE001 -- the tool boundary reports RPC failures as structured errors
            return self._error(self._error_code(exc), self._safe_error(exc))
        return {"ok": True, "status": "complete", "action": action, "data": result}

    async def _execute_moderation(self, source: Any, action: str, p: dict[str, Any]) -> dict[str, Any]:
        from telethon import functions, types

        peer = await self._peer(source)
        user_id, input_peer = await self._resolve_moderation_target(source, p)
        if action in {"user_mute", "user_unmute"}:
            if not isinstance(peer, types.InputPeerChannel):
                raise ValueError("Per-user mute/unmute requires a supergroup or channel; this is a basic group")
            channel = types.InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)
            rights = (types.ChatBannedRights(until_date=p.get("until"), view_messages=False,
                                             **p.get("rights", {}))
                      if action == "user_mute" else
                      types.ChatBannedRights(until_date=None, view_messages=False))
            await self.adapter._mtproto_rich_request(functions.channels.EditBannedRequest(
                channel=channel, participant=input_peer, banned_rights=rights))
            if action == "user_unmute":
                return {"user_id": user_id, "muted": False}
            return {"user_id": user_id, "muted": True, "duration": p.get("duration"),
                    "until_date": p["until"].isoformat() if p.get("until") else None,
                    "rights": p.get("rights", {})}
        if action in {"user_promote", "user_demote"}:
            return await self._edit_admin(source, peer, user_id, input_peer, action, p)
        if action == "user_kick":
            return await self._kick_member(peer, user_id, input_peer)
        raise ValueError(f"Unsupported moderation action: {action}")

    async def _edit_admin(self, source: Any, peer: Any, user_id: int, input_peer: Any,
                          action: str, p: dict[str, Any]) -> dict[str, Any]:
        from telethon import functions, types

        input_user = types.InputUser(user_id=input_peer.user_id, access_hash=input_peer.access_hash)
        if isinstance(peer, types.InputPeerChannel):
            channel = types.InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)
            if action == "user_promote":
                rights = types.ChatAdminRights(**{key: bool(value) for key, value in p["rights"].items()})
                await self.adapter._mtproto_rich_request(functions.channels.EditAdminRequest(
                    channel=channel, user_id=input_user, admin_rights=rights, rank=p.get("title") or ""))
                return {"user_id": user_id, "promoted": True, "preset": p["preset"],
                        "rights": p["rights"], "title": p.get("title") or None}
            empty = types.ChatAdminRights(**{key: False for key in _ADMIN_RIGHT_KEYS})
            await self.adapter._mtproto_rich_request(functions.channels.EditAdminRequest(
                channel=channel, user_id=input_user, admin_rights=empty, rank=""))
            return {"user_id": user_id, "promoted": False}
        if isinstance(peer, types.InputPeerChat):
            if action == "user_promote" and p.get("title"):
                raise ValueError("A custom admin title is available only in a supergroup or channel")
            await self.adapter._mtproto_rich_request(functions.messages.EditChatAdminRequest(
                chat_id=peer.chat_id, user_id=input_user, is_admin=action == "user_promote"))
            if action == "user_promote":
                return {"user_id": user_id, "promoted": True, "preset": p["preset"], "rights": None}
            return {"user_id": user_id, "promoted": False}
        raise ValueError("Promote/demote requires a group, supergroup, or channel")

    async def _kick_member(self, peer: Any, user_id: int, input_peer: Any) -> dict[str, Any]:
        from telethon import functions, types

        if isinstance(peer, types.InputPeerChannel):
            channel = types.InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)
            await self.adapter._mtproto_rich_request(functions.channels.EditBannedRequest(
                channel=channel, participant=input_peer,
                banned_rights=types.ChatBannedRights(until_date=None, view_messages=True)))
            await self.adapter._mtproto_rich_request(functions.channels.EditBannedRequest(
                channel=channel, participant=input_peer,
                banned_rights=types.ChatBannedRights(until_date=None, view_messages=False)))
            return {"user_id": user_id, "kicked": True, "rejoin_allowed": True}
        if isinstance(peer, types.InputPeerChat):
            input_user = types.InputUser(user_id=input_peer.user_id, access_hash=input_peer.access_hash)
            await self.adapter._mtproto_rich_request(functions.messages.DeleteChatUserRequest(
                chat_id=peer.chat_id, user_id=input_user))
            return {"user_id": user_id, "kicked": True, "rejoin_allowed": False}
        raise ValueError("Kick requires a group, supergroup, or channel")

    async def _resolve_moderation_target(self, source: Any, p: dict[str, Any]) -> tuple[int, Any]:
        from telethon import types

        if p.get("user_id") is not None:
            user_id, peer = await self._resolve_user_id(p["user_id"])
        elif p.get("username"):
            user_id, peer = await self._resolve_username(p["username"])
        elif p.get("message_id") is not None:
            message = self._observed_message(source, p["message_id"])
            if message is None:
                message = await self._get_message(source, p["message_id"])
            user_id, peer = await self._target_from_message(message)
        else:
            user_id, peer = await self._target_from_trigger(source)
        if not isinstance(peer, types.InputPeerUser):
            raise ValueError("The moderation target must resolve to a user")
        return user_id, peer

    async def _target_from_trigger(self, source: Any) -> tuple[int, Any]:
        trigger = self._trigger_message(source)
        if trigger is not None:
            reply_id = getattr(trigger, "reply_to_msg_id", None)
            if reply_id:
                reply = self._observed_message(source, int(reply_id))
                if reply is None:
                    try:
                        reply = await self._get_message(source, int(reply_id))
                    except (TypeError, ValueError, RuntimeError):
                        reply = None
                if reply is not None:
                    return await self._target_from_message(reply)
            mentioned = await self._target_from_mentions(trigger)
            if mentioned is not None:
                return mentioned
        raise ValueError("No moderation target: pass user_id, username, or message_id, reply to the member, or mention them")

    def _trigger_message(self, source: Any) -> Any:
        raw_id = self._source_value(source, "message_id", None)
        if raw_id in (None, ""):
            return None
        try:
            return self._observed_message(source, int(raw_id))
        except (TypeError, ValueError):
            return None

    async def _target_from_message(self, message: Any) -> tuple[int, Any]:
        if message is None:
            raise ValueError("The moderation target message is unavailable")
        sender = getattr(message, "sender", None)
        getter = getattr(message, "get_sender", None)
        if sender is None and callable(getter):
            try:
                sender = await getter()
            except Exception:  # health: allow BLE001 -- a failed sender lookup falls back to sender_id below
                sender = None
        if sender is not None:
            sender_id = getattr(sender, "id", None)
            access_hash = getattr(sender, "access_hash", None)
            if sender_id is not None and access_hash is not None:
                return int(sender_id), self._input_peer_user(int(sender_id), access_hash)
        sender_id = getattr(message, "sender_id", None)
        if sender_id is None:
            from_id = getattr(message, "from_id", None)
            sender_id = getattr(from_id, "user_id", None)
        if sender_id is None:
            raise ValueError("The moderation target message has no user sender")
        if int(sender_id) <= 0:
            raise ValueError("The moderation target is a channel/anonymous sender, not a user")
        return await self._resolve_user_id(int(sender_id))

    async def _target_from_mentions(self, trigger: Any) -> tuple[int, Any] | None:
        text = str(getattr(trigger, "message", "") or "")
        bot_username = str(getattr(self.adapter, "_bot_username", "") or "").lstrip("@").lower()
        bot_id = getattr(self.adapter, "_bot_id", None)
        candidates: list[str] = []
        for entity in (getattr(trigger, "entities", None) or []):
            user_id = getattr(entity, "user_id", None)
            if user_id is not None:
                if bot_id is None or int(user_id) != int(bot_id):
                    candidates.append(str(user_id))
                continue
            if "mention" not in type(entity).__name__.lower():
                continue
            username = _utf16_slice(text, int(getattr(entity, "offset", 0)),
                                    int(getattr(entity, "length", 0))).lstrip("@")
            if username and username.lower() != bot_username:
                candidates.append(username)
        distinct = list(dict.fromkeys(candidates))
        if not distinct:
            return None
        if len(distinct) > 1:
            raise ValueError("More than one member is mentioned; pass user_id or username to choose the target")
        target = distinct[0]
        if target.isdigit():
            return await self._resolve_user_id(int(target))
        return await self._resolve_username(target)

    async def _resolve_username(self, username: str) -> tuple[int, Any]:
        peer = await self._input_entity("@" + str(username).lstrip("@"))
        user_id = getattr(peer, "user_id", None)
        if user_id is None:
            raise ValueError(f"Telegram could not resolve @{username} to a user")
        return int(user_id), peer

    async def _resolve_user_id(self, user_id: int) -> tuple[int, Any]:
        peer = await self._input_entity(int(user_id))
        resolved = getattr(peer, "user_id", user_id)
        return int(resolved), peer

    async def _input_entity(self, target: Any) -> Any:
        from telethon import types

        client = self._client()
        getter = getattr(client, "get_input_entity", None)
        try:
            peer = await getter(target) if callable(getter) else None
        except Exception:  # health: allow BLE001 -- an unknown peer falls back to an access-hash-less InputPeerUser
            peer = None
        if isinstance(peer, types.InputPeerUser):
            return peer
        if isinstance(peer, types.InputUser):
            return self._input_peer_user(int(peer.user_id), peer.access_hash)
        if peer is not None and getattr(peer, "id", None) is not None and getattr(peer, "access_hash", None) is not None:
            return self._input_peer_user(int(peer.id), peer.access_hash)
        if isinstance(target, int):
            # Telegram resolves a message sender without an explicit access hash
            # in many admin calls; a wrong ID still fails closed at the RPC.
            return self._input_peer_user(target, 0)
        raise ValueError(f"Telegram could not resolve {target} to a user")

    @staticmethod
    def _input_peer_user(user_id: int, access_hash: Any) -> Any:
        from telethon import types
        return types.InputPeerUser(user_id=int(user_id), access_hash=int(access_hash or 0))

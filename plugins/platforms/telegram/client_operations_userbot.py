"""Owner-only Telegram user-account helper for actions a bot account cannot perform.

The connected gateway identity is a bot. Some Telegram operations (BotFather
management, user-only RPCs, actions a bot is forbidden from calling) need a real
user account. The owner imports a portable Telethon string session once; this
module stores it under the profile home with ``0600`` and reuses it to run
bounded Telethon code in-process, where the profile's API credentials are
available even though terminal children strip them.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

USERBOT_ACTIONS = frozenset({"userbot_import", "userbot_status", "userbot_script"})
USERBOT_SCRIPT_TIMEOUT = 120.0
_MAX_CODE_CHARS = 20_000
_MAX_SESSION_CHARS = 65_536
_MAX_RESULT_CHARS = 8_000

USERBOT_PARAMETERS = {
    "userbot_import": {
        "session": "required owner-supplied Telethon StringSession for a user account; stored with mode 0600 and never echoed back",
    },
    "userbot_status": {},
    "userbot_script": {
        "code": "required async Python body run with a connected Telethon client; use await client(...), return a value, and print for progress; 120-second limit",
    },
}


def validate_userbot_payload(action: str, params: dict[str, Any]) -> dict[str, Any]:
    p = dict(params)
    if action == "userbot_import":
        if set(p) != {"session"}:
            raise ValueError("userbot_import accepts only session")
        session = p.get("session")
        if not isinstance(session, str) or not session.strip():
            raise ValueError("session must be a non-empty Telethon StringSession string")
        session = session.strip()
        if len(session) > _MAX_SESSION_CHARS:
            raise ValueError("session is too long for a Telethon StringSession")
        return {"session": session}
    if action == "userbot_status":
        if p:
            raise ValueError("userbot_status accepts no parameters")
        return {}
    if action == "userbot_script":
        if set(p) != {"code"}:
            raise ValueError("userbot_script accepts only code")
        code = p.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty Python async function body")
        if len(code) > _MAX_CODE_CHARS:
            raise ValueError(f"code must be at most {_MAX_CODE_CHARS} characters")
        return {"code": code}
    raise ValueError(f"Unsupported userbot action: {action}")


def _bounded(value: Any) -> Any:
    from agent.redact import redact_sensitive_text

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_sensitive_text(value)[:_MAX_RESULT_CHARS]
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return redact_sensitive_text(text)[:_MAX_RESULT_CHARS]


class UserbotOperationsMixin:
    """User-account half of ``TelegramClientOperations`` (see the facade module)."""

    def _userbot_is_owner(self, source: Any) -> bool:
        check = getattr(self.adapter, "_is_owner_user", None)
        if not callable(check):
            return False
        actor = str(getattr(source, "user_id", "") or "")
        try:
            return bool(check(actor, source=source))
        except (OSError, RuntimeError, ValueError, TypeError):
            return False

    def _userbot_dir(self, source: Any) -> Path:
        return Path(self._profile(source)).expanduser().resolve() / "telegram-userbot"

    def _userbot_paths(self, source: Any) -> tuple[Path, Path]:
        root = self._userbot_dir(source)
        return root / "session", root / "account.json"

    def _userbot_session(self, source: Any) -> str | None:
        path, _meta = self._userbot_paths(source)
        try:
            session = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if not session:
            return None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return session

    def _userbot_credentials(self) -> tuple[int, str]:
        from agent.secret_scope import get_secret

        raw_id = get_secret("TELEGRAM_API_ID")
        api_hash = get_secret("TELEGRAM_API_HASH")
        try:
            api_id = int(str(raw_id or "").strip())
        except (TypeError, ValueError):
            api_id = 0
        if api_id <= 0 or not str(api_hash or "").strip():
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be configured for the userbot helper")
        return api_id, str(api_hash).strip()

    @contextlib.asynccontextmanager
    async def _userbot_client(self, session_string: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id, api_hash = self._userbot_credentials()
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        try:
            await client.connect()
            yield client
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def request_userbot(self, source: Any, action: str,
                              params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and run one owner-only userbot action, returning the tool response envelope."""
        if not self._userbot_is_owner(source):
            return self._error("FORBIDDEN", "Userbot actions are owner-only")
        try:
            clean_params = self._validate_payload(action, dict(params or {}))
            data = await self._execute_userbot(source, action, clean_params)
        except PermissionError as exc:
            return self._error("FORBIDDEN", str(exc))
        except (TypeError, ValueError) as exc:
            return self._error("INVALID_ARGUMENT", str(exc))
        except Exception as exc:  # health: allow BLE001 -- the tool boundary reports RPC failures as structured errors
            return self._error(self._error_code(exc), self._safe_error(exc))
        return {"ok": True, "status": "complete", "action": action, "data": data}

    async def _execute_userbot(self, source: Any, action: str, p: dict[str, Any]) -> dict[str, Any]:
        if not self._userbot_is_owner(source):
            raise PermissionError("Userbot actions are owner-only")
        if action == "userbot_import":
            return await self._userbot_import(source, p["session"])
        if action == "userbot_status":
            return await self._userbot_status(source)
        if action == "userbot_script":
            return await self._userbot_script(source, p["code"])
        raise ValueError(f"Unsupported userbot action: {action}")

    async def _userbot_import(self, source: Any, session_string: str) -> dict[str, Any]:
        async with self._userbot_client(session_string) as client:
            if not await client.is_user_authorized():
                raise ValueError("The imported string session is not authorized; log in to that account again and export a fresh StringSession")
            me = await client.get_me()
            if me is None:
                raise ValueError("Telegram did not return an account for the imported string session")
            if getattr(me, "bot", False):
                raise ValueError("The imported string session is a bot account; import a user-account session")
            saved = client.session.save()
            saved = saved.strip() if isinstance(saved, str) and saved.strip() else session_string
            account = {
                "user_id": int(getattr(me, "id", 0) or 0),
                "username": str(getattr(me, "username", "") or "") or None,
                "name": " ".join(part for part in (
                    str(getattr(me, "first_name", "") or "").strip(),
                    str(getattr(me, "last_name", "") or "").strip()) if part) or None,
                "imported_at": int(time.time()),
            }
        self._write_userbot_session(source, saved, account)
        return {"configured": True, **account}

    async def _userbot_status(self, source: Any) -> dict[str, Any]:
        session_path, _meta = self._userbot_paths(source)
        session_string = self._userbot_session(source)
        if session_string is None:
            return {
                "configured": False,
                "session_path": str(session_path),
                "hint": "Import the user-account StringSession with action=userbot_import and params={session: ...}.",
            }
        async with self._userbot_client(session_string) as client:
            authorized = bool(await client.is_user_authorized())
            me = await client.get_me() if authorized else None
        data = {
            "configured": True,
            "authorized": authorized,
            "session_path": str(session_path),
            "user_id": int(getattr(me, "id", 0) or 0) if me is not None else None,
            "username": (str(getattr(me, "username", "") or "") or None) if me is not None else None,
            "name": (" ".join(part for part in (
                str(getattr(me, "first_name", "") or "").strip(),
                str(getattr(me, "last_name", "") or "").strip()) if part) or None) if me is not None else None,
        }
        if not authorized:
            data["hint"] = "The stored session is no longer authorized; re-import a fresh StringSession."
        return data

    async def _userbot_script(self, source: Any, code: str) -> dict[str, Any]:
        session_string = self._userbot_session(source)
        if session_string is None:
            raise ValueError("No userbot session is imported; use action=userbot_import with the user-account StringSession first")
        wrapped = "async def __hermes_userbot_main():\n" + textwrap.indent(code, "    ")
        try:
            compiled = compile(wrapped, "<telegram-userbot>", "exec")
        except SyntaxError as exc:
            raise ValueError(f"Userbot code has a syntax error: {exc.msg} (line {exc.lineno})") from None
        buffer = io.StringIO()
        async with self._userbot_client(session_string) as client:
            if not await client.is_user_authorized():
                raise ValueError("The stored userbot session is no longer authorized; re-import a fresh StringSession")
            from telethon import functions, types, utils
            env = {
                "__name__": "__hermes_userbot__",
                "asyncio": asyncio,
                "client": client,
                "functions": functions,
                "types": types,
                "utils": utils,
                "source": source,
                "chat_id": str(getattr(source, "chat_id", "") or ""),
                "thread_id": str(getattr(source, "thread_id", "") or "") or None,
                "result": None,
            }
            try:
                with contextlib.redirect_stdout(buffer):
                    exec(compiled, env)
                    result = await asyncio.wait_for(env["__hermes_userbot_main"](), timeout=USERBOT_SCRIPT_TIMEOUT)
                    env["result"] = result
            except TimeoutError:
                raise ValueError(f"Userbot code exceeded the {int(USERBOT_SCRIPT_TIMEOUT)}-second limit") from None
        return {
            "result": _bounded(env.get("result")),
            "output": _bounded(buffer.getvalue()[-_MAX_RESULT_CHARS:]),
            "account": {"user_id": int(getattr(getattr(client, "session", None), "user_id", 0) or 0) or None},
        }

    def _write_userbot_session(self, source: Any, session_string: str, account: dict[str, Any]) -> None:
        root = self._userbot_dir(source)
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        session_path, meta_path = self._userbot_paths(source)
        self._write_private(session_path, session_string + "\n")
        self._write_private(meta_path, json.dumps(account, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _write_private(path: Path, text: str) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, path)
            os.chmod(path, 0o600)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

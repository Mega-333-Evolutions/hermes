"""Telegram photo albums (one grouped media message, 10 per chunk) for the gateway adapter and the
standalone sender.

Telethon port of upstream's PTB-shaped ``telegram_album`` (new in the
v0.23.x gap): the behaviour is identical, the transport is MTProto —
``sendMediaGroup`` becomes ``messages.SendMultiMediaRequest`` and
``InputMediaPhoto`` becomes an uploaded ``InputMediaUploadedPhoto`` per
``InputSingleMedia``. No ``telegram`` (python-telegram-bot) import
anywhere in this file.

The fork previously had no album send at all: ``TelethonAdapter.
send_multiple_images`` (and ``send_standalone``) sent every photo as its
own message. A batch of still photos now lands as one grouped album.

Telegram's own 5xx-equivalent (an ``MTProto`` server error after the
bounded retry spent its budget) resends the SAME ``SendMultiMediaRequest``
with the same random ids, never photo by photo. A chunk that may
already be in the chat (a timeout, or a reset after the write:
``reply_lost``) is reported ambiguous and never resent. Any other failure
(a refusal, a flood wait, a connect failure whose request never left, a
local error) falls back photo by photo. Remote-URL photos and GIFs cannot
join the multi-media request (uploads need a local file; a GIF is an
animation, not a still photo) and go per-photo through the adapter's
normal image path, as upstream does for GIFs.

Topic routing mirrors ``TelethonAdapter._send_forum_media``: a threaded
send carries the verified ``topic_reply_header`` (anchor verified first);
a non-threaded send carries a plain ``InputReplyToMessage`` when the
metadata names a reply target. There is no stale-topic retry without the
thread: the fork's media path has no thread-not-found retry, and silently
de-threading an album would scatter it outside the topic it was bound to.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import Any, Optional
from urllib.parse import unquote

from gateway.platforms.base import SendResult

logger = logging.getLogger(__name__)

ALBUM_LIMIT = 10  # Telegram's media-group cap
_CAPTION_LIMIT_UTF16 = 1024  # photo captions

# MTProto errors that mean the chunk may already be in the chat: a timeout
# (the request may have executed server-side before the reply was lost), a
# Telegram 500 (``ServerError``) once the bounded retry spent its budget,
# an ``RpcCallFail`` after the same retries, or a reset/abort after the
# write. A flood wait is NOT reply-lost: it carries ``seconds`` and the
# per-photo fallback degrades gracefully instead.
_REPLY_LOST_MARKERS = (
    "timeout",
    "timedout",
    "servererror",
    "rpccallfail",
    "connectionreset",
    "connectionaborted",
)


def reply_lost(error: Exception) -> bool:
    """True when the chunk may already be in the chat: never resend it."""
    if isinstance(error, TimeoutError):
        return True
    return any(marker in type(error).__name__.lower() for marker in _REPLY_LOST_MARKERS)


def _caption_1024(text: Any) -> str:
    """Alt text truncated to Telegram's 1024-UTF-16-unit caption cap."""
    from gateway.platforms.base import utf16_len

    text = str(text or "")
    if utf16_len(text) <= _CAPTION_LIMIT_UTF16:
        return text
    out: list[str] = []
    total = 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if total + width > _CAPTION_LIMIT_UTF16:
            break
        out.append(ch)
        total += width
    return "".join(out)


def _local_path(url: str) -> Optional[str]:
    return unquote(url[7:]) if url.startswith("file://") else None


async def _send_one(
    adapter: Any, chat_id: str, url: str, alt: Optional[str], metadata: Optional[dict]
) -> SendResult:
    path = _local_path(url)
    try:
        if path is not None:
            return await adapter.send_image_file(
                chat_id=chat_id, image_path=path, caption=alt or None, metadata=metadata
            )
        return await adapter.send_image(
            chat_id=chat_id, image_url=url, caption=alt or None, metadata=metadata
        )
    except (
        Exception
    ) as exc:  # a per-photo failure is reported, never fatal to the batch
        logger.warning(
            "[%s] Photo send failed: %s", adapter.name, str(exc)[:300], exc_info=True
        )
        return SendResult(success=False, error=str(exc)[:300])


async def _send_each(
    adapter: Any,
    chat_id: str,
    photos: list[tuple],
    metadata: Optional[dict],
    human_delay: float,
    failed: list,
) -> bool:
    """One upload per photo (``send_image_file``'s photo -> document fallbacks); failures land in ``failed``."""
    delivered = False
    for photo_idx, (image_url, alt_text) in enumerate(photos):
        if human_delay > 0 and photo_idx > 0:
            await asyncio.sleep(human_delay)
        result = await _send_one(adapter, chat_id, image_url, alt_text, metadata)
        delivered = delivered or result.success
        if not result.success:
            failed.append(
                (_local_path(image_url) or image_url, result.error or "send failed")
            )
    return delivered


async def _open_chunk(
    adapter: Any, chunk: list[tuple], failed: list
) -> tuple[list, list]:
    """``(singles, deferred)`` for one album chunk: uploaded ``InputSingleMedia`` items for the
    local still photos, and the photos that must go per-photo (remote URLs, GIFs, missing files)."""
    from telethon import types

    singles: list = []
    deferred: list[tuple] = []
    for image_url, alt_text in chunk:
        path = _local_path(image_url)
        if path is None or os.path.splitext(path)[1].lower() == ".gif":
            deferred.append(
                (image_url, alt_text)
            )  # remote URL or animation: per-photo path
            continue
        if not await asyncio.to_thread(os.path.exists, path):
            logger.warning(
                "[%s] Skipping missing image in media group: %s", adapter.name, path
            )
            failed.append((path, "file not found"))
            continue
        try:
            _handle, media, _image = await adapter._client._file_to_media(path)
        except Exception as exc:  # health: allow BLE001 -- one photo's upload failure is recorded and the chunk continues; _file_to_media raises OSError, ValueError and Telethon RPC errors alike
            failed.append((path, str(exc)[:300]))
            continue
        if media is None:
            failed.append((path, "Telegram media file could not be prepared"))
            continue
        singles.append(
            types.InputSingleMedia(
                media=media,
                message=_caption_1024(alt_text),
                random_id=secrets.randbits(63) or 1,
            )
        )
    return singles, deferred


async def _reply_header(adapter: Any, chat_id: str, metadata: Optional[dict]) -> Any:
    """Reply header for the chunk: verified topic header when threaded, plain reply otherwise."""
    from telethon import types

    thread_id = adapter._reply_top_target(metadata)
    if thread_id is None:
        reply_id = adapter._reply_target(None, metadata)
        return (
            types.InputReplyToMessage(reply_to_msg_id=int(reply_id))
            if reply_id is not None
            else None
        )
    from .topic_delivery import topic_reply_header, verified_topic_reply

    peer = await adapter._input_peer_for(chat_id)
    reply_id = adapter._reply_target(None, metadata) or thread_id
    reply_id = await verified_topic_reply(adapter, peer, reply_id, thread_id, metadata)
    if reply_id is None and (metadata or {}).get("telegram_send_in_topic"):
        reply_id = thread_id
    if reply_id is None:
        raise RuntimeError(
            "Strict Telegram media reply refused: anchor is not verified in this topic"
        )
    return topic_reply_header(reply_id, thread_id)


async def _send_chunk(
    adapter: Any, chat_id: str, singles: list, metadata: Optional[dict]
) -> None:
    """One ``SendMultiMediaRequest`` for the chunk, via the adapter's bounded retry (same request
    object, same ``random_id``\\ s); raises the send's error."""
    from telethon import functions

    peer = await adapter._input_peer_for(chat_id)
    request = functions.messages.SendMultiMediaRequest(
        peer=peer,
        multi_media=singles,
        reply_to=await _reply_header(adapter, chat_id, metadata),
    )
    await adapter._request_with_bounded_retry(request)


async def send_photo_album(
    adapter: Any,
    chat_id: str,
    photos: list[tuple],
    metadata: Optional[dict] = None,
    human_delay: float = 0.0,
    *,
    delivered: bool = False,
    per_photo_fallback: bool = True,
) -> SendResult:
    """Send ``(url, alt)`` photos as albums. ``success`` = at least one photo delivered or possibly
    delivered; ``raw_response`` carries ``failed`` (``[(photo, error)]``), ``refused`` (when
    ``per_photo_fallback`` is off: the first refused chunk's photos and every later one, in order,
    for the caller to send its own way) and ``ambiguous`` (a chunk was sent but never confirmed)
    whenever any applies."""
    failed: list[tuple[str, str]] = []
    refused: list[tuple] = []
    ambiguous = False
    chunks = [photos[i : i + ALBUM_LIMIT] for i in range(0, len(photos), ALBUM_LIMIT)]
    for chunk_idx, chunk in enumerate(chunks):
        if human_delay > 0 and chunk_idx > 0:
            await asyncio.sleep(human_delay)
        singles, deferred = await _open_chunk(adapter, chunk, failed)
        sent = list(
            chunk
        )  # every photo in this chunk, for the fallback / refused accounting
        fell_back = False
        if singles:
            logger.info(
                "[%s] Sending media group of %d photo(s) (chunk %d/%d)",
                adapter.name,
                len(singles),
                chunk_idx + 1,
                len(chunks),
            )
            try:
                await _send_chunk(adapter, chat_id, singles, metadata)
                delivered = True
            except Exception as err:
                if reply_lost(err):
                    logger.warning(
                        "[%s] send multi-media reply lost (chunk %d/%d); not resending, it may have been posted: %s",
                        adapter.name,
                        chunk_idx + 1,
                        len(chunks),
                        str(err)[:300],
                        exc_info=True,
                    )
                    ambiguous = delivered = True
                else:
                    logger.warning(
                        "[%s] send multi-media refused (chunk %d/%d), falling back to per-image: %s",
                        adapter.name,
                        chunk_idx + 1,
                        len(chunks),
                        str(err)[:300],
                        exc_info=True,
                    )
                    if per_photo_fallback:
                        delivered = (
                            await _send_each(
                                adapter, chat_id, sent, metadata, human_delay, failed
                            )
                            or delivered
                        )
                        fell_back = True  # `sent` already covered the deferred photos
                    else:
                        refused += sent + [
                            photo
                            for later in chunks[chunk_idx + 1 :]
                            for photo in later
                        ]
                        break
        if deferred and not fell_back:
            # Remote URLs / GIFs ride the per-photo path.
            delivered = (
                await _send_each(
                    adapter, chat_id, deferred, metadata, human_delay, failed
                )
                or delivered
            )
    if not chunks:
        if per_photo_fallback:
            delivered = (
                await _send_each(
                    adapter, chat_id, photos, metadata, human_delay, failed
                )
                or delivered
            )
        else:
            refused = list(photos)
    detail = (
        {"failed": failed, "refused": refused, "ambiguous": ambiguous}
        if failed or refused or ambiguous
        else None
    )
    error = (
        None
        if delivered or refused
        else "all images failed to send" + (f": {failed[0][1]}" if failed else "")
    )
    return SendResult(success=delivered, error=error, raw_response=detail)

"""Message-edit protocol: what an edit turn carries and how it is answered.

The turn recovers the old text and the previous reply from the loaded
history (the corrected text is appended as a new row, never mutating past
context), gets a one-shot note telling it what changed, and the final send
decides whether to edit the previous reply in place, send nothing (the
correction changed nothing), or send a new message.
"""
from __future__ import annotations

from gateway.message_edits import (
    EDIT_UNCHANGED_SENTINEL,
    build_edit_note,
    decide_edit_correction,
    find_edit_context,
)


def _history():
    return [
        {"role": "user", "content": "the ghost is down", "message_id": "400"},
        {"role": "assistant", "content": "The ghost server is unreachable."},
        {"role": "user", "content": "and the db too", "message_id": "401"},
        {"role": "assistant", "content": "Checking the database as well."},
    ]


def test_find_edit_context_recovers_old_text_and_previous_reply():
    """The edited user row gives the old text; the assistant row after it is the reply."""
    old_text, previous_reply = find_edit_context(_history(), "400")
    assert old_text == "the ghost is down"
    assert previous_reply == "The ghost server is unreachable."


def test_find_edit_context_takes_the_latest_row_for_a_twice_edited_message():
    """A message edited twice: the old text is the most recent row before this edit."""
    history = _history() + [
        {"role": "user", "content": "the host is down", "message_id": "400"},
        {"role": "assistant", "content": "The host server is unreachable."},
    ]
    old_text, previous_reply = find_edit_context(history, "400")
    assert old_text == "the host is down"
    assert previous_reply == "The host server is unreachable."


def test_find_edit_context_returns_nothing_when_the_message_is_unknown():
    """No user row with that id: no old text, no previous reply, no note."""
    assert find_edit_context(_history(), "999") == (None, None)


def test_find_edit_context_returns_no_reply_when_the_agent_had_not_replied():
    """The edit landed before any reply: the old text is known, the reply is not."""
    history = [{"role": "user", "content": "the ghost is down", "message_id": "400"}]
    assert find_edit_context(history, "400") == ("the ghost is down", None)


def test_find_edit_context_ignores_non_text_content():
    """A media-only row has no text to compare against."""
    history = [{"role": "user", "content": [{"type": "image"}], "message_id": "400"}]
    assert find_edit_context(history, "400") == (None, None)


def test_build_edit_note_is_none_without_old_text():
    """No old text means no note: the turn just answers the new text."""
    assert build_edit_note(old_text=None) is None
    assert build_edit_note(old_text="   ") is None


def test_build_edit_note_before_any_reply_names_the_original():
    """The agent had not replied: the note says so and quotes the original."""
    note = build_edit_note(old_text="the ghost is down")
    assert note is not None
    assert "the ghost is down" in note
    assert "before you replied" in note
    assert EDIT_UNCHANGED_SENTINEL not in note


def test_build_edit_note_with_a_correctable_reply_gives_the_unchanged_protocol():
    """A previous reply that can be edited in place: the note carries the
    UNCHANGED protocol and the previous reply for comparison."""
    note = build_edit_note(
        old_text="the ghost is down",
        previous_reply_text="The ghost server is unreachable.",
        can_edit_reply=True,
    )
    assert note is not None
    assert "the ghost is down" in note
    assert "The ghost server is unreachable." in note
    assert "replace your previous reply" in note
    assert EDIT_UNCHANGED_SENTINEL in note


def test_build_edit_note_with_an_uncorrectable_reply_has_no_unchanged_protocol():
    """A previous reply with no id to edit: the answer goes out as a new reply,
    so the model is never told the UNCHANGED sentinel."""
    note = build_edit_note(
        old_text="the ghost is down",
        previous_reply_text="The ghost server is unreachable.",
        can_edit_reply=False,
    )
    assert note is not None
    assert "as a new reply" in note
    assert EDIT_UNCHANGED_SENTINEL not in note


def test_decide_edit_correction_edits_the_previous_reply():
    """A changed answer with a previous reply id: edit it in place."""
    assert decide_edit_correction(final_text="The host is down.", previous_reply_id="900") == "edit"


def test_decide_edit_correction_sends_nothing_on_unchanged():
    """The exact UNCHANGED sentinel: the previous reply stands, send nothing."""
    assert decide_edit_correction(final_text="UNCHANGED", previous_reply_id="900") == "unchanged"
    assert decide_edit_correction(final_text="  UNCHANGED  ", previous_reply_id="900") == "unchanged"


def test_decide_edit_correction_sends_a_new_message_without_a_reply_id():
    """No previous reply id (cache cold, restart): a normal new message."""
    assert decide_edit_correction(final_text="The host is down.", previous_reply_id=None) == "send"


def test_decide_edit_correction_treats_a_near_sentinel_as_an_answer():
    """Only the exact sentinel means unchanged; a lookalike is a real answer
    that replaces the previous reply."""
    assert decide_edit_correction(final_text="unchanged", previous_reply_id="900") == "edit"
    assert decide_edit_correction(final_text="UNCHANGED!", previous_reply_id="900") == "edit"

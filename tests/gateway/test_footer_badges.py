"""Footer badges: the /footer fields as tappable badges (Beta).

The builder returns (label, callback data) pairs in footer order; the tap
answers carry the detail — provider + full model name, token counts, full
path — in the callback data itself, so no server-side lookup is needed.
"""

from __future__ import annotations

import pytest

from gateway.runtime_footer import (
    build_footer_badges,
    footer_badges_enabled,
    format_footer_badge_row,
    format_runtime_footer,
)


def test_badges_off_by_default():
    assert footer_badges_enabled({}, "telegram") is False
    assert footer_badges_enabled({"platforms": {"telegram": {}}}, "telegram") is False


def test_badges_are_telegram_only():
    cfg = {"platforms": {"telegram": {"footer_badges": True}}}
    assert footer_badges_enabled(cfg, "telegram") is True
    assert footer_badges_enabled(cfg, "cli") is False
    assert footer_badges_enabled(cfg, None) is False


def test_badges_mirror_the_footer_line(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cwd = str(tmp_path / "projects" / "hermes")
    badges = build_footer_badges(
        model="openrouter/nousresearch/hermes-4-405b", provider="openrouter",
        context_tokens=38400, context_length=256000, cwd=cwd,
    )
    assert [label for label, _ in badges] == ["hermes-4-405b", "15%", "~/projects/hermes"]
    assert format_runtime_footer(
        model="openrouter/nousresearch/hermes-4-405b",
        context_tokens=38400, context_length=256000, cwd=cwd,
        fields=("model", "context_pct", "cwd")) == "hermes-4-405b · 15% · ~/projects/hermes"


def test_model_badge_carries_provider_and_full_name():
    assert build_footer_badges(
        model="nousresearch/hermes-4-405b", provider="agentrouter",
        context_tokens=0, context_length=None, cwd="",
    ) == [("hermes-4-405b", "fb:m:agentrouter|nousresearch/hermes-4-405b")]


def test_context_badge_carries_raw_counts():
    assert build_footer_badges(
        model="", provider="", context_tokens=38400, context_length=256000, cwd="",
    ) == [("15%", "fb:c:38400|256000")]


def test_cwd_badge_carries_home_relative_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert build_footer_badges(
        model="", provider="", context_tokens=0, context_length=None,
        cwd=str(tmp_path / "projects" / "hermes"),
    ) == [("~/projects/hermes", "fb:p:~/projects/hermes")]


def test_badge_data_never_exceeds_telegram_limit():
    badges = build_footer_badges(
        model="vendor/" + "x" * 100, provider="a-very-long-provider-name",
        context_tokens=0, context_length=None, cwd="",
    )
    assert badges
    for _, data in badges:
        assert len(data.encode("utf-8")) <= 64


def test_badge_row_html_is_one_primary_button_row():
    row = format_footer_badge_row([
        ("hermes-4-405b", "fb:m:agentrouter|nousresearch/hermes-4-405b"),
        ("15%", "fb:c:38400|256000"),
    ])
    assert row.startswith("<tg-button-row>") and row.endswith("</tg-button-row>")
    assert row.count("<tg-button ") == 2
    assert 'data="fb:m:agentrouter|nousresearch/hermes-4-405b"' in row
    assert 'data="fb:c:38400|256000"' in row
    assert 'style="primary"' in row
    assert format_footer_badge_row([]) == ""

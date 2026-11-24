"""Recent providers and models in the Telegram provider-management UI.

A Recent filter on the providers screen lists recently used providers,
newest first; a Recent toggle on a provider's model list lists only
that provider's recent models. Recents come from the profile config
(`_recent_providers` / `_recent_models`), written by every durable model
switch. A removed provider or model never appears as a dead entry.
"""

import pytest

from plugins.platforms.telegram.provider_management_ui import TelegramProviderManagementUI
from tests.plugins.platforms.telegram.test_provider_management_ui import (
    CallbackEvent, FakeAdapter, Source, _provider,
)


def _recent_buttons(markup):
    """(label, callback data) pairs for every keyboard button, in render order."""
    return [
        (button.text, button.type.data.decode("utf-8"))
        for row in markup.rows for button in row.buttons
    ]


def _write_recents(tmp_path, providers=None, models=None):
    lines = []
    if providers is not None:
        lines.append("_recent_providers:")
        lines += [f"  - {slug}" for slug in providers]
    if models is not None:
        lines.append("_recent_models:")
        for slug, names in models.items():
            lines.append(f"  {slug}:")
            lines += [f"    - {name}" for name in names]
    (tmp_path / "config.yaml").write_text("\n".join(lines) + "\n")


@pytest.mark.asyncio
async def test_recent_filter_lists_recent_providers_newest_first(tmp_path):
    """The providers screen offers a Recent filter listing recently used
    providers newest first; a removed provider never appears as a dead row,
    and the full list is unchanged."""
    ui = TelegramProviderManagementUI(FakeAdapter())
    providers = [
        _provider(),
        _provider(slug="nous", name="Nous", is_current=False, is_profile_default=False,
                  models=["model-x"], total_models=1, default_model="model-x",
                  profile_default_model="model-x"),
        _provider(slug="nvidia", name="Nvidia", is_current=False, is_profile_default=False,
                  models=["n1"], total_models=1, default_model="n1",
                  profile_default_model="n1"),
    ]
    state = ui._new_state(
        Source(), providers, current_model="vendor/model-a",
        current_provider="openrouter", session_key="s", profile_home=tmp_path,
        view="providers",
    )
    state["message_id"] = "55"
    _, markup = ui._render(state)
    assert not any(label.startswith("Recent") for label, _ in _recent_buttons(markup))
    _write_recents(tmp_path, providers=["nvidia", "openrouter", "gone-provider"])
    _, markup = ui._render(state)
    recent = next(data for label, data in _recent_buttons(markup)
                  if label.startswith("Recent"))
    assert recent.endswith(":filter:recent")
    assert await ui.handle_callback(CallbackEvent(55), Source(), recent)
    assert state["filter"] == "recent"
    _, markup = ui._render(state)
    listed = [(label, data) for label, data in _recent_buttons(markup)
              if ":provider:" in data]
    assert [label for label, _ in listed] == ["Nvidia · Missing", "Current · OpenRouter · Missing"]
    assert listed[0][1].endswith(":provider:2")
    assert listed[1][1].endswith(":provider:0")
    # Every callback rotates the state id, so the All button must be read
    # fresh from the re-rendered card, like a real second tap.
    all_data = next(data for label, data in _recent_buttons(markup) if label == "All")
    assert await ui.handle_callback(CallbackEvent(55), Source(), all_data)
    assert state["filter"] == "all"
    _, markup = ui._render(state)
    listed = [(label, data) for label, data in _recent_buttons(markup)
              if ":provider:" in data]
    assert [label for label, _ in listed] == [
        "Current · OpenRouter · Missing", "Nous · Missing", "Nvidia · Missing"]


@pytest.mark.asyncio
async def test_recent_models_toggle_lists_only_that_providers_recents(tmp_path):
    """A provider's model list offers a Recent toggle listing only that
    provider's recent models, newest first; other providers' recents never
    leak in, and tapping one routes through the existing select action."""
    ui = TelegramProviderManagementUI(FakeAdapter())
    providers = [
        _provider(slug="nvidia", name="Nvidia", is_current=False, is_profile_default=False,
                  models=["n1", "n2", "n3"], total_models=3, default_model="n1",
                  profile_default_model="n1"),
        _provider(slug="nous", name="Nous", is_current=True,
                  models=["nx"], total_models=1, default_model="nx",
                  profile_default_model="nx"),
    ]
    state = ui._new_state(
        Source(), providers, current_model="nx",
        current_provider="nous", session_key="s", profile_home=tmp_path,
        view="models", selected_provider="nvidia",
    )
    state["message_id"] = "55"
    _, markup = ui._render(state)
    assert not any(label == "Recent" for label, _ in _recent_buttons(markup))
    _write_recents(tmp_path, models={"nvidia": ["n3", "n1"], "nous": ["nx"]})
    _, markup = ui._render(state)
    recent = next(data for label, data in _recent_buttons(markup) if label == "Recent")
    assert recent.endswith(":recent-models")
    assert await ui.handle_callback(CallbackEvent(55), Source(), recent)
    assert state.get("recent_models_only") is True
    _, markup = ui._render(state)
    selects = [(label, data) for label, data in _recent_buttons(markup)
               if ":select:" in data]
    assert [label for label, _ in selects] == ["n3", "n1"]
    assert selects[0][1].endswith(":select:2")
    assert selects[1][1].endswith(":select:0")
    # Every callback rotates the state id, so the Recent button must be read
    # fresh from the re-rendered card, like a real second tap.
    recent = next(data for label, data in _recent_buttons(markup) if label == "Recent")
    assert await ui.handle_callback(CallbackEvent(55), Source(), recent)
    assert state.get("recent_models_only") is False
    _, markup = ui._render(state)
    selects = [(label, data) for label, data in _recent_buttons(markup)
               if ":select:" in data]
    assert [label for label, _ in selects] == ["n1", "n2", "n3"]

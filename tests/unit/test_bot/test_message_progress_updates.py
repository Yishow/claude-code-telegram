"""Tests for Telegram progress update formatting."""

from types import SimpleNamespace

from src.bot.handlers.message import _format_progress_update


async def test_progress_update_formats_tool_start():
    """tool/pre events should produce a running-tool message."""
    update = SimpleNamespace(
        type="tool",
        content="bash",
        metadata={"tool_name": "bash", "action": "pre"},
        tool_calls=None,
    )

    text = await _format_progress_update(update)

    assert text is not None
    assert "Running tool" in text
    assert "bash" in text


async def test_progress_update_formats_reasoning_delta():
    """reasoning events should render concise reasoning text."""
    update = SimpleNamespace(
        type="reasoning",
        content="Thinking about the next command",
        metadata=None,
        tool_calls=None,
    )

    text = await _format_progress_update(update)

    assert text is not None
    assert "Reasoning" in text
    assert "Thinking about the next command" in text


async def test_progress_update_formats_result_delta():
    """result deltas should surface a generating-response indicator."""
    update = SimpleNamespace(
        type="result",
        content="Partial answer",
        metadata=None,
        tool_calls=None,
    )

    text = await _format_progress_update(update)

    assert text is not None
    assert "Generating response" in text
    assert "Partial answer" in text


async def test_progress_update_formats_assistant_tool_calls_without_helpers():
    """assistant tool-call events should not require helper methods on the object."""
    update = SimpleNamespace(
        type="assistant",
        content=None,
        metadata=None,
        tool_calls=[{"name": "Read"}, {"name": "Bash"}],
    )

    text = await _format_progress_update(update)

    assert text is not None
    assert "Using tools" in text
    assert "Read" in text
    assert "Bash" in text

# Core/tests/test_tool_call_debris.py
#
# An unparsed tool call stays in the content and reaches the TTS layer.
# The first case below is the exact string FRED spoke on 2026-08-04.

from llm.llm_client import LLMClient

strip = LLMClient._strip_thinking


def test_the_string_that_was_actually_spoken():
    spoken = strip(
        '{"path":"daily/2026-08"}We need to list directory.{"path":"daily"}'
        "The date check is done."
    )
    assert "{" not in spoken and '"path"' not in spoken
    assert "The date check is done." in spoken


def test_tool_call_tags_go_too():
    assert strip('<tool_call>{"name": "get_time"}</tool_call>Nearly ten.') == "Nearly ten."


def test_ordinary_replies_are_untouched():
    for reply in [
        "It's 10:42 PM on Tuesday, sir.",
        "The next reminder is at 4:55 PM.",
        "I couldn't find that file.",
    ]:
        assert strip(reply) == reply


def test_debris_inside_a_sentence_does_not_weld_words_together():
    assert strip('The date{"path":"x"}is confirmed.') == "The date is confirmed."

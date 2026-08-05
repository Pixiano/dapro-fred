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


# The streamed path yields token-sized pieces, so a leaked call arrives
# split across deltas and strip() never sees a whole object. Second case
# is what FRED actually spoke on 2026-08-05.
def _streamed(text, size=4):
    chunks = [text[i:i + size] for i in range(0, len(text), size)]
    client = LLMClient.__new__(LLMClient)
    client._cloud_stream = lambda *a, **k: (
        {"choices": [{"delta": {"content": c}}]} for c in chunks
    )
    return "".join(client.generate_stream([], local_only=False))


def test_nested_arguments_object_leaves_nothing_behind():
    # The 2026-08-05 leak: the inner {} matched first and the outer
    # wrapper was spoken.
    assert strip('{"name": "list_tasks", "arguments": {}}') == ""
    assert strip('{"name": "x", "arguments": {"a": {"b": 1}}}Done.') == "Done."


def test_streamed_tool_call_is_never_spoken():
    assert _streamed('{"name": "list_tasks", "arguments": {}}') == ""
    assert _streamed('{"name": "list_tasks", "arguments": }') == ""
    assert _streamed('Sure.{"name": "x"}Done.') == "Sure.Done."


def test_streamed_ordinary_reply_survives():
    assert _streamed("It's 10:42 PM on Tuesday, sir.") == "It's 10:42 PM on Tuesday, sir."

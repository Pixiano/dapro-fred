# Confirmed 2026-08-03: a saved task whose markdown link had no separate
# label (just the raw URL as both label and href) made Kokoro read the
# whole address out character by character. clean_for_speech() now
# collapses any URL — markdown-linked or bare — down to just its
# hostname's first label before it ever reaches TTS.

from audio.tts_kokoro import clean_for_speech


def test_markdown_link_with_a_real_label_speaks_only_the_label():
    text = "Visit [Qwen3.5 Docs](https://unsloth.ai/docs/models/qwen3.5) for later"
    assert clean_for_speech(text) == "Visit Qwen3.5 Docs for later"


def test_markdown_link_whose_label_is_the_raw_url_collapses_to_the_hostname():
    text = "Visit [https://example.com/report](https://example.com/report) for later"
    assert clean_for_speech(text) == "Visit example for later"


def test_bare_url_never_wrapped_in_markdown_also_collapses():
    text = "Check https://colab.research.google.com/github/foo/bar for details"
    assert clean_for_speech(text) == "Check colab for details"


def test_plain_text_with_no_links_is_unaffected():
    text = "Sir, your goals for today are logged."
    assert clean_for_speech(text) == text


def test_bracket_stripping_does_not_silently_drop_task_status():
    # The bracket fix above (list_scheduled job ids) would have also
    # eaten list_tasks()'s "[open]"/"[done]" tags — that's exactly why
    # daily_tasks.list_tasks() was changed to say "Open:"/"Done:"
    # instead of bracket-tagging. Pin the actual format here so a
    # future revert of either side gets caught.
    text = "Open: Study SS (History)\nDone: Chemistry journal"
    assert clean_for_speech(text) == text


def test_list_scheduled_job_ids_are_not_spoken():
    # Confirmed 2026-08-03: list_scheduled()'s own bracketed job id
    # ("[reminder_1785718306_1]") was reaching Kokoro and being read out
    # as a string of digits.
    text = '- [reminder_1785718306_1] Reminder: "JEE Live Class" — today at 6:00 PM'
    cleaned = clean_for_speech(text)
    assert "reminder_1785718306_1" not in cleaned
    assert "[" not in cleaned and "]" not in cleaned
    assert 'Reminder: "JEE Live Class" — today at 6:00 PM' in cleaned

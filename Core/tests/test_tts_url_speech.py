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


def test_latex_frac_speaks_as_over():
    text = r"The answer is \frac{3}{4} of the total."
    assert clean_for_speech(text) == "The answer is 3 over 4 of the total."


def test_bare_slash_fraction_speaks_as_over():
    text = "About 3/4 of the class passed."
    assert clean_for_speech(text) == "About 3 over 4 of the class passed."


def test_latex_sqrt_speaks_as_square_root_of():
    text = r"The result is \sqrt{16} exactly."
    assert clean_for_speech(text) == "The result is square root of 16 exactly."


def test_exponent_squared_and_cubed_use_words():
    assert clean_for_speech("x^2 plus y^3") == "x squared plus y cubed"


def test_exponent_other_power_speaks_as_to_the_power():
    assert clean_for_speech("2^10 is 1024") == "2 to the power 10 is 1024"


def test_latex_symbols_speak_as_words():
    text = r"a \times b \pm c \leq d \geq e \neq f \approx g, near \infty, using \pi"
    assert clean_for_speech(text) == (
        "a times b plus or minus c less than or equal to d greater than or "
        "equal to e not equal to f approximately g, near infinity , using pi"
    )


def test_bare_star_multiplication_speaks_as_times():
    text = "3 * 4 equals 12"
    assert clean_for_speech(text) == "3 times 4 equals 12"


def test_list_scheduled_job_ids_are_not_spoken():
    # Confirmed 2026-08-03: list_scheduled()'s own bracketed job id
    # ("[reminder_1785718306_1]") was reaching Kokoro and being read out
    # as a string of digits.
    text = '- [reminder_1785718306_1] Reminder: "JEE Live Class" — today at 6:00 PM'
    cleaned = clean_for_speech(text)
    assert "reminder_1785718306_1" not in cleaned
    assert "[" not in cleaned and "]" not in cleaned
    assert 'Reminder: "JEE Live Class" — today at 6:00 PM' in cleaned

# Core/tests/test_native_template_render.py
#
# Bonsai's chat template only suppresses <think> when enable_thinking is
# an actual jinja variable set False — create_chat_completion() can't
# pass that through (fixed signature, no **kwargs), so llm_client.py
# calls the tier's own Jinja2ChatFormatter/chat_handler directly instead
# (see llm_client._native_call). This is a pure template-render check
# against the model's REAL embedded chat_template (read from the GGUF,
# not a hand-copied string that could drift) — no GPU inference, so it
# runs in CI/normal test time despite needing a one-time vocab-only load.

import pytest
from pathlib import Path

from llama_cpp import Llama
from llama_cpp.llama_chat_format import Jinja2ChatFormatter

_BONSAI_PATH = Path(
    r"C:\Users\Dhiraj Vatsal\.lmstudio\models\lmstudio-community"
    r"\Bonsai-27B-GGUF\Bonsai-27B-Q1_0.gguf"
)

pytestmark = pytest.mark.skipif(
    not _BONSAI_PATH.exists(), reason="Bonsai GGUF not present on this machine"
)


@pytest.fixture(scope="module")
def formatter():
    model = Llama(model_path=str(_BONSAI_PATH), vocab_only=True, verbose=False)
    # token_get_text, not detokenize() — see llm_client._native_call's
    # comment: detokenize() renders special tokens as "", which broke
    # real generation (empty stop string matched after one token).
    return Jinja2ChatFormatter(
        template=model.metadata["tokenizer.chat_template"],
        eos_token=model._model.token_get_text(model.token_eos()),
        bos_token=model._model.token_get_text(model.token_bos()),
        stop_token_ids=[model.token_eos()],
    )


def test_enable_thinking_false_closes_the_think_block(formatter):
    messages = [
        {"role": "system", "content": "You are FRED."},
        {"role": "user", "content": "What's 2+2?"},
    ]
    result = formatter(messages=messages, enable_thinking=False)
    assert "<|im_start|>assistant\n<think>\n\n</think>\n\n" in result.prompt


def test_tools_render_without_raising(formatter):
    messages = [{"role": "user", "content": "What time is it?"}]
    tools = [{
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current time.",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    result = formatter(messages=messages, tools=tools, enable_thinking=False)
    assert "<tools>" in result.prompt
    assert "get_current_time" in result.prompt


def test_prior_think_block_in_history_renders_without_raising(formatter):
    messages = [
        {"role": "user", "content": "Explain gravity."},
        {
            "role": "assistant",
            "content": "<think>\nSome reasoning here\n</think>\n\nGravity pulls things down.",
        },
        {"role": "user", "content": "And light?"},
    ]
    result = formatter(messages=messages, enable_thinking=False)
    # Only the latest turn's <think> block should survive per the
    # template's own ns.last_query_index logic — the historical one is
    # collapsed into non-thinking form.
    assert result.prompt.count("Some reasoning here") == 0
    assert "Gravity pulls things down." in result.prompt


if __name__ == "__main__":
    if not _BONSAI_PATH.exists():
        print("Bonsai GGUF not present, skipping self-check")
    else:
        _model = Llama(model_path=str(_BONSAI_PATH), vocab_only=True, verbose=False)
        _formatter = Jinja2ChatFormatter(
            template=_model.metadata["tokenizer.chat_template"],
            eos_token=_model._model.token_get_text(_model.token_eos()),
            bos_token=_model._model.token_get_text(_model.token_bos()),
            stop_token_ids=[_model.token_eos()],
        )
        r = _formatter(
            messages=[{"role": "user", "content": "hi"}], enable_thinking=False
        )
        assert "<think>\n\n</think>\n\n" in r.prompt
        print("ok")

# Core/llm/llm_client.py

import gc
import json
import re

from utils.gpu_bootstrap import ensure_cuda_dlls

ensure_cuda_dlls()

from llama_cpp import Llama

from config.settings import (
    MODEL_TIERS,
    DEFAULT_TIER,
    TIER_ROUTING_ENABLED,
    CHAT_FORMAT_BY_TIER,
    THINKING_TIERS,
    THINKING_MARKER,
    CONTEXT_WINDOW,
    CONTEXT_WINDOW_BY_TIER,
    GPU_LAYERS,
    TEMPERATURE,
    TOP_P,
    MAX_TOKENS,
)


class LLMClient:
    """
    Fully local LLM inference for F.R.E.D., via llama.cpp.

    Models are loaded directly from disk — no server, no API,
    nothing leaves this machine.

    Responsibilities:
    - Pick the right model tier for the job (nano/standard/deep)
    - Load and cache llama.cpp model instances on demand
    - Unified generation interface
    """

    def __init__(self):

        self.tiers = MODEL_TIERS
        self.default_tier = DEFAULT_TIER

        self.temperature = TEMPERATURE
        self.max_tokens = MAX_TOKENS
        self.top_p = TOP_P

        # Loaded model instances, keyed by tier. Loaded lazily so
        # FRED doesn't pay the load cost for tiers it never needs.
        self._loaded = {}

    # =========================================================
    # PUBLIC INTERFACE
    # =========================================================

    def generate(self, messages: list, tier: str = None) -> str:
        """
        Unified generation interface.

        tier: "nano" | "standard" | "deep" — if not given, FRED
        picks one based on the latest user message.
        """

        chosen_tier = tier or self._pick_tier(messages)
        messages = self._apply_thinking(messages, chosen_tier)

        try:
            model = self._get_model(chosen_tier)
            return self._generate(model, messages)

        except Exception as error:

            print(f"[LLM] Inference failed on tier '{chosen_tier}':", error)

            if chosen_tier != self.default_tier:
                try:
                    model = self._get_model(self.default_tier)
                    return self._generate(model, messages)
                except Exception as fallback_error:
                    print("[LLM] Fallback inference failed:", fallback_error)

            return (
                "I'm experiencing a cognitive malfunction, boss."
            )

    # Openers/closers for the two reasoning syntaxes in play. Streaming
    # has to know about these: with thinking enabled the chain of thought
    # arrives as tokens like everything else, and _strip_thinking can only
    # act on a finished string. Emitting deltas naively would speak the
    # model's reasoning aloud.
    _THOUGHT_OPENERS = ("<|channel>", "<think>")
    _THOUGHT_CLOSERS = ("<channel|>", "</think>")

    @classmethod
    def _could_start_thought(cls, text: str) -> bool:
        """True while `text` is still a possible prefix of an opener, so a
        marker split across two deltas isn't missed."""
        return any(
            opener.startswith(text) or text.startswith(opener)
            for opener in cls._THOUGHT_OPENERS
        )

    def generate_stream(self, messages: list, tier: str = None):
        """
        Yield reply text as it is generated, with any reasoning block
        withheld.

        This is the fix for dead air: the caller can start speaking the
        first sentence while the rest is still being generated. Only safe
        on turns that need no tools — a tool result can't be summarised
        before the tool has run — and the intent router already separates
        those, so this is only ever called on the chat path.

        Falls back to yielding one complete string if streaming fails, so
        a caller never has to handle both shapes.
        """
        chosen_tier = tier or self._pick_tier(messages)
        messages = self._apply_thinking(messages, chosen_tier)

        try:
            model = self._get_model(chosen_tier)
            stream = model.create_chat_completion(
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                stream=True,
            )
        except Exception as error:
            print(f"[LLM] Streaming failed on '{chosen_tier}', falling back:", error)
            yield self.generate(messages, tier=chosen_tier)
            return

        buffer = ""
        state = "unknown"  # unknown -> thinking -> done, or unknown -> done

        try:
            for chunk in stream:
                delta = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content")
                ) or ""
                if not delta:
                    continue

                if state == "done":
                    yield delta
                    continue

                buffer += delta

                if state == "unknown":
                    if any(o in buffer for o in self._THOUGHT_OPENERS):
                        state = "thinking"
                    elif not self._could_start_thought(buffer.lstrip()[:12]):
                        # Long enough to be sure no opener is coming.
                        state = "done"
                        yield buffer
                        buffer = ""
                        continue

                if state == "thinking":
                    for closer in self._THOUGHT_CLOSERS:
                        index = buffer.find(closer)
                        if index >= 0:
                            remainder = buffer[index + len(closer):]
                            state = "done"
                            buffer = ""
                            if remainder.strip():
                                yield remainder
                            break

        except Exception as error:
            print(f"[LLM] Stream interrupted: {error}")

        # Anything still buffered never resolved into a reasoning block —
        # strip defensively and emit, so a reply is never silently lost.
        if buffer.strip():
            leftover = self._strip_thinking(buffer)
            if leftover:
                yield leftover

    def generate_with_tools(
        self,
        messages: list,
        tools: list,
        tier: str = None,
    ) -> dict:
        """
        Tool-calling interface. Returns the raw assistant message
        dict (content + optional tool_calls) so the orchestrator can
        execute any requested tools and continue the conversation.

        Not every local model's chat template supports tool-calling
        grammar — if the call fails for that reason, falls back to a
        plain text response with no tool calls.
        """

        chosen_tier = tier or self._pick_tier(messages)
        messages = self._apply_thinking(messages, chosen_tier)

        try:
            model = self._get_model(chosen_tier)

            response = model.create_chat_completion(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
            )

            message = response["choices"][0]["message"]

            if message.get("content"):
                message["content"] = self._strip_thinking(message["content"])

            return message

        except Exception as error:

            print(f"[LLM] Tool-calling generation failed, falling back:", error)

            return {
                "role": "assistant",
                "content": self.generate(messages, tier=tier),
                "tool_calls": None,
            }

    # =========================================================
    # MODEL LOADING
    # =========================================================

    # =========================================================
    # LOAD / UNLOAD (see settings.LLM_IDLE_UNLOAD_SECONDS)
    # =========================================================

    def is_loaded(self, tier: str = None) -> bool:
        return (tier or self.default_tier) in self._loaded

    def ensure_loaded(self, tier: str = None):
        """
        Load ahead of use. Called on the hotkey press so the ~1.9s load
        happens while the user is still speaking instead of after.
        """
        try:
            self._get_model(tier or self.default_tier)
            return True
        except Exception as e:
            print(f"[LLM] preload failed: {e}")
            return False

    def unload(self, tier: str = None) -> int:
        """
        Free a loaded model's VRAM. Returns how many models were dropped.

        llama_cpp's Llama.close() is what actually releases — measured at
        4566 of 4814 MiB reclaimed. The ~248 MiB left is the CUDA context,
        which is reused on the next load rather than leaked per cycle.
        """
        targets = [tier] if tier else list(self._loaded.keys())
        dropped = 0

        for name in targets:
            model = self._loaded.pop(name, None)
            if model is None:
                continue
            try:
                close = getattr(model, "close", None)
                if close:
                    close()
            except Exception as e:
                print(f"[LLM] close() failed for '{name}': {e}")
            del model
            dropped += 1

        if dropped:
            gc.collect()
            print(f"[LLM] unloaded {dropped} model(s) — VRAM released")

        return dropped

    def _get_model(self, tier: str) -> Llama:

        if tier in self._loaded:
            return self._loaded[tier]

        model_path = self.tiers.get(tier, self.tiers[self.default_tier])

        if not model_path.exists():
            raise FileNotFoundError(
                f"Model file not found for tier '{tier}': {model_path}"
            )

        n_ctx = CONTEXT_WINDOW_BY_TIER.get(tier, CONTEXT_WINDOW)

        # Most local GGUFs' own embedded chat templates have no provision
        # for tool definitions at all — llama.cpp then silently never
        # shows the model its tools, so chatml-function-calling is the
        # default because it works for tool calls AND plain chat on any
        # model. But it also *replaces* the model's own template, which
        # discards anything that template alone provides. Gemma 4 handles
        # both tools and thinking natively, so it opts out via
        # CHAT_FORMAT_BY_TIER and keeps its own.
        chat_format = CHAT_FORMAT_BY_TIER.get(tier, "chatml-function-calling")

        model = Llama(
            model_path=str(model_path),
            n_ctx=n_ctx,
            n_gpu_layers=GPU_LAYERS,
            verbose=False,
            chat_format=chat_format,
        )

        self._loaded[tier] = model

        return model

    # =========================================================
    # TIER SELECTION
    # =========================================================

    def _pick_tier(self, messages: list) -> str:
        """
        Lightweight heuristic to avoid running the beast model for
        "what time is it" and avoid running the nano model for
        anything that actually requires thinking. Refined further
        once Phase 12's real dispatcher exists.

        Disabled by default (TIER_ROUTING_ENABLED). It effectively
        overrode DEFAULT_TIER — the fallback below is "low", so ordinary
        short utterances went to the 2B regardless of configuration, and
        a 25-44 word one pulled in the 8.9GB "standard" model. Since
        _get_model caches each tier it loads, that could leave several
        models resident in VRAM simultaneously.
        """

        if not TIER_ROUTING_ENABLED:
            return self.default_tier

        last_user_msg = ""

        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        text = last_user_msg.strip().lower()

        if not text:
            return self.default_tier

        extreme_signals = (
            "comprehensive", "thorough", "in depth", "in-depth",
            "deep dive", "extensive", "exhaustive", "detailed analysis",
            "write a full", "entire codebase", "step-by-step plan",
        )

        deep_signals = (
            "why", "explain", "debug", "code", "plan", "design",
            "compare", "analyze", "architecture", "refactor",
            "step by step", "reason", "strategy",
        )

        nano_signals = (
            "open ", "launch ", "play ", "what time", "what's the time",
            "create a file", "create a folder", "set a timer",
            "remind me", "volume", "mute", "screenshot",
        )

        low_signals = (
            "hi", "hey", "hello", "thanks", "thank you", "ok", "okay",
            "cool", "lol", "yes", "no", "bye", "sup", "yo",
        )

        word_count = len(text.split())

        if any(sig in text for sig in extreme_signals) and word_count >= 65:
            return "extreme"

        if any(sig in text for sig in nano_signals) and word_count <= 25 and word_count >= 12:
            return "nano"

        if any(sig in text for sig in deep_signals) and word_count >= 45:
            return "deep"

        if word_count >= 25 and word_count < 45:
            return "standard"

        return "low"

    # =========================================================
    # INFERENCE
    # =========================================================

    def _generate(self, model: Llama, messages: list) -> str:

        response = model.create_chat_completion(
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
        )

        content = response["choices"][0]["message"]["content"]

        if not content:
            raise ValueError("Empty response from local model.")

        content = self._strip_thinking(content)

        if not content:
            raise ValueError("Model response was only unfinished reasoning.")

        return content

    @staticmethod
    def _apply_thinking(messages: list, tier: str) -> list:
        """
        Adapt the message list to a tier that uses its own chat template.

        Two adjustments, both required by Gemma 4's canonical template:

        1. Enable reasoning. llama-cpp-python offers no way to pass jinja
           variables through create_chat_completion, so `enable_thinking`
           is unreachable. The template renders THINKING_MARKER at the top
           of the first system turn when that flag is true, so putting the
           marker there directly yields the same rendered prompt.

        2. Convert tool-call arguments from a JSON string to a dict. The
           OpenAI convention (and chatml-function-calling) passes them as
           a string, but Gemma's template explicitly raise_exception()s on
           that — so replaying tool-call history for the follow-up turn
           aborted the whole generation with "arguments must be a JSON
           object (mapping), not a string".

        Returns a copy. Mutating the caller's list would stack a marker
        per turn, since the orchestrator reuses message history.
        """
        native_template = CHAT_FORMAT_BY_TIER.get(tier, "chatml-function-calling") is None

        if tier not in THINKING_TIERS and not native_template:
            return messages

        out = []
        for msg in messages:
            msg = dict(msg)
            tool_calls = msg.get("tool_calls")

            if native_template and tool_calls:
                converted = []
                for call in tool_calls:
                    call = dict(call)
                    function = dict(call.get("function") or {})
                    args = function.get("arguments")
                    if isinstance(args, str):
                        try:
                            function["arguments"] = json.loads(args or "{}")
                        except json.JSONDecodeError:
                            function["arguments"] = {}
                    call["function"] = function
                    converted.append(call)
                msg["tool_calls"] = converted

            out.append(msg)

        if tier not in THINKING_TIERS:
            return out

        for msg in out:
            if msg.get("role") in ("system", "developer"):
                content = msg.get("content") or ""
                if isinstance(content, str) and THINKING_MARKER not in content:
                    msg["content"] = f"{THINKING_MARKER}\n{content}"
                return out

        # No system turn to attach it to — the template only emits the
        # marker inside one, so give it a system message to live in.
        return [{"role": "system", "content": THINKING_MARKER}] + out

    @staticmethod
    def _strip_thinking(content: str) -> str:
        """
        Remove reasoning blocks so only the conclusion is spoken.

        Two syntaxes, because the models disagree:
          <think>...</think>                     DeepSeek-R1 / Nemotron
          <|channel>thought ... <channel|>       Gemma 4

        Gemma 4's canonical template uses paired <|x> ... <x|> channel
        markers rather than angle-bracket tags, and its own template
        carries an equivalent strip_thinking macro — miss this and the
        entire chain of thought gets read aloud.

        Also handles a lone closing marker (some models omit the opener)
        and an unterminated block, which means the model was cut off
        mid-reasoning and has no answer to give.
        """

        # Opened but never closed (e.g. hit max_tokens mid-reasoning) —
        # checked FIRST, before any tag-stripping below runs. The
        # substitutions a few lines down remove a lone, unclosed
        # "<|channel>" opener on its own (line with `</?\|?channel\|?>`),
        # which used to erase the only evidence this check needed —  by
        # the time it ran, the opener was already gone and the raw
        # reasoning text no longer "started with" anything recognisable,
        # so it slipped through and got spoken aloud instead of refused.
        if "<think>" in content and "</think>" not in content:
            return ""
        if re.search(r"<\|channel>\s*thought\b", content) and "<channel|>" not in content:
            return ""

        # <think> style
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = re.sub(r"^.*?</think>", "", content, flags=re.DOTALL)

        # Gemma 4 channel style. The thought channel is the one to drop;
        # any other channel carries real content, so only its markers go.
        content = re.sub(
            r"<\|channel>\s*thought\b.*?<channel\|>", "", content, flags=re.DOTALL
        )
        content = re.sub(r"^.*?<channel\|>", "", content, flags=re.DOTALL)
        content = re.sub(r"</?\|?channel\|?>", "", content)
        content = content.replace(THINKING_MARKER, "")

        content = content.strip()

        # Belt and braces for any other unterminated-opener shape the two
        # checks above didn't name explicitly.
        if content.startswith("<think>") or content.startswith("<|channel>"):
            return ""

        return content

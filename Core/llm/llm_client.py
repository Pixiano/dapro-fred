# Core/llm/llm_client.py

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
        Turn reasoning on for tiers that gate it behind enable_thinking.

        llama-cpp-python offers no way to pass jinja variables through
        create_chat_completion, so the flag itself is unreachable. Gemma
        4's template renders THINKING_MARKER at the top of the first
        system turn when enable_thinking is true, so putting the marker
        there directly yields the same rendered prompt.

        Returns a copy — mutating the caller's list would accumulate a
        marker per turn, since the orchestrator reuses message history.
        """
        if tier not in THINKING_TIERS:
            return messages

        out = [dict(m) for m in messages]

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

        # Opened but never closed (e.g. hit max_tokens) — a raw monologue
        # is not an answer, so refuse it rather than speak it.
        if content.startswith("<think>") or content.startswith("<|channel>"):
            return ""

        return content

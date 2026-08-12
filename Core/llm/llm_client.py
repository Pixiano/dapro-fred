# Core/llm/llm_client.py

import gc
import json
import re
import time

import requests

from utils import event_log
from utils.gpu_bootstrap import ensure_cuda_dlls

ensure_cuda_dlls()

from llama_cpp import Llama

from config.settings import (
    MODEL_TIERS,
    DEFAULT_TIER,
    TIER_ROUTING_ENABLED,
    CHAT_FORMAT_BY_TIER,
    TIER_PROMPT_MARKERS,
    MMPROJ_PATH_BY_TIER,
    CONTEXT_WINDOW,
    CONTEXT_WINDOW_BY_TIER,
    GPU_LAYERS,
    TEMPERATURE,
    TOP_P,
    MAX_TOKENS,
    LLM_STATUS_PATH,
    CLOUD_PROVIDERS,
    CLOUD_VISION_PROVIDER,
)


def _cloud_request(provider: dict, messages: list, tools=None, tool_choice=None,
                    temperature=0.7, top_p=1.0, max_tokens=None, stream=False):
    """
    One call to one OpenAI-compatible /chat/completions endpoint. Raises
    on any failure (network, auth, rate limit, HTTP error) — the caller
    decides what to do next (try the next provider, fall back to local).
    """
    payload = {
        "model": provider["model"],
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": stream,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"

    headers = {"Authorization": f"Bearer {provider['api_key']}"}

    for attempt in (0, 1):
        response = requests.post(
            provider["base_url"], headers=headers, json=payload, timeout=30, stream=stream
        )
        # A 429 here is a per-minute throttle, not an outage: Cerebras'
        # free tier allows 5 req/min and a single tool-calling turn can
        # fire four requests in two seconds. Since Groq was dropped
        # (settings.py) this provider is the whole cascade, so one
        # throttled request used to end the turn with "cognitive
        # malfunction" — confirmed in session_2026-08-06.jsonl at
        # 15:50:52 and 15:51:24. Wait out the window once, then give up
        # and let the caller fall through to local as before.
        if response.status_code == 429 and attempt == 0:
            wait = float(response.headers.get("retry-after") or 0)
            time.sleep(min(wait, 15.0) if wait else 12.0)
            continue
        break

    response.raise_for_status()

    if not stream:
        return response.json()
    return _iter_sse(response)


def _iter_sse(response):
    # SSE ("data: {...}\n\n", terminated by "data: [DONE]") — same wire
    # format on Groq and Cerebras (both OpenAI-compatible), and each
    # decoded chunk already matches the {"choices": [{"delta": ...}]}
    # shape generate_stream()'s parsing loop expects from a llama.cpp
    # chunk, so it needs no cloud-specific handling downstream.
    for line in response.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data == "[DONE]":
            return
        yield json.loads(data)


def _write_llm_status(loaded_tiers):
    """
    Cross-process signal for screen_watcher.py: what's currently
    resident in THIS process, so a separate watcher process can decide
    whether it's safe to load its own model without risking two
    multi-GB models on the card at once. Best-effort and silent — a
    failed write here must never break a real turn, same fail-open rule
    as every other logging/state path in this codebase.
    """
    try:
        LLM_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LLM_STATUS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"loaded": sorted(loaded_tiers)}), encoding="utf-8")
        tmp.replace(LLM_STATUS_PATH)
    except OSError:
        pass


class LLMClient:
    """
    LLM inference for F.R.E.D.

    Two independent systems, tried in order, not one merged into the
    other:
      1. CLOUD_PROVIDERS (settings.py) — Groq, then Cerebras. Tried
         first on every call. Neither trains on or retains inputs (see
         settings.py's comment on CLOUD_PROVIDERS for what was actually
         checked).
      2. The local tier system below (MODEL_TIERS/DEFAULT_TIER) —
         llama.cpp, GGUFs loaded from disk, nothing leaves this
         machine. Reached only once every cloud provider has failed
         (no key, no internet, rate limited, outage), at which point it
         runs exactly as it did before the cloud cascade existed.

    Responsibilities:
    - Pick the right model tier for the job (nano/standard/deep)
    - Load and cache llama.cpp model instances on demand
    - Unified generation interface
    """

    def __init__(self, report_status: bool = True):

        self.tiers = MODEL_TIERS
        self.default_tier = DEFAULT_TIER

        self.temperature = TEMPERATURE
        self.max_tokens = MAX_TOKENS
        self.top_p = TOP_P

        # Loaded model instances, keyed by tier. Loaded lazily so
        # FRED doesn't pay the load cost for tiers it never needs.
        self._loaded = {}

        # False for vision/screen_watcher.py's own LLMClient: that one
        # runs in a separate process this file is shared with only to
        # let IT read the MAIN process's status (see
        # _main_process_has_a_model_loaded() there). If it also wrote
        # its own "Vision" entry to the same file, a hard terminate()
        # (no chance to clean up) leaves that entry stuck forever —
        # confirmed live 2026-08-09: a 6-hour-old ghost "Vision" entry
        # from one such kill silently blocked every capture since,
        # background and on-demand alike.
        self._report_status = report_status

    # =========================================================
    # PUBLIC INTERFACE
    # =========================================================

    def _cloud_providers(self) -> list:
        return [p for p in CLOUD_PROVIDERS if p.get("api_key")]

    def _cloud_generate(self, messages: list, tools=None, tool_choice=None,
                         max_tokens: int = None) -> dict:
        """
        Try each configured cloud provider in order, returning the raw
        response JSON from the first one that succeeds. Raises only
        once every provider has failed — the caller (generate() /
        generate_with_tools()) treats that as "no cloud available" and
        drops straight through to the ORIGINAL local tier system,
        unmodified.
        """
        providers = self._cloud_providers()
        if not providers:
            raise RuntimeError("No cloud provider has an API key configured.")

        errors = []
        for provider in providers:
            try:
                return _cloud_request(
                    provider, messages, tools=tools, tool_choice=tool_choice,
                    temperature=self.temperature, top_p=self.top_p,
                    max_tokens=max_tokens or self.max_tokens,
                )
            except Exception as e:
                print(f"[LLM] cloud provider '{provider['name']}' failed, trying next: {e}")
                event_log.log_error(f"cloud_llm:{provider['name']}", e)
                errors.append(f"{provider['name']}: {e}")

        raise RuntimeError(f"All cloud providers failed: {'; '.join(errors)}")

    def _cloud_stream(self, messages: list):
        """
        Same cascade as _cloud_generate, but for the streaming path.
        Returns the SSE-decoding generator from the first provider whose
        connection succeeds, or None if every provider failed to even
        connect — the caller then falls through to local streaming.

        Only the connection setup (POST + status check) needs to
        succeed here; a failure mid-stream after that is handled by
        generate_stream()'s own existing try/except around iterating
        the stream, same as a local model's stream failing mid-way.
        """
        for provider in self._cloud_providers():
            try:
                return _cloud_request(
                    provider, messages, temperature=self.temperature,
                    top_p=self.top_p, max_tokens=self.max_tokens, stream=True,
                )
            except Exception as e:
                print(f"[LLM] cloud provider '{provider['name']}' streaming setup failed, trying next: {e}")
                event_log.log_error(f"cloud_llm_stream:{provider['name']}", e)
        return None

    def generate(self, messages: list, tier: str = None, max_tokens: int = None,
                 local_only: bool = False) -> str:
        """
        Unified generation interface.

        Tries the cloud cascade (CLOUD_PROVIDERS) first, regardless of
        `tier` — cloud isn't tier-scoped, it's a separate system in
        front of the whole local one. Only on total cloud failure does
        `tier` start to matter, exactly as it always has.

        tier: "Standard" | "Deep" | "Extreme" — if not given, FRED
        picks one based on the latest user message.

        max_tokens: per-call cap, for callers whose expected answer is
        one short line and who would otherwise pay for a thinking-on
        model's full budget on every step of a loop (see
        tools/smart_search.py). Defaults to self.max_tokens.

        local_only: never touch a cloud provider for this call. Set when
        the prompt carries content the vault marks sensitive — see
        utils/sensitive.py. This is a HARD constraint from the vault's
        rules.md ("Never send personal/ or people/ anywhere. No hosted
        model, no API"), not a preference: the cascade would otherwise
        POST health and identity details about a minor to Groq.
        """

        if not local_only:
            try:
                response = self._cloud_generate(messages, max_tokens=max_tokens)
                return self._finish_response(response) or (
                    "I ran out of room thinking that one through, sir. "
                    "Ask me again, or narrow it down a little."
                )
            except Exception as cloud_error:
                print(f"[LLM] cloud cascade unavailable, falling back to local: {cloud_error}")
                event_log.log_error("cloud_llm_cascade", cloud_error)

        chosen_tier = tier or self._pick_tier(messages)
        messages = self._apply_thinking(messages, chosen_tier)

        try:
            model = self._get_model(chosen_tier)
            # _strip_thinking deliberately returns "" when the model
            # opened a reasoning block and never closed it — it ran out
            # of tokens mid-thought and genuinely has no answer, and
            # speaking the raw chain of thought would be worse. But an
            # empty string reaches the TTS layer as total silence:
            # confirmed in session_2026-08-01_18-41-50.jsonl, where three
            # turns logged `"text": ""` with `spoken: true` and FRED just
            # said nothing at all. Thinking-on Qwen3-8B makes this
            # reachable on any turn whose reasoning overruns max_tokens.
            # Say something honest instead of nothing.
            return self._generate(model, messages, max_tokens=max_tokens) or (
                "I ran out of room thinking that one through, sir. "
                "Ask me again, or narrow it down a little."
            )

        except Exception as error:

            print(f"[LLM] Inference failed on tier '{chosen_tier}':", error)

            if chosen_tier != self.default_tier:
                try:
                    model = self._get_model(self.default_tier)
                    return self._generate(
                        model, messages, max_tokens=max_tokens
                    ) or (
                        "I ran out of room thinking that one through, sir. "
                        "Ask me again, or narrow it down a little."
                    )
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

    def generate_stream(self, messages: list, tier: str = None, local_only: bool = False):
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

        CANCELLATION (confirmed 2026-08-02, don't re-derive this): this
        generator itself takes no cancel flag, and needs none — but
        cancellation still requires the CALLER to hold one and check it.
        create_chat_completion(stream=True) is itself a Python generator
        wrapping llama.cpp's token loop — the next token is only decoded
        when something calls next() on it, so whoever is iterating this
        must stop calling next() to stop generation. pill_app.py does
        that with the `_cancel` Event it already keeps for other reasons
        (stopping the TTS queue): its producer thread's `for piece in
        ...` loop checks `_cancel.is_set()` each iteration and `return`s
        the instant it's set, abandoning this generator without
        exhausting it. Measured directly: GPU utilization was 74%
        mid-generation, then 1% within 0.2-0.4s of that return executing
        — Python's refcount-based cleanup closes the whole generator
        chain (this -> create_chat_completion's stream -> llama.cpp's
        token loop) almost immediately. No cancel plumbing needs to be
        threaded INTO this generator or into llama.cpp — the existing
        consumer-side flag plus ordinary generator abandonment is
        sufficient — but that consumer-side flag is doing real work and
        is not optional.

        This does NOT extend to generate_with_tools() below — that path
        is non-streaming (a single blocking create_chat_completion call)
        and llama-cpp-python's public API doesn't expose the lower-level
        stopping_criteria hook that WOULD allow the same trick there;
        create_completion() takes it, create_chat_completion() doesn't
        forward it. A tool-calling turn genuinely cannot be interrupted
        mid-generation today — this is real, not laziness — because
        interrupting one requires either that missing hook or manually
        reimplementing chat-template application to call create_completion
        directly, bypassing the chat-format handler this codebase relies
        on for tool-calling grammar. Not attempted: real risk of
        diverging from library-maintained, tested formatting for a
        speed-of-interrupt win on turns that are already the minority
        (most conversation happens on the streamed chat path above).
        """
        # local_only: see generate()'s docstring — a hard vault rule, not
        # a preference. None here means "no cloud stream", which is the
        # same shape a total cloud failure produces, so the local path
        # below needs no separate branch.
        stream = None if local_only else self._cloud_stream(messages)
        chosen_tier = None

        if stream is None:
            chosen_tier = tier or self._pick_tier(messages)
            # Separate name, same reason as generate_with_tools below: the
            # except branch re-enters generate(), which tries the cloud,
            # and the adapted copy is rejected there with a 400.
            local_messages = self._apply_thinking(messages, chosen_tier)
            try:
                model = self._get_model(chosen_tier)
                stream = model.create_chat_completion(
                    messages=local_messages,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    max_tokens=self.max_tokens,
                    stream=True,
                )
            except Exception as error:
                print(f"[LLM] Streaming failed on '{chosen_tier}', falling back:", error)
                yield self.generate(messages, tier=chosen_tier, local_only=local_only)
                return

        buffer = ""
        state = "unknown"  # unknown -> thinking -> done, or unknown -> done

        # _strip_tool_call_debris can't run here: it needs a whole `{...}`
        # and this path yields token-sized pieces, so a leaked call is
        # already half-spoken by the time the closing brace arrives.
        # Confirmed live 2026-08-05: FRED said `{"name": "list_tasks",
        # "arguments": }` out loud after the cloud cascade 429'd and the
        # local model emitted the call as plain text. Hold text back from
        # a `{` until its `}` lands, then drop the object — same rule as
        # the non-streaming path, just applied incrementally.
        pending = ""

        def emit(text: str) -> str:
            nonlocal pending
            pending += text
            out = ""
            while True:
                open_at = pending.find("{")
                if open_at < 0:
                    out, pending = out + pending, ""
                    return out
                out += pending[:open_at]
                # Depth-counted, not first-"}": a real call nests its
                # arguments object, and stopping at the inner brace would
                # emit the outer wrapper's tail.
                depth, end = 0, -1
                for i, ch in enumerate(pending[open_at:], open_at):
                    depth += (ch == "{") - (ch == "}")
                    if depth == 0:
                        end = i
                        break
                if end < 0:
                    pending = pending[open_at:]
                    return out
                pending = pending[end + 1:]

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
                    piece = emit(delta)
                    if piece:
                        yield piece
                    continue

                buffer += delta

                if state == "unknown":
                    if any(o in buffer for o in self._THOUGHT_OPENERS):
                        state = "thinking"
                    elif not self._could_start_thought(buffer.lstrip()[:12]):
                        # Long enough to be sure no opener is coming.
                        state = "done"
                        piece = emit(buffer)
                        buffer = ""
                        if piece:
                            yield piece
                        continue

                if state == "thinking":
                    for closer in self._THOUGHT_CLOSERS:
                        index = buffer.find(closer)
                        if index >= 0:
                            remainder = buffer[index + len(closer):]
                            state = "done"
                            buffer = ""
                            piece = emit(remainder) if remainder.strip() else ""
                            if piece:
                                yield piece
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
        local_only: bool = False,
    ) -> dict:
        """
        Tool-calling interface. Returns the raw assistant message
        dict (content + optional tool_calls) so the orchestrator can
        execute any requested tools and continue the conversation.

        Tries the cloud cascade first, same as generate() — cloud
        providers speak plain OpenAI tool_calls, no template quirks to
        fight. Falls through to the local tier system (and, within
        that, to a plain no-tools response) only once cloud has failed
        entirely.

        Not every local model's chat template supports tool-calling
        grammar — if the call fails for that reason, falls back to a
        plain text response with no tool calls.
        """

        if not local_only:
            try:
                response = self._cloud_generate(
                    messages, tools=tools, tool_choice="auto", max_tokens=self.max_tokens
                )
                message = response["choices"][0]["message"]
                if message.get("content"):
                    message["content"] = self._strip_thinking_for_tools(
                        message["content"], has_tool_calls=bool(message.get("tool_calls"))
                    )
                return message
            except Exception as cloud_error:
                print(f"[LLM] cloud cascade unavailable for tool-calling, falling back to local: {cloud_error}")
                event_log.log_error("cloud_llm_cascade_tools", cloud_error)

        chosen_tier = tier or self._pick_tier(messages)

        # Kept under a separate name, NOT rebound over `messages`. The
        # adapted copy has tool_call arguments as dicts (see
        # _apply_thinking), which is what a local native template needs
        # and exactly what the OpenAI wire format forbids — it requires a
        # JSON string. The fallback below re-enters generate(), which
        # tries the cloud first, so handing it the adapted list makes both
        # providers answer 400 Bad Request and the user hears "cognitive
        # malfunction". Confirmed live 2026-08-04, both legs 400 in the
        # same second, immediately after a tool ran.
        local_messages = self._apply_thinking(messages, chosen_tier)

        try:
            model = self._get_model(chosen_tier)

            response = model.create_chat_completion(
                messages=local_messages,
                tools=tools,
                tool_choice="auto",
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
            )

            message = response["choices"][0]["message"]

            if message.get("content"):
                message["content"] = self._strip_thinking_for_tools(
                    message["content"], has_tool_calls=bool(message.get("tool_calls"))
                )

            return message

        except Exception as error:

            print(f"[LLM] Tool-calling generation failed, falling back:", error)

            return {
                "role": "assistant",
                "content": self.generate(messages, tier=tier),
                "tool_calls": None,
            }

    def describe_image(self, image_b64_data_uri: str, prompt: str, max_tokens: int = 200,
                        allow_local_fallback: bool = True, skip_cloud: bool = False) -> str:
        """
        One-shot image description. Takes a data-URI-encoded image
        (base64, with the "data:image/..." prefix), the same
        OpenAI-compatible image_url content-part shape either the cloud
        API or llama-cpp-python's multimodal handler expects.

        Cloud (CLOUD_VISION_PROVIDER, gemma-4-31b) tried first — the
        local Vision tier shares this process's one GPU with whatever
        else FRED is doing (see vision/watcher_manager.py's whole
        cross-process dance to avoid a collision) and was laggy even
        when it got a clean run. Falls back to local only if no cloud
        key is configured or the request fails, same shape as
        generate()'s cloud-then-local cascade.

        allow_local_fallback=False lets a caller that already knows
        local isn't VRAM-safe right now (screen_watcher.py's on-demand
        capture, when the main process has a model resident) still get
        a real shot at the cloud path instead of being blocked from
        even trying. Confirmed live 2026-08-10: whats_on_screen()'s
        cache sat 19+ hours stale because the watcher's old safety gate
        skipped the ENTIRE cycle — cloud attempt included — any time a
        local tier happened to be loaded, even though cloud needs no
        local VRAM at all and was always safe to attempt.

        skip_cloud=True is the mirror case: a caller that already knows
        cloud just failed a moment ago (whats_on_screen()'s forced-local
        retry, after unloading the main model specifically to make local
        safe) and doesn't want to pay a second cloud round-trip — cloud's
        2026-08-12 failure mode included 30s read timeouts, so retrying
        it here would double a real, already-observed delay for no
        benefit.

        Separate from generate()/generate_with_tools() because the
        message shape is genuinely different (image content parts, no
        tool-calling), and because the local fallback always forces
        tier="Vision" — callers never get to pick a different model for
        an image.
        """
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_b64_data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        if CLOUD_VISION_PROVIDER.get("api_key") and not skip_cloud:
            try:
                response = _cloud_request(
                    CLOUD_VISION_PROVIDER, messages,
                    temperature=self.temperature, top_p=self.top_p, max_tokens=max_tokens,
                )
                content = response["choices"][0]["message"]["content"] or ""
                return self._strip_thinking(content) or content
            except Exception as e:
                print(f"[LLM] cloud vision failed: {e}")
                event_log.log_error("cloud_vision", e)
                if not allow_local_fallback:
                    raise

        if not allow_local_fallback:
            raise RuntimeError("cloud vision unavailable (no API key) and local fallback not allowed")

        model = self._get_model("Vision")

        response = model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=self.temperature,
        )
        content = response["choices"][0]["message"]["content"] or ""
        return self._strip_thinking(content) or content

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

        if self._report_status:
            _write_llm_status(self._loaded.keys())
        return dropped

    def _get_model(self, tier: str) -> Llama:

        if tier in self._loaded:
            return self._loaded[tier]

        # Only ONE tier stays resident. Measured 2026-08-02 on the venv's
        # CUDA build: Standard (Qwen3-8B Q4_K_M) at n_ctx 24576 peaks at
        # ~9.9 GB VRAM of a 16310 MiB card, and Deep (Qwen3-14B) is
        # larger again — two resident at once cannot fit, and this
        # machine has a documented history of hard access-violation
        # crashes (0xc0000005) from VRAM exhaustion.
        #
        # This was latent before today and is now reachable: nothing used
        # to request Deep at all (TIER_ROUTING_ENABLED is False), but
        # tools/smart_search.py's find_file_smart could pull a second
        # model in alongside the resident one on a single "find my X"
        # turn.
        #
        # The cost is a reload when alternating tiers, and the
        # tier-switching path is rare.
        if self._loaded:
            evicted = ", ".join(self._loaded)
            print(f"[LLM] evicting {evicted} to load '{tier}' (one tier resident)")
            self.unload()

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
        mmproj_path = MMPROJ_PATH_BY_TIER.get(tier)

        if mmproj_path is not None:
            # Vision tiers use a chat_handler, not chat_format — the
            # handler is what actually knows how to fold an image into
            # the prompt via the paired mmproj (CLIP) model. Passing
            # both chat_format and chat_handler is not a supported
            # combination, so this branch is exclusive of the one below.
            if not mmproj_path.exists():
                raise FileNotFoundError(
                    f"mmproj file not found for tier '{tier}': {mmproj_path}"
                )
            from llama_cpp.llama_chat_format import Gemma4ChatHandler
            chat_handler = Gemma4ChatHandler(clip_model_path=str(mmproj_path))

            model = Llama(
                model_path=str(model_path),
                n_ctx=n_ctx,
                n_gpu_layers=GPU_LAYERS,
                verbose=False,
                chat_handler=chat_handler,
            )
        else:
            chat_format = CHAT_FORMAT_BY_TIER.get(tier, "chatml-function-calling")
            model = Llama(
                model_path=str(model_path),
                n_ctx=n_ctx,
                n_gpu_layers=GPU_LAYERS,
                verbose=False,
                chat_format=chat_format,
            )

        self._loaded[tier] = model
        if self._report_status:
            _write_llm_status(self._loaded.keys())

        return model

    # =========================================================
    # TIER SELECTION
    # =========================================================

    def _pick_tier(self, messages: list) -> str:
        """
        Lightweight heuristic to avoid running Deep/Extreme for
        "what time is it" and to reach for them when a request actually
        looks like it needs the extra capability.

        Disabled by default (TIER_ROUTING_ENABLED). Simplified
        2026-08-01 alongside MODEL_TIERS being cut back to exactly
        Standard/Deep/Extreme — this used to also route to "nano" and
        "low", both since deleted from MODEL_TIERS entirely, so those
        branches are gone rather than left pointing at tiers that no
        longer exist. Likely superseded by the planned "offer Deep, ask
        before switching" flow rather than revived as silent routing —
        see TIER_ROUTING_ENABLED's comment in settings.py.
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
            "step by step", "reason", "strategy", "research",
        )

        word_count = len(text.split())

        if any(sig in text for sig in extreme_signals) and word_count >= 65:
            return "Extreme"

        if any(sig in text for sig in deep_signals) and word_count >= 45:
            return "Deep"

        return self.default_tier

    # =========================================================
    # INFERENCE
    # =========================================================

    def _generate(self, model: Llama, messages: list, max_tokens: int = None) -> str:

        response = model.create_chat_completion(
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=max_tokens or self.max_tokens,
        )

        return self._finish_response(response)

    @staticmethod
    def _finish_response(response: dict) -> str:
        """
        Shared by the local path (_generate, above) and the cloud path
        (generate()'s _cloud_generate call) — same response shape
        either way ({"choices": [{"message": {"content": ...}}]}), so
        one extraction+strip routine covers both.
        """
        content = response["choices"][0]["message"]["content"]

        # A genuinely empty completion is a model/runtime failure worth
        # retrying on another tier, so it still raises.
        if not content:
            raise ValueError("Empty response from model.")

        stripped = LLMClient._strip_thinking(content)

        # "Only unfinished reasoning" is NOT a failure and must not
        # raise: the model worked fine, it just spent its whole budget
        # thinking and never reached an answer. Raising sent it down the
        # fallback path and ultimately to "I'm experiencing a cognitive
        # malfunction", which misdescribes what happened and (on the
        # default tier, where there's no other tier to fall back to) is
        # all the user ever heard. Returning "" lets generate() answer
        # honestly instead — see the fallback string there.
        return stripped

    @staticmethod
    def _apply_thinking(messages: list, tier: str) -> list:
        """
        Adapt the message list to a tier that uses its own chat template.

        Two adjustments:

        1. Inject that tier's TIER_PROMPT_MARKERS entry, if it has one.
           llama-cpp-python offers no way to pass jinja template kwargs
           through create_chat_completion (enable_thinking,
           reasoning_effort, etc. are all unreachable), and each marker
           is literal text confirmed to reproduce what the real kwarg
           would have rendered for that specific tier's template — see
           TIER_PROMPT_MARKERS in settings.py for what's actually been
           checked per tier, since the mechanism differs (Gemma 4's old
           marker suppressed nothing else and was purely additive;
           gpt-oss's default "Reasoning: medium" line renders regardless
           of this marker, so this one is a best-effort override sitting
           alongside it, not a confirmed replacement).

        2. Convert tool-call arguments from a JSON string to a dict, for
           any tier keeping its own template (CHAT_FORMAT_BY_TIER is
           None). The OpenAI convention (and chatml-function-calling)
           passes them as a string, but a model's own template can
           explicitly raise_exception() on that — Gemma 4 did — so
           replaying tool-call history for a follow-up turn aborted the
           whole generation with "arguments must be a JSON object
           (mapping), not a string".

        Returns a copy. Mutating the caller's list would stack a marker
        per turn, since the orchestrator reuses message history.
        """
        native_template = CHAT_FORMAT_BY_TIER.get(tier, "chatml-function-calling") is None
        marker = TIER_PROMPT_MARKERS.get(tier)

        if marker is None and not native_template:
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

        if marker is None:
            return out

        for msg in out:
            if msg.get("role") in ("system", "developer"):
                content = msg.get("content") or ""
                if isinstance(content, str) and marker not in content:
                    msg["content"] = f"{marker}\n{content}"
                return out

        # No system turn to attach it to — give the marker one to live in.
        return [{"role": "system", "content": marker}] + out

    # A tool call that the chat handler failed to parse stays in the
    # content and gets SPOKEN. Confirmed live 2026-08-04, this reached the
    # speaker verbatim: `{"path":"daily/2026-08"}We need to list
    # directory.{"path":"daily"}The date check is done...`. Qwen3's own
    # template emits tool calls inline; llama.cpp lifts the first one out
    # and leaves any others behind as text.
    #
    # ponytail: strips <tool_call> wrappers and any bare {...} object. A
    # reply that legitimately contains braces would lose them — for a
    # voice assistant that never happens, and speaking JSON aloud is
    # always wrong. Narrow it to argument-shaped objects if it ever bites.
    _TOOL_DEBRIS = re.compile(
        r"</?tool_call>|<\|?tool▁calls?▁begin\|?>|\{[^{}]*\}", re.DOTALL
    )

    @staticmethod
    def _strip_tool_call_debris(content: str) -> str:
        """Remove unparsed tool-call syntax so it is never spoken."""
        if "{" not in content and "tool_call" not in content:
            return content
        # Repeat to a fixpoint: the pattern only matches an object with
        # no braces inside it, so a nested one is peeled innermost-first
        # and a single pass leaves the outer wrapper behind. Confirmed
        # live 2026-08-05 — {"name": "list_tasks", "arguments": {}} had
        # its {} removed and FRED spoke the remaining
        # `{"name": "list_tasks", "arguments": }` out loud.
        cleaned = content
        while True:
            once = LLMClient._TOOL_DEBRIS.sub(" ", cleaned)
            if once == cleaned:
                break
            cleaned = once
        # Collapse the gaps the removals leave mid-sentence.
        return re.sub(r"[ \t]{2,}", " ", cleaned)

    @classmethod
    def _strip_thinking_for_tools(cls, content: str, has_tool_calls: bool) -> str:
        """
        Same stripping as generate()'s own call, plus generate()'s honest
        fallback for the "opened a reasoning block, never closed it" case
        — see that method's comment (session_2026-08-01_18-41-50.jsonl:
        three turns logged `"text": ""` with `spoken: true`, dead silence
        reaching the user). generate_with_tools() ran the strip but never
        got the fallback, so the identical failure was reachable again
        here — reproduced in shape 2026-08-12 14:13-14:14 (turf-search
        turn, two failed find_file_smart calls, then an empty spoken
        reply): a stuck model with nothing left to try can open a
        reasoning block explaining the dead end and run out of max_tokens
        before closing it, same as the plain-chat path always could.
        Only substituted when there are no tool_calls this round —
        content is allowed to be empty when a tool call carries the turn,
        and inventing a spoken line there would be pure noise the
        orchestrator ignores anyway.
        """
        stripped = cls._strip_thinking(content, debris=False)
        if not stripped and not has_tool_calls:
            return (
                "I ran out of room thinking that one through, sir. "
                "Ask me again, or narrow it down a little."
            )
        return stripped

    @staticmethod
    def _strip_thinking(content: str, debris: bool = True) -> str:
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
        # Leftover from the single-global-marker era — TIER_PROMPT_MARKERS
        # is now per-tier, so strip whichever marker(s) it defines rather
        # than one fixed string. Confirmed live: a bare `THINKING_MARKER`
        # reference here threw NameError on every Standard-tier
        # tool-calling turn, caught by generate_with_tools' fallback and
        # masked as the generic "cognitive malfunction" reply.
        for marker in TIER_PROMPT_MARKERS.values():
            content = content.replace(marker, "")

        # debris=False on the tool-calling path only: the orchestrator
        # parses tool calls the model wrote as plain text, and it can't
        # parse what was already deleted here. Confirmed 2026-08-05 —
        # asking for today's tasks made the model emit its list_tasks
        # call as bare JSON, this erased it, and the model then answered
        # "no tasks recorded" for a day with six of them. The
        # orchestrator strips (or regenerates) once it has had its look.
        if debris:
            content = LLMClient._strip_tool_call_debris(content)

        content = content.strip()

        # Belt and braces for any other unterminated-opener shape the two
        # checks above didn't name explicitly.
        if content.startswith("<think>") or content.startswith("<|channel>"):
            return ""

        return content

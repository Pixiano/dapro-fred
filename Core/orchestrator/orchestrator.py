# Core/orchestrator/orchestrator.py

import json
import re

from state.conversation_state import ConversationState
from memory.memory_manager import MemoryManager
from llm.llm_client import LLMClient
from personality.system_prompt import SYSTEM_PROMPT
from tools.registry import ToolRegistry
from tools import system_tools
from config.settings import TOOLS_ENABLED


class FREDOrchestrator:
    """
    Central runtime coordinator for F.R.E.D.

    Responsibilities:
    - Manage conversation flow
    - Retrieve memory context
    - Build structured prompts
    - Call the LLM, executing any tools it requests
    - Persist conversation state
    """

    def __init__(self):
        self.state = ConversationState()
        self.memory = MemoryManager()
        self.llm = LLMClient()

        self.tools = ToolRegistry()
        self._register_tools()

    def process(self, user_input: str) -> str:
        """
        Main orchestration pipeline.
        """

        # -----------------------------
        # 1. Store user message in state
        # -----------------------------
        self.state.add_message("user", user_input)

        # -----------------------------
        # 2. Retrieve relevant memories
        # -----------------------------
        relevant_memories = self.memory.retrieve_relevant(
            query=user_input,
            top_k=5
        )

        # -----------------------------
        # 3. Retrieve recent conversation
        # -----------------------------
        recent_messages = self.state.get_recent_messages(limit=10)

        # -----------------------------
        # 4. Build structured context
        # -----------------------------
        messages = self._build_messages(
            recent_messages=recent_messages,
            memories=relevant_memories,
            user_input=user_input
        )

        # -----------------------------
        # 5. Generate response, acting on any tool calls
        # -----------------------------
        assistant_reply = self._generate_with_tools(messages)

        # -----------------------------
        # 6. Store assistant response
        # -----------------------------
        self.state.add_message("assistant", assistant_reply)

        # -----------------------------
        # 7. Persist memory
        # -----------------------------
        self.memory.store("user", user_input)
        self.memory.store("assistant", assistant_reply)

        return assistant_reply

    # =========================================================
    # TOOL REGISTRATION
    # =========================================================

    def _register_tools(self):

        self.tools.register(
            name="open_website",
            function=system_tools.open_website,
            description="Open a website in the default browser.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to open, e.g. https://youtube.com",
                    }
                },
                "required": ["url"],
            },
        )

        self.tools.register(
            name="launch_application",
            function=system_tools.launch_application,
            description="Launch a desktop application by name or path.",
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application name or executable path, e.g. notepad",
                    }
                },
                "required": ["app_name"],
            },
        )

        self.tools.register(
            name="create_text_file",
            function=system_tools.create_text_file,
            description="Create a text file with optional content.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "File name or path to create.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write into the file.",
                    },
                },
                "required": ["filename"],
            },
        )

        self.tools.register(
            name="create_folder",
            function=system_tools.create_folder,
            description="Create a folder/directory.",
            parameters={
                "type": "object",
                "properties": {
                    "folder_name": {
                        "type": "string",
                        "description": "Folder name or path to create.",
                    }
                },
                "required": ["folder_name"],
            },
        )

        self.tools.register(
            name="get_current_time",
            function=system_tools.get_current_time,
            description="Get the current local date and time.",
            parameters={
                "type": "object",
                "properties": {},
            },
        )

    # =========================================================
    # TOOL-CALLING LOOP
    # =========================================================

    def _generate_with_tools(self, messages: list) -> str:
        """
        Asks the LLM for a response. If it requests one or more
        tools, executes them and asks again with the results, so
        the final reply reflects what actually happened.
        """

        if not TOOLS_ENABLED:
            return self.llm.generate(messages)

        tool_definitions = self.tools.get_tool_definitions()

        message = self.llm.generate_with_tools(messages, tools=tool_definitions)

        tool_calls = message.get("tool_calls")

        if not tool_calls:
            # Some local models (Nemotron/Hermes-style templates) don't
            # use llama.cpp's structured tool_calls field — they emit
            # the call as plain text instead, e.g.:
            #   <tool_call><function=get_current_time></function></tool_call>
            tool_calls = self._parse_text_tool_calls(message.get("content") or "")

        if not tool_calls:
            return message.get("content") or self.llm.generate(messages)

        # Echo the assistant's tool-call request, then append one
        # result message per call, per the tool-calling protocol.
        messages.append({
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": tool_calls,
        })

        tool_results = []

        for call in tool_calls:
            result = self._execute_tool_call(call)
            tool_results.append(str(result))

            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": str(result),
            })

        # Ask once more, now with tool results in context, for the
        # natural-language reply FRED actually says out loud. The
        # function-calling chat format needs `tools` passed again to
        # correctly render the tool-result history — without it, the
        # model loses track of what it just did and may contradict
        # the action it actually took.
        follow_up = self.llm.generate_with_tools(messages, tools=tool_definitions)

        # llama.cpp's function-calling format sometimes returns empty
        # content for this final turn regardless of model strength —
        # fall back to the tool's own (already human-readable) result
        # rather than a generic "Done." that says nothing real.
        return follow_up.get("content") or " ".join(tool_results)

    @staticmethod
    def _parse_text_tool_calls(content: str) -> list:
        """
        Best-effort parser for models that emit tool calls as plain
        text instead of llama.cpp's structured tool_calls field.
        Returns the same shape generate_with_tools would, so the
        rest of the pipeline doesn't need to know which path fired.
        """

        calls = []

        for block in re.findall(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
            block = block.strip()

            # Hermes/Nemotron style: <function=NAME>{args}</function>
            match = re.match(
                r"<function=([\w_]+)>(.*?)</function>", block, re.DOTALL
            )

            if match:
                name = match.group(1).strip()
                raw_args = match.group(2).strip() or "{}"
            else:
                # Qwen-style: {"name": "...", "arguments": {...}}
                try:
                    parsed = json.loads(block)
                    name = parsed.get("name")
                    raw_args = json.dumps(parsed.get("arguments", {}))
                except (json.JSONDecodeError, AttributeError):
                    continue

            if name:
                calls.append({
                    "id": f"text_call_{len(calls)}",
                    "function": {"name": name, "arguments": raw_args},
                })

        return calls

    def _execute_tool_call(self, call: dict) -> str:

        function = call.get("function", {})
        name = function.get("name")
        raw_arguments = function.get("arguments") or "{}"

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return f"Error: malformed arguments for tool '{name}'."

        try:
            return self.tools.execute(name, **arguments)
        except Exception as error:
            return f"Error running tool '{name}': {error}"

    # =========================================================
    # MESSAGE BUILDING
    # =========================================================

    def _build_messages(
        self,
        recent_messages: list,
        memories: list,
        user_input: str
    ) -> list:
        """
        Builds structured message list for the LLM.
        """

        messages = []

        # -----------------------------
        # System prompt
        # -----------------------------
        messages.append({
            "role": "system",
            "content": SYSTEM_PROMPT
        })

        # -----------------------------
        # Inject memory context
        # -----------------------------
        if memories:
            memory_text = "\n".join(
                [f"- {m['content']}" for m in memories]
            )

            messages.append({
                "role": "system",
                "content": (
                    "Relevant long-term memory:\n"
                    f"{memory_text}"
                )
            })

        # -----------------------------
        # Add recent conversation
        # -----------------------------
        for msg in recent_messages:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        # -----------------------------
        # Final user message
        # -----------------------------
        messages.append({
            "role": "user",
            "content": user_input
        })

        return messages

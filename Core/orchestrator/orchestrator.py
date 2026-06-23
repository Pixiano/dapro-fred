# Core/orchestrator/orchestrator.py

import json
import re

from state.conversation_state import ConversationState
from memory.memory_manager import MemoryManager
from llm.llm_client import LLMClient
from personality.system_prompt import SYSTEM_PROMPT
from tools.registry import ToolRegistry
from tools import system_tools
from tools import web_tools
from tools import machine_tools
from orchestrator.dispatcher import Dispatcher
from orchestrator.scheduler import ReminderScheduler
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

        self.scheduler = ReminderScheduler()

        self.tools = ToolRegistry()
        self._register_tools()

        self.dispatcher = Dispatcher()

        # Set whenever a destructive tool is awaiting a yes/no before
        # it's allowed to run. See _request_confirmation /
        # _handle_pending_confirmation.
        self.pending_action = None

    def process(self, user_input: str) -> str:
        """
        Main orchestration pipeline.

        The dispatcher gets first look: obvious commands ("open
        Spotify", "what time is it") are executed directly, no LLM
        involved at all. Anything it doesn't recognize falls through
        to the full memory + LLM + tool-calling pipeline.

        If a destructive tool (delete, kill process, close a window)
        is awaiting confirmation, this turn is treated as the yes/no
        answer instead of a new request — FRED doesn't act on
        anything irreversible without that explicit confirmation.
        """

        self.state.add_message("user", user_input)

        if self.pending_action:
            assistant_reply = self._handle_pending_confirmation(user_input)
        else:
            dispatch = self.dispatcher.match(user_input)

            if dispatch:
                assistant_reply = self._run_or_confirm(
                    dispatch["tool"], dispatch["arguments"]
                )
            else:
                assistant_reply = self._process_with_llm(user_input)

        self.state.add_message("assistant", assistant_reply)

        self.memory.store("user", user_input)
        self.memory.store("assistant", assistant_reply)

        return assistant_reply

    # =========================================================
    # CONFIRMATION GATE
    # =========================================================

    def _run_or_confirm(self, tool_name: str, arguments: dict) -> str:
        """
        Runs a tool immediately if it's safe, or halts and asks for
        confirmation first if it's destructive.
        """

        if self.tools.is_destructive(tool_name):
            return self._request_confirmation(tool_name, arguments)

        try:
            return str(self.tools.execute(tool_name, **arguments))
        except Exception as error:
            return f"Couldn't do that: {error}"

    def _request_confirmation(self, tool_name: str, arguments: dict) -> str:

        self.pending_action = {"tool": tool_name, "arguments": arguments}

        description = ", ".join(f"{k}={v}" for k, v in arguments.items())

        return (
            f"This can't be undone — about to run '{tool_name}'"
            f"{f' ({description})' if description else ''}. "
            "Confirm? (yes/no)"
        )

    def _handle_pending_confirmation(self, user_input: str) -> str:

        action = self.pending_action
        self.pending_action = None

        affirmative = {"yes", "y", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "ok", "okay"}

        if user_input.strip().lower() in affirmative:
            try:
                result = str(self.tools.execute(action["tool"], **action["arguments"]))
                return result
            except Exception as error:
                return f"Couldn't do that: {error}"

        return "Cancelled — didn't run it."

    def _process_with_llm(self, user_input: str) -> str:
        """
        Full pipeline for anything the dispatcher couldn't resolve
        on its own: memory retrieval, prompt building, and the LLM
        (with tool-calling) generating the actual reply.
        """

        relevant_memories = self.memory.retrieve_relevant(
            query=user_input,
            top_k=5
        )

        recent_messages = self.state.get_recent_messages(limit=10)

        messages = self._build_messages(
            recent_messages=recent_messages,
            memories=relevant_memories,
            user_input=user_input
        )

        return self._generate_with_tools(messages)

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

        self.tools.register(
            name="web_search",
            function=web_tools.web_search,
            description=(
                "Search the live web for current information, news, "
                "or anything outside the model's training data."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for.",
                    }
                },
                "required": ["query"],
            },
        )

        self.tools.register(
            name="get_weather",
            function=web_tools.get_weather,
            description="Get the current weather for a location.",
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name, e.g. 'Mumbai'. Leave blank for current location.",
                    }
                },
            },
        )

        # ---------------------------------------------------
        # Phase 14 — Hands on the Machine
        # ---------------------------------------------------

        self.tools.register(
            name="list_windows",
            function=machine_tools.list_windows,
            description="List titles of all open windows.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="focus_window",
            function=machine_tools.focus_window,
            description="Bring a window to the foreground by (partial) title.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Window title or part of it."}
                },
                "required": ["title"],
            },
        )

        self.tools.register(
            name="minimize_window",
            function=machine_tools.minimize_window,
            description="Minimize a window by (partial) title.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Window title or part of it."}
                },
                "required": ["title"],
            },
        )

        self.tools.register(
            name="maximize_window",
            function=machine_tools.maximize_window,
            description="Maximize a window by (partial) title.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Window title or part of it."}
                },
                "required": ["title"],
            },
        )

        self.tools.register(
            name="close_window",
            function=machine_tools.close_window,
            description="Close a window by (partial) title. May discard unsaved work.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Window title or part of it."}
                },
                "required": ["title"],
            },
            destructive=True,
        )

        self.tools.register(
            name="get_volume",
            function=machine_tools.get_volume,
            description="Get the current system volume and mute state.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="set_volume",
            function=machine_tools.set_volume,
            description="Set system volume to a percentage (0-100).",
            parameters={
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Volume percentage, 0-100."}
                },
                "required": ["level"],
            },
        )

        self.tools.register(
            name="mute",
            function=machine_tools.mute,
            description="Mute or unmute system audio.",
            parameters={
                "type": "object",
                "properties": {
                    "should_mute": {"type": "boolean", "description": "True to mute, false to unmute."}
                },
            },
        )

        self.tools.register(
            name="get_brightness",
            function=machine_tools.get_brightness,
            description="Get current screen brightness (0-100).",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="set_brightness",
            function=machine_tools.set_brightness,
            description="Set screen brightness to a percentage (0-100).",
            parameters={
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Brightness percentage, 0-100."}
                },
                "required": ["level"],
            },
        )

        self.tools.register(
            name="get_clipboard",
            function=machine_tools.get_clipboard,
            description="Read the current clipboard contents.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="set_clipboard",
            function=machine_tools.set_clipboard,
            description="Write text to the clipboard.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy to the clipboard."}
                },
                "required": ["text"],
            },
        )

        self.tools.register(
            name="take_screenshot",
            function=machine_tools.take_screenshot,
            description="Capture the screen and save it as a PNG.",
            parameters={
                "type": "object",
                "properties": {
                    "save_path": {"type": "string", "description": "Where to save it. Optional."}
                },
            },
        )

        self.tools.register(
            name="list_processes",
            function=machine_tools.list_processes,
            description="List running processes, optionally filtered by name.",
            parameters={
                "type": "object",
                "properties": {
                    "filter_name": {"type": "string", "description": "Name substring to filter by. Optional."}
                },
            },
        )

        self.tools.register(
            name="kill_process",
            function=machine_tools.kill_process,
            description="Kill a process by name or PID. Unsaved work in it is lost.",
            parameters={
                "type": "object",
                "properties": {
                    "name_or_pid": {"type": "string", "description": "Process name or PID."}
                },
                "required": ["name_or_pid"],
            },
            destructive=True,
        )

        self.tools.register(
            name="search_files",
            function=machine_tools.search_files,
            description="Search for files by name under a directory.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename substring to search for."},
                    "directory": {"type": "string", "description": "Directory to search under. Optional, defaults to home."},
                },
                "required": ["query"],
            },
        )

        self.tools.register(
            name="move_file",
            function=machine_tools.move_file,
            description="Move or rename a file/folder to a new location.",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Current path."},
                    "destination": {"type": "string", "description": "New path."},
                },
                "required": ["source", "destination"],
            },
        )

        self.tools.register(
            name="rename_file",
            function=machine_tools.rename_file,
            description="Rename a file or folder in place.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file/folder."},
                    "new_name": {"type": "string", "description": "New name (not full path)."},
                },
                "required": ["path", "new_name"],
            },
        )

        self.tools.register(
            name="read_file",
            function=machine_tools.read_file,
            description="Read a text file's contents.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."}
                },
                "required": ["path"],
            },
        )

        self.tools.register(
            name="delete_file",
            function=machine_tools.delete_file,
            description="Delete a file or folder. Irreversible.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to delete."}
                },
                "required": ["path"],
            },
            destructive=True,
        )

        # ---------------------------------------------------
        # Phase 15 — He Speaks First
        # ---------------------------------------------------

        self.tools.register(
            name="schedule_reminder",
            function=self.scheduler.schedule_reminder,
            description="Set a one-off reminder that fires after N minutes.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "What to remind about."},
                    "minutes": {"type": "number", "description": "Minutes from now to fire."},
                },
                "required": ["message", "minutes"],
            },
        )

        self.tools.register(
            name="schedule_file_watch",
            function=self.scheduler.schedule_file_watch,
            description="Watch for a file/folder to appear, and notify when it does.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or folder path to watch for."},
                    "message": {"type": "string", "description": "What to say once it appears. Optional."},
                },
                "required": ["path"],
            },
        )

    def shutdown(self):
        """
        Stops the background scheduler. Call on process exit so
        pending jobs don't keep the interpreter alive.
        """

        self.scheduler.shutdown()

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
            content = message.get("content") or ""

            # Weaker models sometimes spit out broken tool-call syntax
            # ("functions.get_current_time:") that's neither a real call
            # nor a real answer. Never show that — regenerate once as a
            # plain reply with no tools to tempt it.
            if self._looks_like_leaked_tool_syntax(content):
                return self.llm.generate(messages)

            return content or self.llm.generate(messages)

        # If the model wants to run anything destructive, stop here
        # and ask first — don't execute any call in this batch yet,
        # including the safe ones, to keep the turn simple to reason
        # about. The confirmed action resumes via
        # _handle_pending_confirmation on the next turn.
        for call in tool_calls:
            function = call.get("function", {})
            name = function.get("name")

            if self.tools.is_destructive(name):
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                return self._request_confirmation(name, arguments)

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

    def _parse_text_tool_calls(self, content: str) -> list:
        """
        Best-effort parser for models that emit tool calls as plain
        text instead of llama.cpp's structured tool_calls field.
        Returns the same shape generate_with_tools would, so the
        rest of the pipeline doesn't need to know which path fired.

        Only emits a call when the name matches a really-registered
        tool — so leaked-but-meaningless syntax (e.g. gemma-2-2b's
        "functions.get_current_time:" with no real intent) is ignored
        here and handled as junk by _looks_like_leaked_tool_syntax.
        """

        valid = set(self.tools.list_tools())
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

            if name in valid:
                calls.append({
                    "id": f"text_call_{len(calls)}",
                    "function": {"name": name, "arguments": raw_args},
                })

        # Gemma style: functions.NAME: {args}  or  functions.NAME(args)
        for name, raw_args in re.findall(
            r"functions\.([\w_]+)\s*[:\(]\s*(\{.*?\})?", content, re.DOTALL
        ):
            if name in valid:
                calls.append({
                    "id": f"text_call_{len(calls)}",
                    "function": {"name": name, "arguments": raw_args or "{}"},
                })

        return calls

    def _looks_like_leaked_tool_syntax(self, content: str) -> bool:
        """
        True when content is leaked tool-call scaffolding rather than a
        real answer — e.g. "functions.get_current_time:" or a stray
        <tool_call> tag that _parse_text_tool_calls couldn't turn into
        a valid call. Such fragments must never reach the user.
        """

        text = (content or "").strip()

        if not text:
            return False

        return bool(
            re.search(r"functions\.[\w_]+\s*[:\(]", text)
            or "<tool_call>" in text
            or re.match(r"^<function=", text)
        )

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

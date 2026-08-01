# Core/orchestrator/orchestrator.py

import json
import re
from datetime import datetime

from state.conversation_state import ConversationState
from memory.memory_manager import MemoryManager
from llm.llm_client import LLMClient
from personality.system_prompt import SYSTEM_PROMPT
from tools.registry import ToolRegistry
from tools import system_tools
from tools import web_tools
from tools import machine_tools
from tools import assist_tools
from tools import git_tools
from tools import smart_search
from orchestrator import canned_replies
from orchestrator.dispatcher import Dispatcher
from orchestrator.scheduler import ReminderScheduler
from orchestrator import proactive_checks
from orchestrator import intent
from orchestrator import tool_call_log
from orchestrator.vault_router import VaultRouter
from utils import event_log
from utils.vault_md import flatten_tables
from config.settings import TOOLS_ENABLED, VAULT_CHUNK_INJECT_CHARS


# Present-tense phrases for the pill's tool-fire confirmation. Written as
# what FRED is doing rather than the function name, since this is read by a
# human at a glance, not parsed.
TOOL_LABELS = {
    "open_website": "Opening website",
    "launch_application": "Launching app",
    "open_path": "Opening",
    "web_search": "Searching the web",
    "get_weather": "Checking weather",
    "get_current_time": "Checking the time",
    "calculate": "Calculating",
    "get_system_status": "Checking system",
    "get_network_status": "Checking network",
    "media_control": "Media",
    "power_action": "Power",
    "get_volume": "Checking volume",
    "set_volume": "Setting volume",
    "mute": "Muting",
    "get_brightness": "Checking brightness",
    "set_brightness": "Setting brightness",
    "get_clipboard": "Reading clipboard",
    "set_clipboard": "Copying",
    "take_screenshot": "Taking screenshot",
    "list_windows": "Listing windows",
    "focus_window": "Switching window",
    "minimize_window": "Minimising",
    "maximize_window": "Maximising",
    "close_window": "Closing window",
    "list_processes": "Listing processes",
    "kill_process": "Ending process",
    "create_text_file": "Creating file",
    "create_folder": "Creating folder",
    "append_to_file": "Adding to file",
    "read_file": "Reading file",
    "list_directory": "Listing folder",
    "search_files": "Searching files",
    "find_file_smart": "Searching thoroughly",
    "git_status": "Checking git status",
    "git_log": "Checking git history",
    "git_diff_summary": "Checking git changes",
    "move_file": "Moving file",
    "rename_file": "Renaming",
    "delete_file": "Deleting",
    "schedule_reminder": "Setting reminder",
    "set_timer": "Setting timer",
    "schedule_file_watch": "Watching for file",
    "list_scheduled": "Checking reminders",
    "cancel_scheduled": "Cancelling",
}


# Tools whose own return string is already a complete spoken sentence,
# so the tool-calling loop's second LLM pass (re-phrasing the raw result
# into something sayable) is skipped entirely when every tool called this
# turn is in this set — see the skip in _generate_with_tools. Deliberately
# a short, conservative list: only tools recently rewritten to return full
# sentences on purpose (see tools/assist_tools.py, tools/machine_tools.py,
# orchestrator/scheduler.py), not "whatever currently happens to look
# okay" — a tool added later defaults to going through the follow-up pass
# until someone deliberately decides its phrasing needs no help.
#
# calculate() was here and is deliberately NOT any more. The bare-
# arithmetic case that motivated it ("what's 12 times 8") never reaches
# this code path at all — dispatcher.py's _route_calculate /
# _route_calculate_bare already catch that with zero LLM calls, before
# the tool-loop even starts (verified: dispatcher.match() returns a hit
# for "what is 12 times 8", "12 times 8", "what is 17 percent of 300").
# So by construction, anything that reaches calculate() THROUGH the tool
# loop already failed the dispatcher's bare-math check — it's a
# calculation embedded in a larger question, e.g. "a shirt is 40% off
# then another 20% off, is that the same as 60% off?". Skipping the
# follow-up there answered the arithmetic (48) and never answered the
# actual question (no). calculate() stays self-narrating for the
# dispatcher's zero-LLM path; the tool-loop path now always gets its
# interpretive sentence.
SELF_NARRATING_TOOLS = {
    "get_current_time",
    "get_weather",
    "get_system_status",
    "get_network_status",
    "get_volume",
    "set_volume",
    "mute",
    "get_brightness",
    "set_brightness",
    "schedule_reminder",
    "set_timer",
    "list_scheduled",
    "cancel_scheduled",
}


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
        proactive_checks.register(self.scheduler)

        self.tools = ToolRegistry()
        self._register_tools()

        self.dispatcher = Dispatcher()

        # Set whenever a destructive tool is awaiting a yes/no before
        # it's allowed to run. See _request_confirmation /
        # _handle_pending_confirmation.
        self.pending_action = None

        # Semantic tool router, built lazily on the first tool-eligible
        # turn (see _tool_router).
        self._router = None
        self._vault = None

        # Per-turn scratch, set at the top of process()/process_stream()
        # and read by the tool-execution paths below — orchestrator
        # processes one turn at a time (pill_app serialises via its own
        # lock), so this is safe without threading utterance/turn_id
        # through every call signature. Exposed via last_turn_id so the
        # UI can attach post-hoc feedback (e.g. "user interrupted this
        # reply") to the same row after the fact.
        self._turn_utterance = ""
        self.last_turn_id = None
        self._last_tools_offered = []
        self._last_routing_reason = ""

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
        self._turn_utterance = user_input
        self.last_turn_id = tool_call_log.new_turn_id()

        canned = None if self.pending_action else canned_replies.match(user_input)

        if self.pending_action:
            assistant_reply = self._handle_pending_confirmation(user_input)
        elif canned:
            assistant_reply = canned
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

    def process_stream(self, user_input: str):
        """
        Same pipeline as process(), but yields the reply in pieces when it
        can, so the caller can start speaking before generation finishes.

        Streams only on the plain-conversation path. A dispatcher hit, a
        pending confirmation, or a turn that needs tools all produce their
        reply from something that must complete first — a tool result
        can't be narrated before the tool has run — so those yield once,
        whole. Callers therefore always get an iterator and never have to
        know which path ran.

        Memory and conversation state are written after the stream is
        drained, using the assembled text, so history is identical either
        way.
        """
        self.state.add_message("user", user_input)
        self._turn_utterance = user_input
        self.last_turn_id = tool_call_log.new_turn_id()

        pieces = []

        def finish(reply: str):
            self.state.add_message("assistant", reply)
            self.memory.store("user", user_input)
            self.memory.store("assistant", reply)

        if self.pending_action:
            reply = self._handle_pending_confirmation(user_input)
            finish(reply)
            yield reply
            return

        canned = canned_replies.match(user_input)
        if canned:
            finish(canned)
            yield canned
            return

        dispatch = self.dispatcher.match(user_input)
        if dispatch:
            reply = self._run_or_confirm(dispatch["tool"], dispatch["arguments"])
            finish(reply)
            yield reply
            return

        if not TOOLS_ENABLED:
            needs_tools, tool_names, reason = False, [], "tools disabled"
        else:
            needs_tools, tool_names, reason = intent.classify(
                user_input, llm=self.llm, router=self._tool_router()
            )

        if needs_tools:
            print(f"[intent] tools ({reason}) — not streaming")
            reply = self._process_with_llm(user_input)
            finish(reply)
            yield reply
            return

        print(f"[intent] chat ({reason}) — streaming")

        # Same retrieval and prompt shape as _process_with_llm, so a
        # streamed chat turn and a non-streamed one see identical context.
        messages = self._build_messages(
            recent_messages=self.state.get_recent_messages(limit=10),
            memories=self.memory.retrieve_relevant(query=user_input, top_k=5),
            user_input=user_input,
        )

        for piece in self.llm.generate_stream(messages):
            if piece:
                pieces.append(piece)
                yield piece

        reply = "".join(pieces).strip()
        if not reply:
            # Streaming produced nothing usable — fall back rather than
            # leaving the turn silent.
            reply = self.llm.generate(messages)
            yield reply

        finish(reply)

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

        # Announced here as well as in _execute_tool_call: the dispatcher
        # resolves obvious commands without ever reaching the tool-calling
        # loop, so hooking only that loop left the fast path — which is
        # most of the common commands — with no visual confirmation.
        self._announce_tool(tool_name)

        try:
            result = str(self.tools.execute(tool_name, **arguments))
        except Exception as error:
            result = f"Couldn't do that: {error}"
            event_log.log_error(f"tool:{tool_name}", error)

        tool_call_log.log_tool_call(
            self.last_turn_id, self._turn_utterance, tool_name, arguments,
            result, path="dispatcher",
        )
        event_log.log(
            "tool_call", tool=tool_name, arguments=arguments,
            result=result[:300], path="dispatcher",
        )
        return result

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
            except Exception as error:
                result = f"Couldn't do that: {error}"
                event_log.log_error(f"tool:{action['tool']}", error)

            # The turn_id here belongs to the confirmation ("yes"), not the
            # original destructive request — the two are separate calls to
            # process(). Logged anyway since the tool+arguments pair is
            # what matters for learning routing, and it's still linkable
            # by timestamp proximity if that turns out to matter later.
            tool_call_log.log_tool_call(
                self.last_turn_id, self._turn_utterance, action["tool"],
                action["arguments"], result, path="confirmed_destructive",
            )
            event_log.log(
                "tool_call", tool=action["tool"], arguments=action["arguments"],
                result=result[:300], path="confirmed_destructive",
            )
            return result

        tool_call_log.log_tool_call(
            self.last_turn_id, self._turn_utterance, action["tool"],
            action["arguments"], "Cancelled by user", path="confirmed_destructive",
        )
        event_log.log(
            "tool_call", tool=action["tool"], arguments=action["arguments"],
            result="Cancelled by user", path="confirmed_destructive",
        )
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
            description="Search for files by exact/partial filename under a directory.",
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
            name="find_file_smart",
            function=self._find_file_smart,
            description=(
                "Find a file when you don't know its exact name — describe what "
                "it is (e.g. 'my health logs') and this reasons through the folder "
                "tree instead of matching a filename substring. Slower than "
                "search_files; use search_files first if the filename is known."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What the file is, in plain language."},
                    "directory": {"type": "string", "description": "Where to start looking. Optional, defaults to home."},
                },
                "required": ["description"],
            },
        )

        # ---------------------------------------------------
        # Git — read-only (Suggestion #1). status/log/diff-summary
        # only, nothing that mutates repo state. See git_tools.py.
        # ---------------------------------------------------

        self.tools.register(
            name="git_status",
            function=git_tools.git_status,
            description="Current git branch and a summary of staged/modified/untracked files.",
            parameters={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repo folder. Optional, defaults to the FRED project."},
                },
                "required": [],
            },
        )

        self.tools.register(
            name="git_log",
            function=git_tools.git_log,
            description="Recent git commit history — subjects and relative times.",
            parameters={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repo folder. Optional, defaults to the FRED project."},
                    "count": {"type": "integer", "description": "How many recent commits. Optional, defaults to 5."},
                },
                "required": [],
            },
        )

        self.tools.register(
            name="git_diff_summary",
            function=git_tools.git_diff_summary,
            description="Summary of uncommitted changes — files and line counts, not the raw diff.",
            parameters={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Repo folder. Optional, defaults to the FRED project."},
                },
                "required": [],
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
            description=(
                "Set a one-off reminder. Use 'when' for a clock time "
                "(\"7pm\", \"tomorrow at 8:30am\", \"19:00\"), or 'minutes' "
                "for an offset from now. Give exactly one of them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "What to remind about."},
                    "when": {
                        "type": "string",
                        "description": (
                            "Absolute time, e.g. \"7pm\", \"7:30 am\", "
                            "\"19:00\", \"tomorrow at 8am\", \"noon\". "
                            "A time already past rolls to the next day."
                        ),
                    },
                    "minutes": {"type": "number", "description": "Minutes from now to fire."},
                },
                # Only the message is truly required — the time can arrive
                # as either field, and marking both required made the model
                # invent a minutes value alongside every clock time.
                "required": ["message"],
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

        self.tools.register(
            name="list_scheduled",
            function=self.scheduler.list_scheduled,
            description="List every pending reminder and file watch.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="cancel_scheduled",
            function=self.scheduler.cancel_scheduled,
            description="Cancel a pending reminder or file watch by id, by a word from its message, or 'all'.",
            parameters={
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Job id, message substring, or 'all'."}
                },
                "required": ["identifier"],
            },
        )

        # =========================================================
        # ADDITIONS — see tools/assist_tools.py
        # =========================================================

        self.tools.register(
            name="calculate",
            function=assist_tools.calculate,
            description=(
                "Work out an arithmetic expression exactly. Use this for any "
                "sum, percentage, or number question instead of answering "
                "from memory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. \"3 * 7 - 4\", \"17% of 300\", \"sqrt(144)\".",
                    }
                },
                "required": ["expression"],
            },
        )

        self.tools.register(
            name="get_system_status",
            function=assist_tools.get_system_status,
            description="Battery level, CPU and memory use, free disk space and uptime.",
            parameters={"type": "object", "properties": {}, "required": []},
        )

        self.tools.register(
            name="get_network_status",
            function=assist_tools.get_network_status,
            description="Whether the machine is online, which Wi-Fi network, and the local IP.",
            parameters={"type": "object", "properties": {}, "required": []},
        )

        self.tools.register(
            name="media_control",
            function=assist_tools.media_control,
            description=(
                "Control whatever is playing music or video — play/pause, "
                "skip, go back, stop. Works with any player."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["playpause", "next", "previous", "stop"],
                        "description": "Which media action to send.",
                    }
                },
                "required": ["action"],
            },
        )

        self.tools.register(
            name="power_action",
            function=assist_tools.power_action,
            description=(
                "Lock, sleep, restart or shut down the PC. Shutdown and "
                "restart wait 5 seconds and can be cancelled."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["lock", "sleep", "restart", "shutdown", "cancel"],
                        "description": "Which power action to take.",
                    }
                },
                "required": ["action"],
            },
            destructive=True,
        )

        self.tools.register(
            name="append_to_file",
            function=assist_tools.append_to_file,
            description=(
                "Add a line to the end of a text file, creating it if it "
                "doesn't exist. Use for notes and lists."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "File name or path. Bare names go to Documents/FRED.",
                    },
                    "text": {"type": "string", "description": "The line to add."},
                },
                "required": ["filename", "text"],
            },
        )

        self.tools.register(
            name="list_directory",
            function=assist_tools.list_directory,
            description="List what's inside a folder. Defaults to Documents/FRED.",
            parameters={
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Folder to list."}
                },
                "required": [],
            },
        )

        self.tools.register(
            name="open_path",
            function=assist_tools.open_path,
            description=(
                "Open an existing file or folder with its default program. "
                "For programs use launch_application; for sites use open_website."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or folder to open."}
                },
                "required": ["path"],
            },
        )

        self.tools.register(
            name="set_timer",
            function=self.scheduler.set_timer,
            description=(
                "Start a countdown timer for a number of minutes. For a clock "
                "time like \"7pm\" use schedule_reminder instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "How many minutes to count down."},
                    "label": {"type": "string", "description": "Optional name, e.g. \"pasta\"."},
                },
                "required": ["minutes"],
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

        # Conversation bypasses tools entirely. Handing the model ~30 tool
        # definitions on a turn that needs none is what made it choose one
        # anyway — there is no "reply normally" entry in that menu to
        # compete with them. Not showing the tools makes a misfire
        # impossible rather than merely unlikely.
        last_user = next(
            (m.get("content", "") for m in reversed(messages)
             if m.get("role") == "user"),
            "",
        )
        needs_tools, tool_names, reason = intent.classify(
            last_user, llm=self.llm, router=self._tool_router()
        )
        if not needs_tools:
            print(f"[intent] chat ({reason})")
            return self.llm.generate(messages)

        print(f"[intent] tools ({reason})")

        # Read by _execute_tool_call so the log records what the model was
        # actually offered, not just what it picked.
        self._last_tools_offered = tool_names
        self._last_routing_reason = reason

        # Only the matched category's tools. Choosing between four volume
        # tools is something a 4B does reliably; choosing among forty is
        # not, and that mismatch was the whole source of erratic calls.
        tool_definitions = self.tools.get_tool_definitions(only=tool_names)

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

        # Skip the second LLM call entirely when every tool this turn
        # called already returns a complete spoken sentence — see
        # SELF_NARRATING_TOOLS, and its note on why calculate() is
        # deliberately not in it: this loop only ever sees calculate()
        # results for calculations embedded in a bigger question, which
        # need interpreting, not just stating.
        called_names = {c.get("function", {}).get("name") for c in tool_calls}
        if called_names and called_names <= SELF_NARRATING_TOOLS:
            return " ".join(tool_results)

        # Ask once more, now with tool results in context, for the
        # natural-language reply FRED actually says out loud. The
        # function-calling chat format needs `tools` passed again to
        # correctly render the tool-result history — without it, the
        # model loses track of what it just did and may contradict
        # the action it actually took.
        follow_up = self.llm.generate_with_tools(messages, tools=tool_definitions)
        follow_up_content = follow_up.get("content") or ""

        # Same leaked-syntax risk applies to this turn too — a
        # confused model can echo broken tool syntax here just as
        # easily as on the first pass. Never show that.
        if self._looks_like_leaked_tool_syntax(follow_up_content):
            return " ".join(tool_results)

        # llama.cpp's function-calling format sometimes returns empty
        # content for this final turn regardless of model strength —
        # fall back to the tool's own (already human-readable) result
        # rather than a generic "Done." that says nothing real.
        return follow_up_content or " ".join(tool_results)

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

        # Gemma 4 native: <|tool_call>call:NAME{args}<tool_call|>
        #
        # This path exists because giving Gemma 4 its own chat template
        # (needed to enable thinking — see CHAT_FORMAT_BY_TIER) gives up
        # llama-cpp-python's tool-call parsing, which lives only in the
        # chatml-function-calling handler. Nothing else recognised this
        # syntax, so the raw call text was reaching the user as the answer.
        for name, raw_args in re.findall(
            r"<\|tool_call>\s*call:\s*([\w_]+)\s*(\{.*?\})?\s*<tool_call\|>",
            content,
            re.DOTALL,
        ):
            if name in valid:
                calls.append({
                    "id": f"gemma_call_{len(calls)}",
                    "function": {
                        "name": name,
                        "arguments": self._gemma_args_to_json(raw_args),
                    },
                })

        for block in re.findall(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
            block = block.strip()

            # Hermes/Nemotron style: <function=NAME>{args}</function>
            match = re.match(
                r"<function=([\w_]+)>(.*?)</function>", block, re.DOTALL
            )

            if match:
                name = match.group(1).strip()
                inner = match.group(2).strip()

                # Qwen3.5 style, nested one level deeper than Hermes:
                # <function=NAME><parameter=key>value</parameter>...
                # </function> — the body is NOT a JSON blob, it's one
                # tag per argument. Assuming it already was JSON (the
                # Hermes assumption below) fed the literal XML fragment
                # into json.loads downstream, which failed with
                # "malformed arguments" on every tool call this tier
                # made — reproduced directly: asking a percentage
                # question routed to calculate() with exactly this shape.
                params = re.findall(
                    r"<parameter=([\w_]+)>(.*?)</parameter>", inner, re.DOTALL
                )
                if params:
                    raw_args = json.dumps({
                        k: self._coerce_text_arg(v) for k, v in params
                    })
                else:
                    raw_args = inner or "{}"
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
        # This pattern is the loosest of the three — a model just
        # echoing a tool name with no real intent behind it (e.g.
        # "functions.set_volume:" in response to an unrelated request)
        # looks identical to a genuine bare call. Only trust it when
        # there are real captured args, or the tool needs none at all
        # — a tool with required args but an empty {} here is a
        # strong signal this is hallucinated noise, not a real call.
        for name, raw_args in re.findall(
            r"functions\.([\w_]+)\s*[:\(]\s*(\{.*?\})?", content, re.DOTALL
        ):
            if name not in valid:
                continue

            required = self.tools.tools[name]["parameters"].get("required", [])

            if not raw_args and required:
                continue

            calls.append({
                "id": f"text_call_{len(calls)}",
                "function": {"name": name, "arguments": raw_args or "{}"},
            })

        return calls

    @staticmethod
    def _coerce_text_arg(value: str):
        """
        Qwen3.5's <parameter=key>value</parameter> syntax gives every
        argument as bare text — there is no type information the way a
        JSON blob would carry it. A tool schema like set_volume(level:
        int) then gets the string "50" instead of 50. Coerce the common
        cases; leave anything else as a string, which is the safe
        default a JSON number/bool would have been anyway if this
        guessed wrong.
        """
        v = value.strip()
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        if re.fullmatch(r"-?\d+\.\d+", v):
            return float(v)
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        return v

    @staticmethod
    def _gemma_args_to_json(raw: str) -> str:
        """
        Convert Gemma 4's argument syntax to JSON.

        It renders arguments near-JSON but not quite: keys are bare and
        strings are wrapped in its own <|"|> token rather than quotes,
        e.g. {location:<|"|>London<|"|>,days:3}. _execute_tool_call
        json.loads() this, so it has to be real JSON by the time it
        arrives, or every call fails as "malformed arguments".

        Returns "{}" if the result still isn't valid JSON — a call with
        no arguments is recoverable, a crash is not.
        """
        if not raw or not raw.strip() or raw.strip() == "{}":
            return "{}"

        text = raw.replace('<|"|>', '"')
        # Quote bare keys: {name: -> {"name":
        text = re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', text)

        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            print(f"[orchestrator] could not parse Gemma args: {raw!r}")
            return "{}"

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
            # Gemma 4's own markers. Without these the model's raw
            # "<|tool_call>call:get_current_time{}<tool_call|>" was
            # spoken aloud verbatim, since it matches none of the
            # patterns above.
            or "<|tool_call>" in text
            or "<tool_call|>" in text
            or "<|channel>" in text
            or re.match(r"^<function=", text)
        )

    # Set by the UI controller to show what FRED just did. Left as None
    # for the CLI, which prints tool results anyway. Phase 16 asked for
    # "visual confirmation when tools fire" — without it, an action is
    # audio-only and there's no way to see that it actually happened.
    on_tool_event = None

    def _announce_tool(self, name: str):
        if not self.on_tool_event:
            return
        try:
            self.on_tool_event(TOOL_LABELS.get(name, name.replace("_", " ")))
        except Exception as e:
            print(f"[orchestrator] tool-event hook failed: {e}")

    def _find_file_smart(self, description: str, directory: str = "") -> str:
        """
        Bound wrapper so find_file_smart (tools/smart_search.py) gets
        an LLM handle without every plain tool function needing one —
        same shape as self.scheduler.* being registered directly for
        tools that need orchestrator-level state.
        """
        return smart_search.find_file_smart(description, directory, llm=self.llm)

    def _execute_tool_call(self, call: dict) -> str:

        function = call.get("function", {})
        name = function.get("name")
        raw_arguments = function.get("arguments") or "{}"

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return f"Error: malformed arguments for tool '{name}'."

        self._announce_tool(name)

        try:
            result = self.tools.execute(name, **arguments)
        except Exception as error:
            result = f"Error running tool '{name}': {error}"
            event_log.log_error(f"tool:{name}", error)

        tool_call_log.log_tool_call(
            self.last_turn_id, self._turn_utterance, name, arguments, result,
            path="tool_loop",
            tools_offered=self._last_tools_offered,
            reason=self._last_routing_reason,
        )
        event_log.log(
            "tool_call", tool=name, arguments=arguments,
            result=str(result)[:300], path="tool_loop",
        )
        return result

    # =========================================================
    # MESSAGE BUILDING
    # =========================================================

    def _vault_router(self):
        """
        The vault knowledge router, built on first use — same lazy pattern
        as _tool_router, reusing the same embedder so this is zero extra
        VRAM. Cold build re-embeds only vault files whose content changed
        since the on-disk cache was written (measured: 19.7s cold, 0.04s
        fully cached), so this cost is paid once per changed file, not
        once per FRED launch.
        """
        if self._vault is not None:
            return self._vault

        try:
            router = VaultRouter(self.memory._generate_embedding)
            if router.build():
                self._vault = router
        except Exception as e:
            print(f"[orchestrator] vault router unavailable: {e}")

        return self._vault

    def _tool_router(self):
        """
        The semantic router, built on first use.

        Lazy because building it embeds all 40 tools (~4.8s) and there's no
        reason to pay that at startup when a session might be all
        conversation. Reuses the memory embedder rather than loading a
        second copy of the model, and returns None on any failure so
        classification falls back to cue matching.
        """
        if self._router is not None:
            return self._router

        try:
            from orchestrator.tool_router import SemanticToolRouter

            descriptions = {
                name: self.tools.tools[name]["description"]
                for name in self.tools.list_tools()
            }
            router = SemanticToolRouter(
                self.memory._generate_embedding, descriptions
            )
            if router.build():
                self._router = router
        except Exception as e:
            print(f"[orchestrator] semantic router unavailable: {e}")

        return self._router

    @staticmethod
    def _screen_context() -> str:
        """
        One line naming the active window and the local time.

        Deliberately just the title, not a screenshot — it costs a single
        Win32 call, needs no vision model, and answers most of what
        context is actually for. Returns "" on any failure so a missing
        window manager can never break a turn.
        """
        try:
            import win32gui

            title = (win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "").strip()
        except Exception:
            title = ""

        now = datetime.now().strftime("%A %d %B, %I:%M %p").replace(" 0", " ")

        if not title or title in ("FRED_PILL", "Program Manager"):
            return f"Current context: it is {now}."

        return (
            f"Current context: it is {now}. The user is currently looking at "
            f"\"{title}\". Mention this only if it is relevant to what they asked."
        )

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
        # System prompt — ONE message, not one per section.
        # -----------------------------
        # Used to be four separate {"role": "system", ...} entries
        # (persona, screen context, vault knowledge, memory). gemma4's
        # template tolerated that silently; Qwen3.5's native template
        # enforces exactly one leading system message and raises "System
        # message must be at the beginning" the moment a second one
        # appears — which surfaced as every real reply falling through to
        # the generic "cognitive malfunction" string, since generate()
        # catches the exception and returns that fallback rather than
        # propagating it. Concatenating into one message is also just
        # more correct prompt construction regardless of model.
        system_sections = [SYSTEM_PROMPT]

        # Ambient screen context: what's in front of the user right now,
        # so "what am I doing" and app-implicit requests ("close this",
        # "what's this error") have something to resolve against. Cheap:
        # one Win32 title read per turn, no screenshot, no vision model.
        context = self._screen_context()
        if context:
            system_sections.append(context)

        # Vault knowledge (the other files — persona/profile/rules are
        # loaded directly and always, see personality/system_prompt.py)
        vault_router = self._vault_router()
        if vault_router:
            hits = vault_router.retrieve(user_input)
            if hits:
                # Tables are flattened to "row — Column: value" BEFORE
                # truncation, so a value can never be read out of the
                # wrong column — see utils.vault_md.flatten_tables for
                # the confirmed failure that motivated it.
                vault_text = "\n".join(
                    f"- [{label}] {flat[:VAULT_CHUNK_INJECT_CHARS]}"
                    + ("..." if len(flat) > VAULT_CHUNK_INJECT_CHARS else "")
                    for label, flat, _score in (
                        (label, flatten_tables(text), score)
                        for label, text, score in hits
                    )
                )
                # The anti-fabrication instruction is NOT optional framing.
                # Confirmed 2026-08-01: asked to "review my fitness
                # progress", FRED invented an entire report — a weight, a
                # BMI, a body-fat percentage and a full bloodwork panel,
                # none of which matched personal/fitness.md, which records
                # different figures and no bloodwork at all. (The real
                # values are deliberately not quoted here: that file is
                # marked sensitive and rules.md forbids copying personal/
                # content into the repo.) Chunks arrive truncated
                # (VAULT_CHUNK_INJECT_CHARS), so a cut-off table looks to
                # the model like a form to complete, and nothing in the
                # prompt previously said the vault was the only admissible
                # source for a personal fact. Inventing health data is the
                # worst failure this system can have — rules.md's "don't
                # launder guesses into facts" applies double here.
                system_sections.append(
                    "Relevant vault knowledge:\n"
                    f"{vault_text}\n\n"
                    "These vault excerpts are the ONLY valid source for facts "
                    "about Vatsal — his body, health, projects, people, "
                    "schedule, or history. Never state a number, date, "
                    "measurement, or detail about him that does not appear "
                    "verbatim above. Excerpts may be truncated mid-sentence or "
                    "mid-table; a cut-off excerpt is missing information, never "
                    "an invitation to fill in the rest. If the answer isn't in "
                    "the text above, say you don't have it and name the file "
                    "that might.\n"
                    "In tables, the column a value sits in changes what it "
                    "means: a Target or Goal is something not yet reached, a "
                    "Baseline is where he started, and only Current is true "
                    "now. Never report a target as if it were current. A dash "
                    "or blank cell means that value is genuinely unknown — say "
                    "so rather than substituting a number from another column."
                )

        # Long-term memory
        if memories:
            memory_text = "\n".join(f"- {m['content']}" for m in memories)
            system_sections.append(f"Relevant long-term memory:\n{memory_text}")

        messages.append({
            "role": "system",
            "content": "\n\n".join(system_sections),
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

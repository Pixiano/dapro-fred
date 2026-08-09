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
from tools import session_summary
from tools import vision_tools
from tools import daily_tasks
from tools import school_tasks
from tools import vault_files
from tools import workout_plan
from audio import device_info
from utils import confidence, sensitive
from orchestrator import canned_replies
from orchestrator.dispatcher import Dispatcher
from orchestrator.scheduler import ReminderScheduler
from orchestrator import proactive_checks
from orchestrator import intent
from orchestrator import tool_call_log
from orchestrator.vault_router import VaultRouter
from utils import event_log
from utils.vault_md import flatten_tables
from config.settings import (
    TOOLS_ENABLED,
    VAULT_CHUNK_INJECT_CHARS,
    SENSITIVE_LOCAL_ONLY,
)


# Present-tense phrases for the pill's tool-fire confirmation. Written as
# what FRED is doing rather than the function name, since this is read by a
# human at a glance, not parsed.
TOOL_LABELS = {
    "open_website": "Opening website",
    "launch_application": "Launching app",
    "open_path": "Opening",
    "open_vault_file": "Opening vault file",
    "web_search": "Searching the web",
    "get_weather": "Checking weather",
    "get_current_time": "Checking the time",
    "calculate": "Calculating",
    "get_system_status": "Checking system",
    "get_network_status": "Checking network",
    "media_control": "Media",
    "power_action": "Power",
    "end_of_day": "Winding down",
    "get_volume": "Checking volume",
    "set_volume": "Setting volume",
    "adjust_volume": "Adjusting volume",
    "adjust_brightness": "Adjusting brightness",
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
    "open_last_found": "Opening",
    "whats_on_screen": "Checking the screen",
    "summarise_today": "Reviewing today",
    "save_today_summary": "Saving to vault",
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
    "add_task": "Adding task",
    "list_tasks": "Checking tasks",
    "complete_task": "Updating task",
    "add_school_item": "Logging",
    "list_school_items": "Checking school",
    "update_school_item": "Updating",
    "cancel_scheduled": "Cancelling",
    "restart_fred": "Restarting",
    "schedule_recurring": "Setting recurring reminder",
    "workout_split": "Checking your split",
    "todays_workout": "Checking today's workout",
    "schedule_workouts": "Setting workout reminders",
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
    "add_task",
    "list_tasks",
    "complete_task",
    "restart_fred",
    "schedule_recurring",
    "workout_split",
    "todays_workout",
    "schedule_workouts",
    "add_school_item",
    "list_school_items",
    "update_school_item",
}

# Stricter than SELF_NARRATING_TOOLS above: for these, the raw tool
# result is preferred over the model's own words even on a COMPOUND
# turn (see the exact_readback_only tracking in _generate_with_tools).
#
# Built for "3 questions in Geography and 1 in physics, due in 3
# days" — the canonical example this feature was built to answer. Two
# add_school_item calls across two rounds is exactly the compound
# shape SELF_NARRATING_TOOLS' own compound check exists to catch, but
# THAT check exists to give a local model a second round to call the
# tool it forgot, not to improve phrasing — once both calls have
# happened, the turn ends with the model paraphrasing its own two
# tool results in one sentence. The tool results are right there in
# its context, so that paraphrase is usually fine, but "usually fine"
# on a due DATE is exactly the failure mode this whole feature exists
# to close. The read-back confirmation is the thing that makes the
# 99%-accuracy promise real; it has to be the literal string a
# deterministic function produced, not a second LLM pass restating it
# from memory a few tokens later.
#
# Deliberately a separate set from SELF_NARRATING_TOOLS, not a flag on
# it: schedule_reminder+list_scheduled is ALSO all-self-narrating on a
# compound turn, and test_compound_tool_calls.py already pins that
# case to the model's OWN synthesis ("Set for 6pm. You already had one
# other reminder.") — a natural sentence combining a check with an
# action, which raw concatenation would make worse, not better. That
# case stays on the existing path; only the tools below skip it.
EXACT_READBACK_TOOLS = {
    "add_school_item",
    "list_school_items",
    "update_school_item",
}

# Tools whose RESULT contains vault content marked sensitive — today
# that means anything reading personal/. Executing one forces the rest
# of the turn onto the local model (see _execute_tool_call), because the
# tool-calling loop feeds every result back to the LLM for phrasing and
# the cloud cascade would otherwise carry it off the machine. This is
# the tool-side half of the same rule utils/sensitive.py enforces on the
# retrieval side; a tool added later that reads personal/ or people/
# belongs here.
SENSITIVE_TOOLS = {
    "workout_split",
    "todays_workout",
    "schedule_workouts",
}

# A compound turn ("set a reminder and tell me if one exists") can need
# two tool calls, but a small local model asked for both at once
# reliably only manages one of them. This is how many extra round-trips
# _generate_with_tools gets to let it ask for the one it forgot instead
# of the request silently vanishing — see the loop's compound-request
# comment. A model still requesting tools after this many rounds is
# looping, not making progress, so the budget stops there rather than
# never.
MAX_TOOL_ROUNDS = 4

# A short follow-up carries no subject, so retrieving on it alone matches
# the vault on filler words and drops the entry the question is about —
# the model then answers from nothing and invents the content. Prepending
# the previous user turn puts the referent back in the query. Confirmed
# 2026-08-04 against the live index.
#
# ponytail: word count, not a pronoun/cue list — every follow-up shape
# shares only being short. Swap in a cue list if it misfires in practice.
FOLLOW_UP_MAX_WORDS = 6


def _retrieval_query(user_input: str, recent_messages: list) -> str:
    """A short follow-up gets the previous user turn prepended."""
    if len(user_input.split()) > FOLLOW_UP_MAX_WORDS:
        return user_input

    for msg in reversed(recent_messages or []):
        content = (msg.get("content") or "").strip()
        # The current turn is already in state, so it comes back here too.
        if msg.get("role") == "user" and content and content != user_input.strip():
            return f"{content} {user_input}"

    return user_input


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

    # Class-level default so it exists even on an instance built without
    # __init__ — tests construct a bare orchestrator via __new__ (see
    # tests/test_compound_tool_calls.py) to exercise the tool loop
    # without booting an LLM, and the tool loop reads this.
    _turn_local_only = False

    # Carry-forward state for _classify_turn. Class-level for the same
    # reason as _turn_local_only: tests build a bare orchestrator via
    # __new__ and still reach the tool loop.
    _classified_turn = (None, None)
    _carry_tools = []
    _carry_left = 0

    def __init__(self):
        self.state = ConversationState()
        self.memory = MemoryManager()
        self.llm = LLMClient()

        self.scheduler = ReminderScheduler()
        proactive_checks.register(
            self.scheduler, llm=self.llm, on_school_ask=self._prime_carry
        )

        self.tools = ToolRegistry()
        self._register_tools()

        self.dispatcher = Dispatcher()

        # Set whenever a destructive tool is awaiting a yes/no before
        # it's allowed to run. See _request_confirmation /
        # _handle_pending_confirmation.
        self.pending_action = None

        # Queued confirmations for the end-of-day sequence, walked one
        # per turn. Deliberately built on pending_action rather than a
        # parallel state machine: the yes/no parsing, the tool logging
        # and the cancelled path all already live there, and the only
        # thing missing was "and then ask the next one".
        self.pending_chain = []

        # Semantic tool router, built lazily on the first tool-eligible
        # turn (see _tool_router).
        self._router = None
        self._vault = None

        # Latched by _build_messages when this turn's vault retrieval
        # pulled sensitive content, and read by every LLM call below so
        # the cloud cascade is skipped entirely. Defaults False and is
        # recomputed per turn — a turn that retrieves nothing sensitive
        # must not inherit the previous turn's restriction.
        self._turn_local_only = False

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
            needs_tools, tool_names, reason = self._classify_turn(user_input)

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

        for piece in self.llm.generate_stream(messages, local_only=self._turn_local_only):
            if piece:
                pieces.append(piece)
                yield piece

        reply = "".join(pieces).strip()
        if not reply:
            # Streaming produced nothing usable — fall back rather than
            # leaving the turn silent.
            reply = self.llm.generate(messages, local_only=self._turn_local_only)
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

        # kill_process substring-matches by design ("code" matches every
        # process with "code" anywhere in its name), and a confirmation
        # that only echoes the raw argument back — "about to run
        # kill_process (name_or_pid=code)" — gives no way to notice
        # that before it happens. Naming the actual targets turns "yes"
        # into an informed answer instead of a guess.
        if tool_name == "kill_process":
            target = arguments.get("name_or_pid", "")
            matches = machine_tools.matching_processes(target)
            if not matches:
                self.pending_action = None
                return f"Nothing matches '{target}' — nothing to kill."
            names = ", ".join(f"{n} (PID {p})" for n, p in matches)
            plural = "es" if len(matches) > 1 else ""
            return (
                f"This can't be undone — would kill {len(matches)} process{plural}: "
                f"{names}. Confirm? (yes/no)"
            )

        description = ", ".join(f"{k}={v}" for k, v in arguments.items())

        return (
            f"This can't be undone — about to run '{tool_name}'"
            f"{f' ({description})' if description else ''}. "
            "Confirm? (yes/no)"
        )

    # ---------------------------------------------------
    # END-OF-DAY SEQUENCE
    # ---------------------------------------------------
    #
    _ABORT_WORDS = {
        "stop", "cancel", "abort", "never mind", "nevermind",
        "stop it", "forget it", "quit",
    }

    def _arm_next_step(self) -> str:
        """
        Move the next queued step into pending_action and return the
        question to ask about it. Empty string when the queue is done.
        """
        if not self.pending_chain:
            return ""

        step = self.pending_chain.pop(0)
        self.pending_action = {"tool": step["tool"], "arguments": step["arguments"]}
        return step["prompt"]

    def end_of_day(self) -> str:
        """
        The wind-down: close each open window one at a time, recap the
        day, then offer to shut the machine down.
        """
        titles = machine_tools.open_window_titles()

        self.pending_chain = [
            {
                "tool": "close_window",
                "arguments": {"title": title},
                "prompt": f"Close {title}? (yes/no)",
            }
            for title in titles
        ]

        # Summarised NOW, not when the last window closes: closing
        # windows adds nothing to the day's log, and generating it here
        # means the recap doesn't stall the sequence at its end. Say
        # "stop" at any point to leave the rest of it alone.
        summary = session_summary.summarise_today(llm=self.llm)

        self.pending_chain.append({
            "tool": "power_action",
            "arguments": {"action": "shutdown"},
            "prompt": (
                f"{summary}\n\nThat's the day, sir. Shut the machine down? (yes/no)"
            ),
        })

        opening = (
            f"Winding down. {len(titles)} window(s) open — one at a time."
            if titles else "Nothing open to close."
        )
        return f"{opening}\n{self._arm_next_step()}"

    def _handle_pending_confirmation(self, user_input: str) -> str:

        action = self.pending_action
        self.pending_action = None

        affirmative = {"yes", "y", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "ok", "okay"}

        # "no" declines one step and moves on; only an abort word ends
        # the whole wind-down. Without this an unwanted window keeps its
        # answer from cancelling everything queued behind it.
        if self.pending_chain and user_input.strip().lower() in self._ABORT_WORDS:
            self.pending_chain = []
            return "Stopped. Leaving the rest as it is."

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
            return "\n".join(filter(None, [result, self._arm_next_step()]))

        tool_call_log.log_tool_call(
            self.last_turn_id, self._turn_utterance, action["tool"],
            action["arguments"], "Cancelled by user", path="confirmed_destructive",
        )
        event_log.log(
            "tool_call", tool=action["tool"], arguments=action["arguments"],
            result="Cancelled by user", path="confirmed_destructive",
        )
        return "\n".join(filter(None, [
            "Left that one open." if self.pending_chain else "Cancelled — didn't run it.",
            self._arm_next_step(),
        ]))

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
            description="Get the current system volume and whether FRED's own voice output is muted.",
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
            description="Mute or unmute FRED's own voice output. Does not affect system audio or other apps.",
            parameters={
                "type": "object",
                "properties": {
                    "should_mute": {"type": "boolean", "description": "True to mute, false to unmute."}
                },
            },
        )

        self.tools.register(
            name="list_audio_devices",
            function=device_info.list_audio_devices,
            description="List available microphones and speakers with their device indices.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="set_input_device",
            function=device_info.set_input_device,
            description="Switch FRED's microphone to a different input device by index (see list_audio_devices).",
            parameters={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Device index from list_audio_devices."}
                },
                "required": ["index"],
            },
        )

        self.tools.register(
            name="set_output_device",
            function=device_info.set_output_device,
            description="Switch FRED's speaker to a different output device by index (see list_audio_devices).",
            parameters={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Device index from list_audio_devices."}
                },
                "required": ["index"],
            },
        )

        self.tools.register(
            name="adjust_volume",
            function=machine_tools.adjust_volume,
            description=(
                "Change volume relative to its current level — for "
                "'turn it up', 'a bit quieter', 'louder'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "'up' or 'down'."},
                    "amount": {
                        "type": "string",
                        "description": "'small', 'normal', or 'large'. Defaults to normal.",
                    },
                },
                "required": ["direction"],
            },
        )

        self.tools.register(
            name="adjust_brightness",
            function=machine_tools.adjust_brightness,
            description=(
                "Change screen brightness relative to its current level — "
                "for 'brighter', 'dim it a bit'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "'up' or 'down'."},
                    "amount": {
                        "type": "string",
                        "description": "'small', 'normal', or 'large'. Defaults to normal.",
                    },
                },
                "required": ["direction"],
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
            name="end_of_day",
            function=self.end_of_day,
            description=(
                "The wind-down sequence: close every open window one at a "
                "time, recap the day, then offer to shut the PC down. Use "
                "for 'end of day', 'wind down', 'shut everything down', "
                "'I'm done for today', 'goodnight'."
            ),
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="restart_fred",
            function=machine_tools.restart_fred,
            description=(
                "Restart FRED itself — relaunch a fresh process and shut "
                "this one down. Use for 'restart yourself', 'restart FRED', "
                "not for restarting the PC (see power_action)."
            ),
            parameters={"type": "object", "properties": {}},
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
            name="repeat_last",
            function=self._repeat_last,
            description="Repeat FRED's last spoken reply — for 'say that again', 'what did you say'.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="whats_on_screen",
            function=vision_tools.whats_on_screen,
            description=(
                "What was on screen last time the background watcher looked — "
                "for 'what am I looking at' / 'what's on my screen'. Instant, "
                "reads a cache rather than taking a new screenshot."
            ),
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="summarise_today",
            function=self._summarise_today,
            description=(
                "Recap what was worked on today, built from the session "
                "logs. Also says where it would be saved in the vault; "
                "does NOT write anything."
            ),
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="save_today_summary",
            function=self._save_today_summary,
            description=(
                "Append today's recap to the vault's daily note. Only "
                "call after the user has explicitly confirmed saving."
            ),
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="add_task",
            function=daily_tasks.add_task,
            description=(
                "Add an item to today's task list, saved to the vault's "
                "daily note. Use for 'add to my tasks', 'I need to ...', "
                "a to-do item — call once per item for a compound request. "
                "Call this the moment a task, deadline, or pending item is "
                "mentioned at all, even offhand in the middle of other "
                "conversation ('chemistry journal is due Thursday') — "
                "confirmed 2026-08-04: replying as if a task were saved "
                "without actually calling this tool leaves the vault "
                "silently out of sync with what was told to the user."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "The task, written out plainly. Include a URL "
                            "as a markdown link if one was given — this "
                            "only ever records the task, it never opens "
                            "anything."
                        ),
                    }
                },
                "required": ["text"],
            },
        )

        self.tools.register(
            name="list_tasks",
            function=daily_tasks.list_tasks,
            description="List today's tasks and whether each is done.",
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="schedule_recurring",
            function=self.scheduler.schedule_recurring,
            description=(
                "Set a REPEATING reminder — one that fires on a schedule "
                "rather than once. Use whenever the request says every, "
                "each, daily, weekly, weekdays or weekends ('remind me "
                "every weekday at 7am'). For a one-off use "
                "schedule_reminder instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "What to say when it fires.",
                    },
                    "when": {
                        "type": "string",
                        "description": (
                            "The repeating time in words, e.g. 'every weekday "
                            "at 7am', 'every monday and thursday at 6pm'."
                        ),
                    },
                },
                "required": ["message", "when"],
            },
        )

        self.tools.register(
            name="workout_split",
            function=workout_plan.describe_split,
            description=(
                "Vatsal's weekly training split — which muscle group he "
                "trains on each day, read from his workout plan. Use for "
                "'what's my split', 'what do I train on Friday'."
            ),
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="todays_workout",
            function=workout_plan.today_workout,
            description=(
                "What Vatsal is training today, or that today is a rest "
                "day. Use for 'what am I training today', 'is today a rest "
                "day', 'what's my workout'."
            ),
            parameters={"type": "object", "properties": {}},
        )

        self.tools.register(
            name="schedule_workouts",
            function=lambda **kw: workout_plan.schedule_workouts(
                self.scheduler, **kw
            ),
            description=(
                "Set up recurring daily workout reminders from Vatsal's "
                "training plan, one per training day, labelled with that "
                "day's muscle group. Use for 'set up my workout reminders', "
                "'remind me to work out'. Safe to re-run — it replaces the "
                "existing ones rather than duplicating them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hour": {
                        "type": "integer",
                        "description": "Hour in 24h time. Defaults to 16 (4pm).",
                    },
                    "minute": {
                        "type": "integer",
                        "description": "Minute. Defaults to 55, i.e. 4:55pm.",
                    },
                },
            },
        )

        self.tools.register(
            name="complete_task",
            function=daily_tasks.complete_task,
            description=(
                "Mark a task from today's list done or not done. Match "
                "on whatever part of the task text identifies it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "description": "Text that identifies the task.",
                    },
                    "done": {
                        "type": "boolean",
                        "description": "True to mark complete, false to mark incomplete. Defaults true.",
                    },
                },
                "required": ["match"],
            },
        )

        self.tools.register(
            name="add_school_item",
            function=school_tasks.add_item,
            description=(
                "Log one piece of school work: homework, a project, or "
                "an event that needs getting-ready lead time (a class "
                "trip, a movie, meeting friends). Call this the moment "
                "one is mentioned, even offhand — the same reasoning as "
                "add_task, but for anything that has its own due date "
                "or progress. Call it ONCE PER ITEM: 'geography and "
                "physics homework, due in 3 days' is two separate "
                "calls, not one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["homework", "project", "event"],
                        "description": (
                            "homework: a one-off assignment or questions. "
                            "project: multi-step work with its own "
                            "progress and a next step. event: something "
                            "at a specific time that may need getting-"
                            "ready lead time."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": "The subject or title, e.g. 'Geography', 'Physics model', 'Movie', 'Turf session'.",
                    },
                    "detail": {
                        "type": "string",
                        "description": "What it actually is, e.g. '3 questions', 'build a working model', '7 people at Inorbit'. Leave blank if there's nothing more to say.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of items to complete, e.g. 3 questions. Omit if there's no meaningful count — never use this for a headcount of people.",
                    },
                    "due": {
                        "type": "string",
                        "description": "When it's due (homework/project) or starts (event) — 'today', 'tomorrow', 'in 3 days', a weekday name, or an exact date.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Clock time, e.g. '2:45pm'. Give this for an event. Only give it for homework/project if a specific time was actually stated.",
                    },
                    "prep_minutes": {
                        "type": "integer",
                        "description": "Event only: minutes of lead time to start getting ready before it starts.",
                    },
                    "next_step": {
                        "type": "string",
                        "description": "Project only: the very next actionable step, e.g. 'buy card sheet'.",
                    },
                },
                "required": ["kind", "subject"],
            },
        )

        self.tools.register(
            name="list_school_items",
            function=school_tasks.list_items,
            description=(
                "Answer any question about school homework, projects or "
                "events — what's due, what's left, progress on "
                "something, what's happening tomorrow. Always reads the "
                "current record; never answer this from memory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when": {
                        "type": "string",
                        "enum": ["today", "tomorrow", "week", "overdue", "all"],
                        "description": "Which items to include. Default 'all' — every still-open item.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["homework", "project", "event"],
                        "description": "Restrict to one kind. Omit for all kinds.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Restrict to items whose subject contains this text, e.g. 'geography'.",
                    },
                },
                "required": [],
            },
        )

        self.tools.register(
            name="update_school_item",
            function=school_tasks.update_item,
            description=(
                "Update an existing school item: progress, done state, "
                "reschedule, or a note. This is where the ANSWER to a "
                "question FRED asked about something overdue or "
                "upcoming actually gets recorded — 'did you finish the "
                "geography questions' followed by 'yeah, two of them' "
                "must call this, never just get acknowledged in speech."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "description": "Text that identifies the item — its subject or part of it, e.g. 'geography'.",
                    },
                    "done": {
                        "type": "boolean",
                        "description": "Mark fully done (homework/project) or prep complete (event).",
                    },
                    "add_progress": {
                        "type": "integer",
                        "description": "Add this many completed items to the count so far, e.g. 2 if two more questions got done.",
                    },
                    "set_progress": {
                        "type": "integer",
                        "description": "Set the completed count directly, instead of adding to it.",
                    },
                    "new_due": {
                        "type": "string",
                        "description": "Reschedule to this new date — use when it was postponed or extended.",
                    },
                    "new_time": {
                        "type": "string",
                        "description": "New clock time to go with new_due, if one was given.",
                    },
                    "note": {
                        "type": "string",
                        "description": "A short free-text note to attach, e.g. why it's late, or a plan for a project.",
                    },
                    "next_step": {
                        "type": "string",
                        "description": "Update a project's next actionable step.",
                    },
                },
                "required": ["match"],
            },
        )

        self.tools.register(
            name="open_last_found",
            function=assist_tools.open_last_found,
            description=(
                "Open a file from the most recent search — use for "
                "'open it', 'open that one', 'open the second one'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "which": {
                        "type": "integer",
                        "description": "Which result, 1-based. Defaults to 1.",
                    }
                },
                "required": [],
            },
        )

        self.tools.register(
            name="open_path",
            function=assist_tools.open_path,
            description=(
                "Open an existing file or folder with its default program. "
                "For programs use launch_application; for sites use open_website. "
                "For a file that lives in the personal knowledge vault "
                "(persona/profile/rules, active-priorities, daily notes, or "
                "anything under projects/jobs/people/personal/reference/etc.), "
                "use open_vault_file instead — it resolves the file by name or "
                "title without needing a real path."
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
            name="open_vault_file",
            function=vault_files.open_vault_file,
            description=(
                "Open a file from the personal knowledge vault by its name, "
                "filename, or title — e.g. 'active priorities', 'goals', "
                "'machine spec'. Resolves vault files without needing a path; "
                "use this instead of open_path/read_file for anything in the vault."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The vault file's name, filename, or title.",
                    }
                },
                "required": ["name"],
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

    # How many follow-up turns may reuse the previous turn's tools.
    # 2 because that is what the confirmed failure needed: FRED listed
    # tasks, Vatsal said "No, that was for yesterday..." (turn 1) and
    # then "Check it then" (turn 2), and neither matched a cue, so both
    # were answered from context — the second by asserting a vault file
    # didn't exist without ever looking.
    # ponytail: fixed count, not a decay or topic check. Two turns of a
    # stale 3-tool menu is cheap; widen only if follow-ups get dropped.
    CARRY_TOOLS_TURNS = 2

    def _prime_carry(self, tool_names: list):
        """
        Called by proactive_checks.py right after it SPEAKS a question
        whose answer should update a school item — "you had Geography
        due today, did you finish it, or find a workaround?" — so the
        next user reply reaches update_school_item instead of landing
        wherever _classify_turn's cues happen to fall, or the chat path
        on a bare "yeah I did it".

        This is the exact deletion-confirmation failure from
        2026-08-06 (a reply with no tool to answer with, that talked
        instead of acting) recurring on a DIFFERENT trigger: there the
        missing tool was reachable once the fabrication guard widened
        the menu after the fact; here there is no such retry, because
        an unrecorded answer to "did you finish it" isn't a false
        claim FRED makes — it is Vatsal's own true answer, correctly
        spoken back, just never saved. Nothing downstream would ever
        catch that, so this has to be primed before the question is
        even answered, not repaired after.

        Runs on the scheduler's own background thread, not the turn
        thread — a real user turn arriving in the same instant can
        overwrite this a moment later. That's fine: last-write-wins is
        already how every other piece of this same scratch state
        behaves (see _classify_turn's carry-forward), and the race
        window is a live turn already in flight versus a proactive
        check that just fired, not two proactive checks against each
        other.
        """
        self._carry_tools = list(tool_names)
        self._carry_left = self.CARRY_TOOLS_TURNS

    def _classify_turn(self, text: str) -> tuple:
        """
        intent.classify(), plus one rule: a turn that matches no cue but
        immediately follows a tool turn re-offers that turn's tools
        rather than being answered from conversation context. Corrections
        and "check it then" carry their subject in the previous turn, not
        in themselves, so classify() sees nothing actionable and FRED
        answers from what it already believes.

        Memoised on `text` because both process_stream() and
        _generate_with_tools() ask, on the same turn — and the
        carry-forward is consumed, so asking twice must not change it.
        """
        if self._classified_turn[0] == text:
            return self._classified_turn[1]

        result = intent.classify(text, llm=self.llm, router=self._tool_router())
        needs_tools, tool_names, reason = result

        carry_live = bool(self._carry_left and self._carry_tools)

        if needs_tools:
            # A follow-up that classify lands on a DIFFERENT category than
            # the turn before is usually a correction to the same request,
            # not a new one — and replacing the menu wholesale is how the
            # right tool goes missing. On 2026-08-06, "I meant identity.md"
            # matched the vault-open cues, delete_file dropped out of the
            # menu, and the model described the deletion instead of
            # performing it because it had no way to perform it.
            #
            # So offer both. An empty tool_names is left alone: it is
            # classify's "no category matched, offer everything" and is
            # already maximal — narrowing it to a union would REMOVE
            # options.
            if carry_live and tool_names and set(tool_names) != set(self._carry_tools):
                tool_names = list(dict.fromkeys([*tool_names, *self._carry_tools]))
                result = (
                    True, tool_names,
                    f"{reason}; plus last turn's tools (possible correction)",
                )
            self._carry_tools, self._carry_left = tool_names, self.CARRY_TOOLS_TURNS

        # is_affirmative before looks_social, because _SOCIAL matches a
        # bare "yes" and that is the single most important turn to keep
        # the tools for: it is the user answering a question FRED asked.
        # When FRED asks via _request_confirmation this never matters —
        # pending_action catches it upstream — but when the MODEL asks in
        # prose ("Shall I delete it, sir?") nothing is pending, and the
        # "yes" fell through to chat and got a fabricated confirmation.
        elif carry_live and (intent.is_affirmative(text) or not intent.looks_social(text)):
            self._carry_left -= 1
            result = (
                True, self._carry_tools,
                f"{reason}; re-offering last turn's tools (follow-up)",
            )
        else:
            self._carry_tools, self._carry_left = [], 0

        self._classified_turn = (text, result)
        return result

    def _generate_with_tools(self, messages: list) -> str:
        """
        Asks the LLM for a response. If it requests one or more
        tools, executes them and asks again with the results, so
        the final reply reflects what actually happened.
        """

        if not TOOLS_ENABLED:
            return self.llm.generate(messages, local_only=self._turn_local_only)

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
        needs_tools, tool_names, reason = self._classify_turn(last_user)
        widened = False    # the full-menu retry fires at most once per turn

        if not needs_tools:
            print(f"[intent] chat ({reason})")
            reply = self.llm.generate(messages, local_only=self._turn_local_only)

            # The chat path runs no tools by construction, so a reply
            # claiming something is done is false every time — there is
            # no case where it could be true.
            #
            # Observed 2026-08-06 19:09:06: "Deleted personal/identity.md,
            # sir." in answer to a bare "Yes", with no tool_call anywhere
            # in the turn and the file still on disk. A lone affirmative
            # carries no verb, so the router sends it to chat; the model
            # then read its own "Shall I delete ...?" from one turn back
            # and reported the deletion as done. The earlier guard lived
            # only in the tool loop below and never saw this.
            if not self._claims_completed_action(reply):
                return reply

            print("[intent] chat reply claimed an action - rerunning with tools")
            tool_names = self.tools.list_tools()
            widened = True
        else:
            print(f"[intent] tools ({reason})")

            # The pill disambiguation chip: FRED still picks and acts on
            # the top candidate below (never blocks on this), but if the
            # top two were a genuine near-tie, show both so a wrong pick
            # is visible and correctable next turn instead of silent.
            close = intent.close_candidates(last_user, tool_names, self._tool_router())
            if close:
                self._announce_ambiguity(*close)

        # Read by _execute_tool_call so the log records what the model was
        # actually offered, not just what it picked.
        self._last_tools_offered = tool_names
        self._last_routing_reason = reason

        # Only the matched category's tools. Choosing between four volume
        # tools is something a 4B does reliably; choosing among forty is
        # not, and that mismatch was the whole source of erratic calls.
        tool_definitions = self.tools.get_tool_definitions(only=tool_names)

        message = self.llm.generate_with_tools(messages, tools=tool_definitions, local_only=self._turn_local_only)
        all_results = []
        # AND-narrowed every round below; stays True only if EVERY tool
        # called this whole turn is in EXACT_READBACK_TOOLS. See that
        # set's own comment for why this needs to survive a compound
        # turn's extra rounds rather than resetting each round.
        exact_readback_only = True

        for round_num in range(MAX_TOOL_ROUNDS):
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
                # plain reply with no tools to tempt it. Only sensible
                # before any tool has actually run; once results exist,
                # showing those beats discarding them for a fresh, toolless
                # generation that no longer knows what it just did.
                if self._looks_like_leaked_tool_syntax(content):
                    if round_num == 0:
                        return self.llm.generate(messages, local_only=self._turn_local_only)
                    return " ".join(all_results)

                # The debris strip llm_client skips on this path (see its
                # debris=False note) happens here instead — after the
                # parser and the leak check have had the raw text.
                content = LLMClient._strip_tool_call_debris(content).strip()

                # The model just said it did something, and nothing ran.
                #
                # Observed 2026-08-06 18:52:29: "File personal/identity.md
                # has been deleted, sir" with no tool_call anywhere in the
                # turn — the file was still there. The cause is upstream:
                # _classify_turn routes each utterance in isolation, so a
                # bare correction ("I meant identity.md") matched the
                # vault-open category and delete_file was never in the
                # menu. Handed no way to act, the model narrated the act
                # instead.
                #
                # Widening the menu once is the fix for both halves: it
                # gives the model the tool the router missed, and it is
                # the only branch that ever shows all ~40 definitions, so
                # the small-model misfire problem that motivated the
                # filtering stays contained to a path that has already
                # produced a falsehood.
                if not all_results and self._claims_completed_action(content):
                    if not widened:
                        widened = True
                        print("[intent] completion claim with no tool run — widening the menu")
                        tool_definitions = self.tools.get_tool_definitions()
                        message = self.llm.generate_with_tools(
                            messages, tools=tool_definitions,
                            local_only=self._turn_local_only,
                        )
                        continue
                    # Widened and it still only talks. Say the true thing:
                    # silence would be better than this reply, and this is
                    # better than silence.
                    return (
                        "I haven't actually done that, sir — nothing ran. "
                        "Say it again and name the file, and I'll run it."
                    )

                # See EXACT_READBACK_TOOLS: for these, the deterministic
                # tool result IS the answer, even after a compound turn's
                # extra round — the model's own paraphrase of a date it
                # already stated correctly one message ago is a second,
                # unnecessary chance to get that date wrong.
                if all_results and exact_readback_only:
                    return " ".join(all_results)

                return content or (
                    " ".join(all_results) if all_results else self.llm.generate(messages, local_only=self._turn_local_only)
                )

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

            round_results = []

            for call in tool_calls:
                result = self._execute_tool_call(call)
                round_results.append(str(result))
                all_results.append(str(result))

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": str(result),
                })

            # Skip the round-trip entirely when every tool called so far
            # already returns a complete spoken sentence and the request
            # wasn't compound — see SELF_NARRATING_TOOLS, and its note on
            # why calculate() is deliberately not in it. A compound turn
            # ("set a reminder and tell me if one exists") instead falls
            # through to the loop asking again below: a local model that
            # forgot the second tool on its first pass gets one more shot
            # at requesting it now that the first result is in context,
            # rather than that half of the request silently vanishing.
            called_names = {c.get("function", {}).get("name") for c in tool_calls}
            exact_readback_only = exact_readback_only and called_names <= EXACT_READBACK_TOOLS

            if (
                called_names
                and called_names <= SELF_NARRATING_TOOLS
                and not intent.looks_compound(last_user)
            ):
                return " ".join(round_results)

            # On a compound turn, restate the original request before
            # asking again. By this point the model is looking at its own
            # tool call and a result, several messages after what was
            # actually asked — nothing in that recent context says a
            # second half exists, so it tends to summarise what it just
            # did and stop. One line naming the goal is what turns "I set
            # the reminder" into "...and I still need to open Spotify".
            # Only on compound turns: on a simple one this would be an
            # invitation to invent extra work.
            if intent.looks_compound(last_user):
                messages.append({
                    "role": "user",
                    "content": (
                        f"That was part of this request: \"{last_user}\". "
                        "If any part of it has not been done yet, call the "
                        "tool for it now. If it is all done, reply normally."
                    ),
                })

            # Ask again, now with these results in context — either for
            # the natural-language reply FRED actually says out loud, or
            # for another tool call if the model realises the turn isn't
            # done yet. The function-calling chat format needs `tools`
            # passed again to correctly render the tool-result history —
            # without it, the model loses track of what it just did and
            # may contradict the action it actually took.
            message = self.llm.generate_with_tools(messages, tools=tool_definitions, local_only=self._turn_local_only)

        # Exhausted the round budget with the model still asking for
        # tools — answer with whatever's actually been done rather than
        # looping forever.
        content = message.get("content") or ""
        if self._looks_like_leaked_tool_syntax(content):
            return " ".join(all_results)
        if all_results and exact_readback_only:
            return " ".join(all_results)
        return LLMClient._strip_tool_call_debris(content).strip() or " ".join(all_results)

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

        # Bare JSON, no wrapper at all: {"name": "x", "arguments": {...}}
        # Qwen emits this when its template's <tool_call> tags don't make
        # it into the output. Confirmed 2026-08-05: "today's tasks?"
        # produced exactly this for list_tasks, nothing here parsed it,
        # and the model then answered "no tasks recorded" for a day with
        # six of them. raw_decode rather than a regex — it stops at the
        # end of the object, so a nested arguments dict is handled and
        # trailing prose after the call is left alone.
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            name = parsed.get("name")
            if name in valid and not any(
                c["function"]["name"] == name for c in calls
            ):
                calls.append({
                    "id": f"text_call_{len(calls)}",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(parsed.get("arguments") or {}),
                    },
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
            # A tool call the parser above couldn't rescue — malformed
            # JSON ({"name": "list_tasks", "arguments": } was spoken
            # aloud on 2026-08-05), or a name that isn't a real tool.
            # Either way it's scaffolding, so regenerate rather than say
            # it.
            or re.search(r'\{\s*"name"\s*:\s*"[\w_]+"', text)
        )

    # Past-tense claims that an action is finished. Deliberately only the
    # verbs that correspond to something a tool DOES — "checked", "looked"
    # and friends are excluded, because a turn can honestly report having
    # consulted context without having acted on the machine.
    _ACTION_DONE = re.compile(
        r"\b(?:deleted|removed|erased|created|moved|renamed|updated|added|"
        r"saved|written|wrote|scheduled|cancell?ed|muted|unmuted|"
        r"launched|installed|uninstalled)\b",
        re.IGNORECASE,
    )

    def _claims_completed_action(self, content: str) -> bool:
        """
        True when a reply asserts it finished something. Only ever
        consulted on a turn the router said needs tools and where no tool
        actually ran, so the bar for a false positive is already high —
        an ordinary conversational turn never reaches this branch.

        A question is not a claim: "Shall I delete it?" and "Proposed
        deletion" are the model asking, which is fine, and the confirm
        path answers them properly on the next turn.
        """

        text = (content or "").strip()

        if not text or text.endswith("?"):
            return False

        # "I will not delete the file" and "I can't delete it" are honest
        # refusals, not claims — check the negation before the verb.
        if re.search(r"\b(?:not|never|won't|will not|can't|cannot|unable)\b", text, re.I):
            return False

        return bool(self._ACTION_DONE.search(text))

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

    # Same shape as on_tool_event above, set by the UI controller — the
    # pill disambiguation chip. Deliberately a SEPARATE hook rather than
    # reusing on_tool_event: that one is spoken through Kokoro (see
    # pill_app._on_tool_event), and adding speech time to every
    # near-tie turn is exactly the "informative but non-blocking" bar
    # this was built not to cross. This one is visual-only.
    on_ambiguous_choice = None

    def _announce_ambiguity(self, top: str, alt: str):
        if not self.on_ambiguous_choice:
            return
        try:
            self.on_ambiguous_choice(
                TOOL_LABELS.get(top, top.replace("_", " ")),
                TOOL_LABELS.get(alt, alt.replace("_", " ")),
            )
        except Exception as e:
            print(f"[orchestrator] ambiguity-event hook failed: {e}")

    def _repeat_last(self) -> str:
        """
        "Say that again" — a pure state lookup, no model call, so it
        still works even if the LLM path is having trouble. Searches
        backwards past the just-added user turn for the most recent
        real assistant message; canned/filler text was never added to
        state in the first place, so nothing further needs excluding.
        """
        for msg in reversed(self.state.get_all_messages()):
            if msg["role"] == "assistant" and msg["content"].strip():
                return msg["content"]
        return "I haven't said anything yet, sir."

    def _summarise_today(self) -> str:
        """Bound so the summariser can use the loaded model for real
        prose instead of falling back to bare counts."""
        return session_summary.preview_session_summary(llm=self.llm)

    def _save_today_summary(self) -> str:
        """Writes to the vault — reached only when the user has said
        yes to the preview, per rules.md's propose-before-write."""
        return session_summary.save_session_summary(llm=self.llm)

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

        # A tool that reads personal/ puts sensitive content into the
        # tool-result message, which the loop then sends BACK to the LLM
        # for phrasing — so the retrieval-side check in _build_messages
        # doesn't cover this path. Latch here too, before the tool runs,
        # so the follow-up round can't reach the cloud cascade.
        if SENSITIVE_LOCAL_ONLY and name in SENSITIVE_TOOLS:
            self._turn_local_only = True

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

        # Cleared before retrieval, never after: this must reflect THIS
        # turn's context only. Leaving it latched would be the safe
        # direction (an unnecessary local turn), but leaving it set from
        # a previous turn would silently pin every later turn to the
        # local model with no way back.
        self._turn_local_only = False

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

        # Today's date, rebuilt every turn. Nothing in SYSTEM_PROMPT, the
        # vault, or the tool menu carried it, so the model dated things
        # from whatever it had just read — confirmed 2026-08-05: asked to
        # log that day's event, it wrote "2026-08-02" into a people/ file
        # and the wrong date is now persisted where it will be believed.
        # Stated rather than made a tool call: a date the model must ask
        # for is a date it can decide it already knows, and at ~15k
        # tokens a round-trip this is the expensive way to learn one line.
        now = datetime.now()
        system_sections.append(
            f"Today is {now.strftime('%A, %d %B %Y')} "
            f"(ISO date {now.strftime('%Y-%m-%d')}), local time "
            f"{now.strftime('%H:%M')}. Use this date for anything you "
            f"write, log or schedule. Never date an entry from a date "
            f"you read in a file — those are past entries, not today."
        )

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
            hits = vault_router.retrieve(
                _retrieval_query(user_input, recent_messages)
            )
            if hits:
                # Sensitivity is decided BEFORE the text is formatted into
                # the prompt, and latches for the whole turn — see
                # utils/sensitive.py. Confirmed 2026-08-04: retrieval runs
                # with VAULT_RETRIEVAL_FLOOR = -1.0, i.e. six chunks come
                # back on EVERY turn regardless of relevance, so personal/
                # and people/ excerpts were reaching the prompt routinely
                # and the cloud cascade was POSTing them to Groq. That is
                # the exact thing rules.md forbids ("no hosted model, no
                # API"), and nothing was enforcing it.
                self._turn_local_only = SENSITIVE_LOCAL_ONLY and sensitive.any_sensitive(
                    [{"source": label, "text": text} for label, text, _ in hits]
                )
                if self._turn_local_only:
                    print("[vault] sensitive content retrieved — local LLM only this turn")

                # Provenance is tagged PER EXCERPT, not once for the turn.
                # A turn-level minimum would hedge a fact Vatsal stated
                # outright just because some unrelated sixth chunk was a
                # guess — and with VAULT_RETRIEVAL_FLOOR = -1.0 forcing
                # six hits every turn regardless of relevance, an
                # irrelevant weak chunk is the normal case, not the
                # exception. See utils/confidence.py.
                # Tables are flattened to "row — Column: value" BEFORE
                # truncation, so a value can never be read out of the
                # wrong column — see utils.vault_md.flatten_tables for
                # the confirmed failure that motivated it.
                vault_text = "\n".join(
                    f"- [{label}] ({confidence.name(level)}) "
                    f"{flat[:VAULT_CHUNK_INJECT_CHARS]}"
                    + ("..." if len(flat) > VAULT_CHUNK_INJECT_CHARS else "")
                    for label, flat, level in (
                        (label, flatten_tables(text), confidence.classify(text))
                        for label, text, _score in hits
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
                    "so rather than substituting a number from another column.\n"
                    "Each excerpt is tagged with how well-sourced it is. "
                    "'stated' and 'confirmed' you may say plainly. 'derived' "
                    "should be attributed to his notes. 'inferred' and "
                    "'speculative' were never said by him — say that you are "
                    "inferring, or ask, rather than asserting them as fact."
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

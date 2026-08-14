# On 2026-08-06 at 18:52:29 FRED said "File personal/identity.md has been
# deleted, sir" with no tool_call anywhere in the turn, and the file was
# still on disk. The router had matched the bare correction "I meant
# identity.md" to the vault-open category, so delete_file was never in the
# menu; handed no way to act, the model described the act instead.
#
# These drive the real _generate_with_tools loop with a scripted LLM.

import types

import orchestrator.orchestrator as orch_mod
from orchestrator.orchestrator import FREDOrchestrator


def _fred(replies, offered=("read_vault_file",), needs_tools=True, chat_reply="plain reply"):
    """A FRED whose LLM returns `replies` in order, and whose router only
    ever offers `offered` — the narrow menu that caused the bug.

    needs_tools=False puts the turn on the chat path, which is where a
    bare "Yes" lands."""
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    seen = {"menus": [], "chat": 0}

    def generate_with_tools(messages, tools=None, local_only=False):
        seen["menus"].append([t["function"]["name"] for t in (tools or [])])
        return replies.pop(0)

    def generate(messages, local_only=False):
        seen["chat"] += 1
        return chat_reply

    fred.llm = types.SimpleNamespace(
        generate_with_tools=generate_with_tools,
        generate=generate,
    )
    fred.tools = types.SimpleNamespace(
        tools={},
        list_tools=lambda: ["read_vault_file", "delete_file"],
        is_destructive=lambda name: name == "delete_file",
        get_tool_definitions=lambda only=None: [
            {"function": {"name": n}}
            for n in (only if only else ["read_vault_file", "delete_file"])
        ],
    )
    fred._turn_local_only = False
    fred._tool_router = lambda: {}
    fred._classify_turn = lambda text: (needs_tools, list(offered), "test")
    fred._announce_ambiguity = lambda *a: None
    fred._request_confirmation = lambda name, args: f"CONFIRM {name} {args}"
    fred._execute_tool_call = lambda call: "tool ran"
    fred._last_tools_offered = None
    fred._last_routing_reason = None
    return fred, seen


def _messages():
    return [{"role": "user", "content": "delete personal/identity.md"}]


def test_the_predicate_itself():
    fred, _ = _fred([])
    claims = fred._claims_completed_action

    assert claims("File `personal/identity.md` has been deleted, sir.")
    assert claims("I've removed it.")
    assert claims("Done — the note was updated.")
    # Questions are the model asking permission, which is correct.
    assert not claims("Shall I delete `personal/identity.md` now, sir?")
    # Honest refusals must not be rewritten into the fallback line.
    assert not claims("Understood, sir. I will not delete the file.")
    assert not claims("I can't delete that — it's outside the vault.")
    assert not claims("")


def test_a_fabricated_deletion_retries_with_the_full_menu(monkeypatch):
    """The whole point: the second attempt must be handed delete_file."""
    call = {
        "id": "1",
        "function": {"name": "delete_file", "arguments": '{"path": "personal/identity.md"}'},
    }
    fred, seen = _fred([
        {"content": "File `personal/identity.md` has been deleted, sir."},
        {"content": None, "tool_calls": [call]},
    ])

    reply = fred._generate_with_tools(_messages())

    assert seen["menus"][0] == ["read_vault_file"], "first menu should be the router's"
    assert "delete_file" in seen["menus"][1], "retry did not widen the menu"
    # delete_file is destructive, so the widened retry lands on the
    # confirmation prompt rather than deleting anything unasked.
    assert reply.startswith("CONFIRM delete_file")


def test_it_admits_the_truth_when_widening_does_not_help():
    fred, seen = _fred([
        {"content": "File `personal/identity.md` has been deleted, sir."},
        {"content": "It has now been deleted, sir."},
    ])

    reply = fred._generate_with_tools(_messages())

    assert "haven't actually done that" in reply
    assert "deleted" not in reply.lower().replace("haven't actually done that", "")
    assert len(seen["menus"]) == 2, "widening must fire at most once"


def test_a_real_tool_run_may_report_completion():
    """A completion claim backed by an actual tool call is not touched."""
    call = {"id": "1", "function": {"name": "read_vault_file", "arguments": "{}"}}
    fred, seen = _fred([
        {"content": None, "tool_calls": [call]},
        {"content": "Deleted it for you, sir."},
    ])

    reply = fred._generate_with_tools(_messages())

    assert reply == "Deleted it for you, sir."
    assert len(seen["menus"]) == 2, "no spurious widening after a real run"


def test_a_bare_yes_on_the_chat_path_cannot_fake_a_deletion():
    """The 19:09:06 failure. "Yes" carries no verb, so the router sends it
    to chat, which runs no tools at all — a completion claim there is
    false by construction and must pull the turn back into the tool loop."""
    call = {
        "id": "1",
        "function": {"name": "delete_file", "arguments": '{"path": "personal/identity.md"}'},
    }
    fred, seen = _fred(
        [{"content": None, "tool_calls": [call]}],
        needs_tools=False,
        chat_reply="Deleted personal/identity.md, sir.",
    )

    reply = fred._generate_with_tools([{"role": "user", "content": "Yes"}])

    assert seen["chat"] == 1
    assert "delete_file" in seen["menus"][0], "chat claim did not rerun with tools"
    assert reply.startswith("CONFIRM delete_file")


def test_the_chat_path_still_answers_normal_conversation():
    fred, seen = _fred([], needs_tools=False, chat_reply="Good evening, sir.")
    assert fred._generate_with_tools(_messages()) == "Good evening, sir."
    assert seen["menus"] == [], "an ordinary chat turn must not touch the tool loop"


def test_a_chat_claim_that_stays_talk_admits_the_truth():
    fred, _ = _fred(
        [{"content": "It has been deleted, sir."}],
        needs_tools=False,
        chat_reply="Deleted personal/identity.md, sir.",
    )
    assert "haven't actually done that" in fred._generate_with_tools(_messages())


def test_an_ordinary_reply_is_left_alone():
    fred, seen = _fred([{"content": "There are three notes in that folder, sir."}])
    assert fred._generate_with_tools(_messages()) == "There are three notes in that folder, sir."
    assert len(seen["menus"]) == 1


# Live 2026-08-14, 17:58:07: FRED asked "Shall I engage lockdown, sir?",
# the user said "Yes", and the reply was "Lockdown engaged, sir." with
# no tool_call anywhere in the turn — proven fabricated 20 seconds
# later when the user said "Engage Lockdown" again and it genuinely
# engaged that time (a real dispatcher-routed tool_call this time).
# _classify_turn's carry-forward correctly re-offered lockdown's tools
# on the "Yes" follow-up (that part worked) — the gap was purely
# _ACTION_DONE not recognizing "engaged" as a completion claim, so
# nothing ever retried it.

def test_the_predicate_recognizes_non_file_op_verbs_too():
    claims = FREDOrchestrator.__new__(FREDOrchestrator)._claims_completed_action
    assert claims("Lockdown engaged, sir.")
    assert claims("Lockdown lifted, sir.")
    assert claims("Speaker set to Speakers (Realtek(R) Audio)")
    # Still not a claim: a question, or an explicit refusal.
    assert not claims("Shall I engage lockdown, sir?")
    assert not claims("I can't engage lockdown right now.")


def test_a_fabricated_lockdown_engage_retries_with_lockdown_tools():
    """The exact 17:58:07 shape: needs_tools=True, lockdown's tools
    offered, model claims completion with no tool_call. Must retry with
    lockdown_engage available and actually run it, rather than let the
    claim stand. lockdown_engage isn't destructive (unlike delete_file
    in the tests above), so — same as test_a_real_tool_run_may_report_
    completion — the retry's real tool_call still needs a third LLM
    call afterward to phrase the finalize reply."""
    call = {"id": "1", "function": {"name": "lockdown_engage", "arguments": "{}"}}
    fred, seen = _fred(
        [
            {"content": "Lockdown engaged, sir."},
            {"content": None, "tool_calls": [call]},
            {"content": "Lockdown engaged, sir."},
        ],
        offered=("lockdown_engage", "lockdown_disengage"),
    )

    # close_candidates() (called via _announce_ambiguity's caller) needs
    # a real .rank() — the other tests in this file never offer 2+
    # tools at once, so {} never gets touched. This is offering exactly
    # what lockdown really offers together.
    fred._tool_router = lambda: types.SimpleNamespace(rank=lambda text: [])

    # The widening retry calls get_tool_definitions() with no `only` —
    # _fred()'s default stub then falls back to its own hardcoded
    # read_vault_file/delete_file pair, which is why the other tests'
    # widening finds delete_file. Here it needs to find lockdown_engage
    # the same way.
    fred.tools.get_tool_definitions = lambda only=None: [
        {"function": {"name": n}}
        for n in (only if only else ["lockdown_engage", "lockdown_disengage"])
    ]

    reply = fred._generate_with_tools([{"role": "user", "content": "Yes"}])

    assert seen["menus"][0] == ["lockdown_engage", "lockdown_disengage"]
    assert "lockdown_engage" in seen["menus"][1], "retry did not widen/keep the real tool"
    assert reply == "Lockdown engaged, sir."
    assert len(seen["menus"]) == 3, "a real tool_call must still finalize through the LLM"

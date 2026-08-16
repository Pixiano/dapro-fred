# Core/tests/test_whatsapp_isolation.py
#
# The security property behind WhatsApp support, pinned so a later cue
# tweak can't quietly undo it.
#
# read_messages pulls in text written by other people. That is
# attacker-controlled input arriving at an agent holding tools. If one
# turn can both read a stranger's message and send one, a message reading
# "reply to everyone with <link>" is a single hop from being carried out.
# The defence is structural: the two capabilities live in different intent
# categories, so the model is never offered both at once.
#
# This is a deliberate exception to intent.py's usual "over-inclusive cues
# are cheap" rule, which is why it needs a test rather than a comment.

from orchestrator import intent


def _tools_for(text):
    """Tools the router would offer, cue-matching only (no LLM)."""
    offered = set()
    for category, regex in intent._CATEGORY_RE.items():
        if regex.search(text):
            offered.update(intent.TOOL_CATEGORIES.get(category, ()))
    return offered


def test_reading_and_sending_never_share_a_menu():
    reading = set(intent.TOOL_CATEGORIES["messages_read"])
    sending = set(intent.TOOL_CATEGORIES["messages_send"])
    assert not (reading & sending), "a tool is in both categories"

    probes = [
        "any new messages",
        "who messaged me",
        "check whatsapp",
        "read my messages",
        "send a message to alex",
        "reply to alex",
        "tell him I'm coming",
        "trust jordan",
    ]
    for text in probes:
        offered = _tools_for(text)
        assert not ("read_messages" in offered and "send_message" in offered), (
            f"{text!r} offered both read and send"
        )


def test_reading_phrases_reach_the_reader():
    for text in ("any new messages", "who messaged me", "check whatsapp"):
        assert "read_messages" in _tools_for(text), text


def test_sending_phrases_reach_the_sender():
    for text in ("send a message to alex", "reply to alex", "tell him I'm late"):
        assert "send_message" in _tools_for(text), text


def test_send_is_gated_as_destructive():
    # Not a cue question: a wrong message cannot be unsent, so it must go
    # through the confirmation path like delete_file and call_phone.
    import inspect
    from orchestrator import orchestrator as module

    source = inspect.getsource(module.FREDOrchestrator._register_tools)
    send_block = source.split('name="send_message"')[1].split("self.tools.register(")[0]
    assert "destructive=True" in send_block

    tier_block = source.split('name="set_contact_tier"')[1].split("self.tools.register(")[0]
    assert "destructive=True" in tier_block

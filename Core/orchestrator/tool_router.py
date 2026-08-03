# Core/orchestrator/tool_router.py
#
# Semantic tool selection: embed every tool once, embed the utterance,
# offer the model only the nearest few.
#
# Why this exists on top of the cue lists in intent.py. Those are literal
# strings, so they only fire on wording someone thought to write down —
# "make the screen less bright" contains no cue at all, falls through to
# the LLM's ACTION/CHAT binary, and a 4B answers that unreliably. Keywords
# can't generalise; embeddings can.
#
# The embedding model is already resident for memory (Qwen3-Embedding-0.6B)
# and is not touched by the idle unloader, so this costs one short
# embed per turn — milliseconds — and no extra VRAM.
#
# Deliberately NOT the model deciding. Asking the 4B to pick a category
# reintroduces exactly the judgement call being removed. Cosine similarity
# is arithmetic: same input, same answer, every time.

import math

# Extra phrasings per tool, on top of its registered description. The
# description is written for the model to read once it's already been
# offered the tool; these are written the way a person actually asks,
# which is what the utterance gets compared against. Colloquial and
# awkward variants earn their place here more than tidy ones do.
TOOL_EXAMPLES = {
    "get_current_time": ["what time is it", "what day is it today", "what's the date"],
    "get_weather": ["what's the weather like", "is it going to rain", "how hot is it outside"],
    "web_search": ["look this up online", "search the web for", "what's in the news"],
    "calculate": ["what's 17 percent of 300", "add these numbers up", "how much is 45 times 3"],
    "get_system_status": ["how much battery do I have", "is my disk full", "how much RAM is being used"],
    "get_network_status": ["am I online", "what wifi am I on", "is the internet working"],
    "launch_application": ["open spotify", "start vs code", "fire up the browser"],
    "open_website": ["go to youtube", "open google dot com", "take me to that website"],
    "open_path": ["open that file", "show me this folder", "open my downloads folder"],
    "get_volume": ["how loud is it", "what's the volume at", "is it muted"],
    "set_volume": ["turn it up", "make it quieter", "set volume to forty percent"],
    "mute": ["mute the sound", "silence it", "unmute"],
    "media_control": ["skip this song", "pause the music", "play the next track", "resume playback"],
    "get_brightness": ["how bright is the screen", "what's my brightness"],
    "set_brightness": ["make the screen less bright", "dim the display", "brighten my screen"],
    "take_screenshot": ["grab a picture of my screen", "capture the screen", "screenshot this"],
    "get_clipboard": ["what did I copy", "what's on my clipboard"],
    "set_clipboard": ["copy this text", "put that on my clipboard"],
    "list_windows": ["what have I got open", "show my open windows", "what programs are running on screen"],
    "focus_window": ["switch to chrome", "bring up my editor", "go to that window"],
    "minimize_window": ["minimise this", "get this out of the way"],
    "maximize_window": ["make this fullscreen", "maximise the window"],
    "close_window": ["close this window", "shut this down"],
    "list_processes": ["what's using my cpu", "show running processes", "what's eating memory"],
    "kill_process": [
        "chrome is frozen kill it", "force quit that app", "end that task",
        "this app is not responding", "it has stopped responding close it",
    ],
    "power_action": ["lock my pc", "put the computer to sleep", "shut down", "restart the machine"],
    "create_text_file": ["make me a new text file", "create a document called notes"],
    "create_folder": ["make a new folder", "create a directory for this"],
    "append_to_file": ["add milk to my shopping list", "jot this down in my notes", "add a line to that file"],
    "read_file": ["read me that file", "what's in this document"],
    "list_directory": ["what's in my documents", "show me the files in that folder"],
    "search_files": [
        "find my homework file", "where did I save that document",
        "where did I put my essay", "locate that file on my pc",
        "which folder is that document in",
    ],
    "move_file": ["move that file to downloads", "put this somewhere else"],
    "rename_file": ["rename this file", "call that something else"],
    "delete_file": ["delete that file", "get rid of this document"],
    "schedule_reminder": ["remind me to call mum at seven", "remind me tomorrow morning"],
    "set_timer": ["set a timer for ten minutes", "start a countdown"],
    "schedule_file_watch": ["tell me when that file appears", "watch for this download"],
    "list_scheduled": ["what reminders do I have", "what's pending"],
    "cancel_scheduled": ["cancel that reminder", "forget the timer"],
}


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SemanticToolRouter:
    """
    Nearest-neighbour tool lookup over embedded descriptions.

    build() is lazy and safe to call repeatedly. If the embedder is
    unavailable or fails, every method degrades to "no opinion" and the
    caller falls back to cue matching — this is an accuracy improvement,
    never a dependency.
    """

    def __init__(self, embed_fn, descriptions: dict):
        self.embed = embed_fn
        self.descriptions = descriptions
        self._vectors = {}
        self._ready = False
        self._failed = False

    def build(self) -> bool:
        if self._ready or self._failed:
            return self._ready

        try:
            for name, description in self.descriptions.items():
                examples = TOOL_EXAMPLES.get(name, [])
                # One vector per tool from description + example phrasings.
                # Combining rather than embedding each example separately
                # keeps this to 40 embeds at startup instead of ~160.
                text = f"{name.replace('_', ' ')}. {description} " + " ".join(examples)
                self._vectors[name] = self.embed(text)
        except Exception as e:
            print(f"[tool_router] embedding build failed ({e}) — cues only")
            self._failed = True
            self._vectors = {}
            return False

        self._ready = True
        print(f"[tool_router] embedded {len(self._vectors)} tools")
        return True

    def rank(self, text: str):
        """[(tool_name, similarity)] best first, or [] if unavailable."""
        if not self.build():
            return []
        try:
            # is_query=True — asymmetric instruction convention, see
            # memory_manager.py's _generate_embedding. The tool
            # descriptions embedded in build() are documents; this is
            # the query being matched against them.
            query = self.embed(text, is_query=True)
        except Exception as e:
            print(f"[tool_router] embed failed ({e})")
            return []

        scored = [(name, _cosine(query, vec)) for name, vec in self._vectors.items()]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def route(self, text: str, top_k: int = 5, floor: float = 0.0, margin: float = 0.06):
        """
        Returns (names, best_score).

        Takes the top match plus anything within `margin` of it, capped at
        top_k. Keeping near-ties matters: "turn it up" is genuinely close
        between set_volume and set_brightness, and offering both lets the
        model disambiguate from context rather than forcing this layer to
        guess. `floor` rejects everything when nothing is close enough,
        which is the signal that a turn is conversation.
        """
        scored = self.rank(text)
        if not scored:
            return [], 0.0

        best = scored[0][1]
        if best < floor:
            return [], best

        keep = [name for name, score in scored[:top_k] if score >= best - margin]
        return keep, best

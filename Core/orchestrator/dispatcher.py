# Core/orchestrator/dispatcher.py

import re


class Dispatcher:
    """
    F.R.E.D.'s brainstem — decides, before any LLM is touched,
    whether a message is an obvious tool call/OS command FRED can
    just do, or whether it actually needs the LLM (general
    conversation, or a live lookup once Phase 13's web tools exist).

    Deterministic and instant: regex match -> direct tool execution,
    no model inference involved at all. This is what reserves the
    LLM's tiers (especially "Deep") for things that actually need
    reasoning, instead of burning a model call on "open Spotify."

    Add new deterministic routes here as new tools come online
    (e.g. Phase 13's websearch_handler / weather lookup).
    """

    def __init__(self):

        # Order matters — more specific patterns first.
        self._rules = [
            (
                # "Say that again" needs no model call at all — it's a
                # pure state lookup (the last thing FRED said), and
                # routing it here means it works even mid-outage on the
                # LLM side, same as get_current_time.
                re.compile(
                    r"^(?:say (?:that|it) again|repeat that|repeat yourself|"
                    r"what did you say|come again|say again|"
                    r"can you repeat that)\??$",
                    re.IGNORECASE,
                ),
                self._route_repeat_last,
            ),
            (
                # "open it" / "open that one" / "open the second one" —
                # a follow-up to a search whose spoken result carried no
                # filename to repeat back. Must sit above the generic
                # open-a-website and open-an-app rules, both of which
                # would otherwise swallow the pronoun and try to launch
                # an application literally called "it".
                re.compile(
                    r"^open\s+(?:up\s+)?(?:the\s+)?"
                    r"(?:(?P<ordinal>first|second|third|1st|2nd|3rd)\s+)?"
                    r"(?:it|that|that one|one|file|result)$",
                    re.IGNORECASE,
                ),
                self._route_open_last_found,
            ),
            (
                # Vault files first, before the website/app rules below —
                # a vault name is never a valid app or website, so trying
                # it here costs nothing when it doesn't match. Confirmed
                # bug (session_2026-08-03.jsonl): "open up active
                # priorities for me." and "open up the
                # activepriorities.md please." both fell through to the
                # generic launch_application catch-all below (they don't
                # match the strict single-token website/file pattern,
                # having spaces and filler words), which then tried to
                # launch an "app" literally named "up active priorities
                # for me." Declines (returns None) when nothing in the
                # vault matches, so non-vault "open X" phrasing is
                # completely unaffected.
                re.compile(
                    r"^(?:open|launch|start|pull up|bring up)\s+(?:up\s+)?(?:the\s+)?"
                    r"(?:file\s+(?:called|named)\s+)?(?P<target>.+?)"
                    r"(?:\s+(?:please|for me))?\.?$",
                    re.IGNORECASE,
                ),
                self._route_open_vault_file,
            ),
            (
                re.compile(
                    r"^(?:open|launch|start)\s+(?P<target>https?://\S+|\S+\.\S+)$",
                    re.IGNORECASE,
                ),
                self._route_open_website,
            ),
            (
                re.compile(
                    r"^(?:open|launch|start)\s+(?P<target>.+)$",
                    re.IGNORECASE,
                ),
                self._route_launch_application,
            ),
            (
                re.compile(
                    r"^what(?:'s| is) the time|^what time is it",
                    re.IGNORECASE,
                ),
                self._route_get_time,
            ),
            (
                # A word-problem trigger ("calculate", "what is", "how
                # much is"...) followed by a chunk that actually contains
                # both a digit and an operator — the operator requirement
                # is what stops this from swallowing "what is the capital
                # of France", which has neither.
                re.compile(
                    r"^(?:calculate|compute|work out|solve|"
                    r"what(?:'s| is)|how much is)\s+"
                    r"(?P<expr>(?=.*\d)(?=.*(?:[+\-*/^%]|plus|minus|times|"
                    r"multiplied|divided|percent|sqrt|square root)).+?)\??$",
                    re.IGNORECASE,
                ),
                self._route_calculate,
            ),
            (
                # Bare arithmetic with no question prefix at all —
                # "12 times 8", "17% of 300" — a leading number followed
                # by an operator, spoken or symbolic.
                re.compile(
                    r"^-?\d+(?:\.\d+)?\s*(?:[+\-*/^%]|plus|minus|times|"
                    r"multiplied by|divided by|percent of)\s+.+$",
                    re.IGNORECASE,
                ),
                self._route_calculate_bare,
            ),
            (
                re.compile(
                    r"^create (?:a |an )?folder (?:named |called )?(?P<target>.+)$",
                    re.IGNORECASE,
                ),
                self._route_create_folder,
            ),
            (
                re.compile(
                    r"^create (?:a |an )?(?:text )?file (?:named |called )?(?P<target>.+)$",
                    re.IGNORECASE,
                ),
                self._route_create_text_file,
            ),
            (
                re.compile(
                    r"^(?:what(?:'s| is) the )?weather(?: (?:in|for|at) (?P<target>.+))?\??$",
                    re.IGNORECASE,
                ),
                self._route_get_weather,
            ),
            (
                re.compile(
                    r"^(?:search|google|look up)(?: the web)?(?: for)? (?P<target>.+)$",
                    re.IGNORECASE,
                ),
                self._route_web_search,
            ),
            (
                re.compile(r"^mute$", re.IGNORECASE),
                self._route_mute,
            ),
            (
                re.compile(r"^unmute$", re.IGNORECASE),
                self._route_unmute,
            ),
            (
                re.compile(
                    r"^set volume(?: to)? (?P<level>\d+)%?$", re.IGNORECASE
                ),
                self._route_set_volume,
            ),
            (
                # Relative moves — "turn it up a bit", "volume down",
                # "louder", "a lot quieter". Deterministic because the
                # phrasing is closed and the action is trivial; leaving
                # it to the model meant it had to invent an absolute
                # number for a request that never named one.
                re.compile(
                    r"^(?:(?:turn|bump)\s+(?:the\s+)?(?:volume|sound|it)?\s*"
                    r"(?P<dir1>up|down)|"
                    r"(?:volume|sound)\s+(?P<dir2>up|down)|"
                    r"(?P<word>louder|quieter|softer))"
                    r"(?:\s+(?P<amount>a bit|a little|slightly|a lot|way more|much))?$",
                    re.IGNORECASE,
                ),
                self._route_adjust_volume,
            ),
            (
                re.compile(
                    r"^(?:(?:turn|bump)\s+(?:the\s+)?(?:brightness|screen)\s*"
                    r"(?P<dir1>up|down)|"
                    r"(?:brightness|screen)\s+(?P<dir2>up|down)|"
                    r"(?P<word>brighter|dimmer|darker))"
                    r"(?:\s+(?P<amount>a bit|a little|slightly|a lot|way more|much))?$",
                    re.IGNORECASE,
                ),
                self._route_adjust_brightness,
            ),
            (
                re.compile(
                    r"^set brightness(?: to)? (?P<level>\d+)%?$", re.IGNORECASE
                ),
                self._route_set_brightness,
            ),
            (
                re.compile(r"^(?:take a |take )?screenshot$", re.IGNORECASE),
                self._route_screenshot,
            ),
            (
                re.compile(
                    r"^remind me in (?P<minutes>\d+)\s*(?:min(?:ute)?s?) (?:to )?(?P<message>.+)$",
                    re.IGNORECASE,
                ),
                self._route_reminder_minutes_first,
            ),
            (
                re.compile(
                    r"^remind me (?:to )?(?P<message>.+?) in (?P<minutes>\d+)\s*(?:min(?:ute)?s?)$",
                    re.IGNORECASE,
                ),
                self._route_reminder_message_first,
            ),
            (
                re.compile(
                    r"^set a timer for (?P<minutes>\d+)\s*(?:min(?:ute)?s?)$",
                    re.IGNORECASE,
                ),
                self._route_timer,
            ),
            (
                re.compile(
                    r"^set a (?P<minutes>\d+)\s*(?:min(?:ute)?s?) timer(?: for (?P<message>.+))?$",
                    re.IGNORECASE,
                ),
                self._route_timer_minutes_first,
            ),
            (
                re.compile(
                    r"^(?:tell me|let me know) when (?P<target>.+?) (?:shows up|appears|exists)$",
                    re.IGNORECASE,
                ),
                self._route_file_watch,
            ),
            (
                re.compile(
                    r"^(?:list|show)(?: my)? (?:reminders|timers)$|"
                    r"^what reminders do i have\??$",
                    re.IGNORECASE,
                ),
                self._route_list_scheduled,
            ),
            (
                re.compile(
                    r"^cancel (?:the |my )?(?:reminder|timer)(?: for)? (?P<target>.+)$",
                    re.IGNORECASE,
                ),
                self._route_cancel_scheduled,
            ),
            (
                re.compile(
                    r"^cancel (?:all (?:reminders|timers)|everything)$",
                    re.IGNORECASE,
                ),
                self._route_cancel_all_scheduled,
            ),
            # Destructive — routed here deterministically so the
            # confirmation gate ALWAYS fires, instead of relying on a
            # small model to choose to call kill_process/close_window
            # (which it may instead just claim it did, in plain text).
            (
                re.compile(
                    r"^(?:kill|terminate|end)\s+(?P<target>.+?)(?:\s+process)?$",
                    re.IGNORECASE,
                ),
                self._route_kill_process,
            ),
            (
                re.compile(
                    r"^close\s+(?P<target>.+?)(?:\s+window)?$",
                    re.IGNORECASE,
                ),
                self._route_close_window,
            ),
        ]

    def match(self, user_input: str) -> dict | None:
        """
        Returns {"tool": name, "arguments": {...}} for an obvious,
        unambiguous command, or None if this needs real reasoning
        and should go to the LLM pipeline instead.

        A handler may itself return None to decline a match it's
        regex-eligible for but can't responsibly handle (see
        _route_web_search) — matching continues to the remaining
        rules rather than stopping there, same as no rule matching
        at all.
        """

        text = user_input.strip()

        for pattern, handler in self._rules:
            found = pattern.match(text)

            if found:
                result = handler(found)
                if result is not None:
                    return result

        return None

    # =========================================================
    # ROUTES
    # =========================================================

    # Suffixes that mean "this is a file on disk", not a hostname. The
    # open-a-website pattern accepts any <something>.<something>, which
    # a filename satisfies just as well as a domain does — confirmed
    # 2026-08-02: "Open dossier.pdf", said right after FRED had found
    # dossier.pdf on the Desktop, opened a browser at
    # https://dossier.pdf instead of the actual PDF.
    #
    # Checking file extensions rather than valid TLDs is deliberate:
    # the TLD list is enormous and grows, while the set of extensions
    # someone asks FRED to open by voice is small and stable. Anything
    # not listed still goes to the web, which keeps "open youtube.com"
    # and any new TLD working without maintenance here.
    _FILE_SUFFIXES = {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md",
        "csv", "json", "xml", "log", "rtf", "odt", "epub",
        "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico",
        "mp3", "mp4", "wav", "avi", "mkv", "mov", "flac", "m4a", "webm",
        "zip", "rar", "7z", "tar", "gz", "iso",
        "py", "js", "ts", "html", "css", "java", "cpp", "c", "rs", "go",
        "exe", "msi", "bat", "ps1", "lnk", "dll",
    }

    _ORDINALS = {
        "first": 1, "1st": 1,
        "second": 2, "2nd": 2,
        "third": 3, "3rd": 3,
    }

    @staticmethod
    def _route_open_vault_file(match: re.Match) -> dict | None:

        target = match.group("target").strip()
        if not target:
            return None

        from tools.vault_files import resolve_vault_file

        if resolve_vault_file(target) is None:
            return None

        return {"tool": "open_vault_file", "arguments": {"name": target}}

    @classmethod
    def _route_open_last_found(cls, match: re.Match) -> dict:

        ordinal = (match.group("ordinal") or "").lower()

        return {
            "tool": "open_last_found",
            "arguments": {"which": cls._ORDINALS.get(ordinal, 1)},
        }

    @classmethod
    def _route_open_website(cls, match: re.Match) -> dict:

        target = match.group("target")

        # An explicit scheme is unambiguous — always the web.
        if target.startswith(("http://", "https://")):
            return {"tool": "open_website", "arguments": {"url": target}}

        suffix = target.rsplit(".", 1)[-1].lower() if "." in target else ""
        if suffix in cls._FILE_SUFFIXES:
            # open_path handles files and folders with the shell's own
            # default program, which is what "open dossier.pdf" means.
            return {"tool": "open_path", "arguments": {"path": target}}

        return {"tool": "open_website", "arguments": {"url": f"https://{target}"}}

    @staticmethod
    def _route_launch_application(match: re.Match) -> dict:

        return {
            "tool": "launch_application",
            "arguments": {"app_name": match.group("target").strip()},
        }

    @staticmethod
    def _route_repeat_last(match: re.Match) -> dict:

        return {"tool": "repeat_last", "arguments": {}}

    @staticmethod
    def _route_get_time(match: re.Match) -> dict:

        return {"tool": "get_current_time", "arguments": {}}

    @staticmethod
    def _route_calculate(match: re.Match) -> dict:

        return {"tool": "calculate", "arguments": {"expression": match.group("expr").strip()}}

    @staticmethod
    def _route_calculate_bare(match: re.Match) -> dict:

        return {"tool": "calculate", "arguments": {"expression": match.group(0).strip()}}

    @staticmethod
    def _route_create_folder(match: re.Match) -> dict:

        return {
            "tool": "create_folder",
            "arguments": {"folder_name": match.group("target").strip()},
        }

    @staticmethod
    def _route_create_text_file(match: re.Match) -> dict:

        return {
            "tool": "create_text_file",
            "arguments": {"filename": match.group("target").strip()},
        }

    @staticmethod
    def _route_get_weather(match: re.Match) -> dict:

        location = match.group("target") or ""

        return {"tool": "get_weather", "arguments": {"location": location.strip()}}

    # Words that mean the query only makes sense with prior turns in
    # view — a bare regex has no conversation to resolve them against.
    _PRONOUN_LEAD = {
        "it", "that", "this", "them", "him", "her", "they", "those",
        "these",
    }

    # "Search" said about the local machine is a file search, not a web
    # search. Confirmed misroute: "Search my desktop for dossier.pdf"
    # dispatched straight to web_search and read out results about
    # moving Windows folders and free PDF editors. These cues mean the
    # target is local, so the turn belongs on the LLM tool path where
    # search_files/find_file_smart are actually reachable.
    _LOCAL_SEARCH_CUES = re.compile(
        r"\b(?:my |the )?(?:desktop|downloads?|documents?|folder|directory|"
        r"drive|pc|computer|laptop|machine|vault|files?)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _route_web_search(cls, match: re.Match) -> dict | None:

        target = match.group("target").strip()

        # Confirmed bug: "Search it on the web. It has been released."
        # (following a turn about Opus 5 pricing) dispatched here
        # deterministically with query="it on the web. It has been
        # released." — nothing upstream ever saw the actual topic,
        # because this route runs before the LLM/conversation-history
        # pipeline even starts. Declining here (returning None) sends
        # it through Dispatcher.match()'s fallthrough to the LLM tool
        # path instead, which has the last several turns in context
        # and can resolve "it" to what was actually being discussed.
        first_word = re.sub(r"[^\w]", "", target.split()[0].lower()) if target else ""
        if first_word in cls._PRONOUN_LEAD:
            return None

        # Local-machine search, not a web search — see _LOCAL_SEARCH_CUES.
        if cls._LOCAL_SEARCH_CUES.search(target):
            return None

        return {
            "tool": "web_search",
            "arguments": {"query": target},
        }

    @staticmethod
    def _route_mute(match: re.Match) -> dict:

        return {"tool": "mute", "arguments": {"should_mute": True}}

    @staticmethod
    def _route_unmute(match: re.Match) -> dict:

        return {"tool": "mute", "arguments": {"should_mute": False}}

    @staticmethod
    def _route_set_volume(match: re.Match) -> dict:

        return {"tool": "set_volume", "arguments": {"level": int(match.group("level"))}}

    # Spoken hedges -> the three step sizes adjust_volume understands.
    _AMOUNTS = {
        "a bit": "small", "a little": "small", "slightly": "small",
        "a lot": "large", "way more": "large", "much": "large",
    }
    # Bare comparatives that carry their own direction.
    _COMPARATIVE_DIR = {
        "louder": "up", "quieter": "down", "softer": "down",
        "brighter": "up", "dimmer": "down", "darker": "down",
    }

    @classmethod
    def _relative_args(cls, match: re.Match) -> dict:
        word = (match.groupdict().get("word") or "").lower()
        direction = (
            match.groupdict().get("dir1")
            or match.groupdict().get("dir2")
            or cls._COMPARATIVE_DIR.get(word, "up")
        )
        amount = cls._AMOUNTS.get((match.groupdict().get("amount") or "").lower(), "normal")
        return {"direction": direction.lower(), "amount": amount}

    @classmethod
    def _route_adjust_volume(cls, match: re.Match) -> dict:

        return {"tool": "adjust_volume", "arguments": cls._relative_args(match)}

    @classmethod
    def _route_adjust_brightness(cls, match: re.Match) -> dict:

        return {"tool": "adjust_brightness", "arguments": cls._relative_args(match)}

    @staticmethod
    def _route_set_brightness(match: re.Match) -> dict:

        return {"tool": "set_brightness", "arguments": {"level": int(match.group("level"))}}

    @staticmethod
    def _route_screenshot(match: re.Match) -> dict:

        return {"tool": "take_screenshot", "arguments": {}}

    @staticmethod
    def _route_kill_process(match: re.Match) -> dict:

        return {
            "tool": "kill_process",
            "arguments": {"name_or_pid": match.group("target").strip()},
        }

    @staticmethod
    def _route_close_window(match: re.Match) -> dict:

        return {
            "tool": "close_window",
            "arguments": {"title": match.group("target").strip()},
        }

    @staticmethod
    def _route_reminder_minutes_first(match: re.Match) -> dict:

        return {
            "tool": "schedule_reminder",
            "arguments": {
                "message": match.group("message").strip(),
                "minutes": int(match.group("minutes")),
            },
        }

    @staticmethod
    def _route_reminder_message_first(match: re.Match) -> dict:

        return {
            "tool": "schedule_reminder",
            "arguments": {
                "message": match.group("message").strip(),
                "minutes": int(match.group("minutes")),
            },
        }

    @staticmethod
    def _route_timer(match: re.Match) -> dict:

        minutes = match.group("minutes")

        return {
            "tool": "schedule_reminder",
            "arguments": {
                "message": f"Your {minutes}-minute timer is up.",
                "minutes": int(minutes),
            },
        }

    @staticmethod
    def _route_timer_minutes_first(match: re.Match) -> dict:

        minutes = match.group("minutes")
        topic = match.group("message")

        message = f"Timer for {topic.strip()}." if topic else f"Your {minutes}-minute timer is up."

        return {
            "tool": "schedule_reminder",
            "arguments": {"message": message, "minutes": int(minutes)},
        }

    @staticmethod
    def _route_file_watch(match: re.Match) -> dict:

        return {
            "tool": "schedule_file_watch",
            "arguments": {"path": match.group("target").strip()},
        }

    @staticmethod
    def _route_list_scheduled(match: re.Match) -> dict:

        return {"tool": "list_scheduled", "arguments": {}}

    @staticmethod
    def _route_cancel_scheduled(match: re.Match) -> dict:

        return {
            "tool": "cancel_scheduled",
            "arguments": {"identifier": match.group("target").strip()},
        }

    @staticmethod
    def _route_cancel_all_scheduled(match: re.Match) -> dict:

        return {"tool": "cancel_scheduled", "arguments": {"identifier": "all"}}

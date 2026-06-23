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
    LLM's tiers (especially "deep") for things that actually need
    reasoning, instead of burning a model call on "open Spotify."

    Add new deterministic routes here as new tools come online
    (e.g. Phase 13's websearch_handler / weather lookup).
    """

    def __init__(self):

        # Order matters — more specific patterns first.
        self._rules = [
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
        ]

    def match(self, user_input: str) -> dict | None:
        """
        Returns {"tool": name, "arguments": {...}} for an obvious,
        unambiguous command, or None if this needs real reasoning
        and should go to the LLM pipeline instead.
        """

        text = user_input.strip()

        for pattern, handler in self._rules:
            found = pattern.match(text)

            if found:
                return handler(found)

        return None

    # =========================================================
    # ROUTES
    # =========================================================

    @staticmethod
    def _route_open_website(match: re.Match) -> dict:

        target = match.group("target")

        if not target.startswith(("http://", "https://")):
            target = f"https://{target}"

        return {"tool": "open_website", "arguments": {"url": target}}

    @staticmethod
    def _route_launch_application(match: re.Match) -> dict:

        return {
            "tool": "launch_application",
            "arguments": {"app_name": match.group("target").strip()},
        }

    @staticmethod
    def _route_get_time(match: re.Match) -> dict:

        return {"tool": "get_current_time", "arguments": {}}

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

    @staticmethod
    def _route_web_search(match: re.Match) -> dict:

        return {
            "tool": "web_search",
            "arguments": {"query": match.group("target").strip()},
        }

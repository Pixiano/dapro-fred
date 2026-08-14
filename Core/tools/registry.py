# Core/tools/registry.py

from state import lockdown_state


class ToolRegistry:
    """
    Central registry for all executable tools in F.R.E.D.

    Responsibilities:
    - Register tools
    - Execute tools
    - Provide tool schemas to the LLM
    - Maintain clean tool abstraction
    """

    def __init__(self):

        self.tools = {}

    # =========================================================
    # REGISTER TOOL
    # =========================================================

    def register(
        self,
        name: str,
        function,
        description: str,
        parameters: dict,
        destructive: bool = False
    ):
        """
        Register a tool.

        destructive: tools that can't be undone (deleting files,
        killing processes, closing windows with unsaved work) — the
        orchestrator must confirm with the user before running these.
        """

        self.tools[name] = {
            "function": function,
            "description": description,
            "parameters": parameters,
            "destructive": destructive,
        }

    # =========================================================
    # EXECUTE TOOL
    # =========================================================

    def execute(
        self,
        tool_name: str,
        **kwargs
    ):
        """
        Execute registered tool.
        """

        if tool_name not in self.tools:
            raise ValueError(
                f"Tool '{tool_name}' not found."
            )

        # Single choke point every tool call passes through — this is
        # where lockdown mode actually refuses things, rather than a
        # guard duplicated in every tool. "lockdown" itself is always
        # exempt, or there'd be no way to ever say "unlock" again.
        if tool_name != "lockdown" and lockdown_state.is_locked():
            return "FRED is in lockdown mode, sir — say 'unlock' to restore access."

        tool = self.tools[tool_name]

        return tool["function"](**kwargs)

    # =========================================================
    # GET TOOL SCHEMAS
    # =========================================================

    def get_tool_definitions(self, only=None) -> list:
        """
        Convert tools into LLM-compatible schemas.

        `only` restricts the result to the named tools, preserving their
        order, and is how the intent router keeps a turn's menu short —
        showing a small model all 40 definitions is what made it pick a
        random one. Unknown names are ignored, and an empty result falls
        back to every tool rather than none, so a bad subset degrades to
        the old behaviour instead of disabling tools entirely.
        """

        definitions = []

        if only:
            wanted = [name for name in only if name in self.tools]
            items = [(name, self.tools[name]) for name in wanted]
        else:
            items = list(self.tools.items())

        if not items:
            items = list(self.tools.items())

        for name, tool in items:

            definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                }
            })

        return definitions

    # =========================================================
    # LIST TOOLS
    # =========================================================

    def list_tools(self) -> list:
        """
        Return registered tool names.
        """

        return list(self.tools.keys())

    # =========================================================
    # DESTRUCTIVE CHECK
    # =========================================================

    def is_destructive(self, tool_name: str) -> bool:
        """
        Whether a tool needs user confirmation before running.
        """

        tool = self.tools.get(tool_name)

        return bool(tool and tool["destructive"])
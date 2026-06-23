# Core/tools/registry.py

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
        parameters: dict
    ):
        """
        Register a tool.
        """

        self.tools[name] = {
            "function": function,
            "description": description,
            "parameters": parameters
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

        tool = self.tools[tool_name]

        return tool["function"](**kwargs)

    # =========================================================
    # GET TOOL SCHEMAS
    # =========================================================

    def get_tool_definitions(self) -> list:
        """
        Convert tools into LLM-compatible schemas.
        """

        definitions = []

        for name, tool in self.tools.items():

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
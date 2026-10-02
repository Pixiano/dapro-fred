"""One-off: dump FRED's registered tool names+descriptions (code, not private) to data/tools.json."""
import json, sys
from pathlib import Path
from unittest.mock import MagicMock
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Core"))
from orchestrator.orchestrator import FREDOrchestrator
from tools.registry import ToolRegistry
o = FREDOrchestrator.__new__(FREDOrchestrator)
o.tools = ToolRegistry()
o.scheduler = o.llm = o.memory = MagicMock()
o._register_tools()
d = {n: o.tools.tools[n]["description"] for n in o.tools.list_tools()}
(Path(__file__).parent / "data" / "tools.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
print(len(d), "tools")

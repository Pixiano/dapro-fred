# Core/tests/conftest.py
#
# Every module in Core/ imports with absolute paths ("from tools import
# ...", "from config.settings import ...") assuming Core/ itself is on
# sys.path — true when FRED is launched normally (cwd=Core), not true
# by default when pytest collects from Core/tests/. Inserting it once
# here is simpler than rewriting every test's imports.

import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

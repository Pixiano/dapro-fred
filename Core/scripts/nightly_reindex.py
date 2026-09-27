# Core/scripts/nightly_reindex.py
#
# Entry point for a Windows Scheduled Task (FRED_NightlyReindex, daily
# 03:00) that rebuilds the file index over the whole VatsalDaPro tree,
# not just the home-folder default reindex_drive() uses when called from
# conversation. Closes file_index.py's one real staleness gap: changes
# made outside FRED (File Explorer, another app) never update the index
# until something explicitly reindexes.

import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from tools.file_index import reindex_drive

TARGET = r"C:\Users\Dhiraj Vatsal\VatsalDaPro"

if __name__ == "__main__":
    print(reindex_drive(TARGET))

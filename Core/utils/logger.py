# Core/utils/logger.py

from datetime import datetime
from pathlib import Path

from config.settings import LOG_DIR


class FREDLogger:
    """
    Lightweight logging system for F.R.E.D.

    Responsibilities:
    - Runtime logging
    - Error logging
    - Event tracing
    - Persistent log storage
    """

    def __init__(self):

        self.log_file = (
            LOG_DIR /
            f"fred_{datetime.now().strftime('%Y%m%d')}.log"
        )

    # =========================================================
    # INTERNAL WRITER
    # =========================================================

    def _write(
        self,
        level: str,
        message: str
    ):

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        line = (
            f"[{timestamp}] "
            f"[{level.upper()}] "
            f"{message}\n"
        )

        # Console output
        print(line.strip())

        # File output
        with open(
            self.log_file,
            "a",
            encoding="utf-8"
        ) as f:

            f.write(line)

    # =========================================================
    # LOG LEVELS
    # =========================================================

    def info(self, message: str):
        self._write("INFO", message)

    def warning(self, message: str):
        self._write("WARNING", message)

    def error(self, message: str):
        self._write("ERROR", message)

    def debug(self, message: str):
        self._write("DEBUG", message)
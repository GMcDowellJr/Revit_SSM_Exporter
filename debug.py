# debug.py
# Phase 1, Sprint 2: MOVE ONLY (logging utilities)
#
# Extracted Logger class for timestamped logging with optional console output.

"""
Logging utilities for SSM Exporter.

Provides a simple Logger class that:
- Timestamps all messages
- Accumulates log lines in memory
- Optionally prints to console
- Supports info/warn/error levels
"""

import datetime


class Logger(object):
    """
    Simple logger for SSM Exporter.

    Accumulates log messages with timestamps and optional console output.
    All messages are stored in memory (self.lines) for later export.
    """

    def __init__(self, enabled=True):
        """
        Initialize logger.

        Args:
            enabled (bool): If True, print messages to console. If False, only store in memory.
        """
        self.enabled = enabled
        self.lines = []

    def _write(self, level, msg):
        """Internal method to write formatted log messages."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = "[{0}] {1}: {2}".format(ts, level, msg)
        self.lines.append(line)
        if self.enabled:
            try:
                print(line)
            except Exception:
                pass

    def info(self, msg):
        """Log an info-level message."""
        self._write("INFO", msg)

    def warn(self, msg):
        """Log a warning-level message."""
        self._write("WARN", msg)

    def error(self, msg):
        """Log an error-level message."""
        self._write("ERROR", msg)

    def dump(self):
        """Return a copy of all accumulated log lines."""
        return list(self.lines)

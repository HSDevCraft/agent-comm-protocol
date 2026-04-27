"""
Logging compatibility shim.
Uses structlog if available, otherwise falls back to stdlib logging.
Import `get_logger` from here instead of importing structlog directly.
"""
from __future__ import annotations

import logging
import sys
from typing import Any


try:
    import structlog as _structlog

    _structlog.configure(
        wrapper_class=_structlog.make_filtering_bound_logger(logging.WARNING),
    )

    def get_logger(name: str = "") -> Any:
        return _structlog.get_logger(name)

except ImportError:
    _root = logging.getLogger("agent_comm_protocol")
    if not _root.handlers:
        _handler = logging.StreamHandler(sys.stderr)
        _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
        _root.addHandler(_handler)
    _root.setLevel(logging.WARNING)
    _root.propagate = False

    class _StdlibBoundLogger:
        def __init__(self, name: str) -> None:
            self._log = logging.getLogger(f"agent_comm_protocol.{name}" if name else "agent_comm_protocol")

        def _fmt(self, event: str, **kw: Any) -> str:
            if kw:
                parts = " | ".join(f"{k}={v}" for k, v in kw.items())
                return f"{event} | {parts}"
            return event

        def debug(self, event: str, **kw: Any) -> None:
            self._log.debug(self._fmt(event, **kw))

        def info(self, event: str, **kw: Any) -> None:
            self._log.info(self._fmt(event, **kw))

        def warning(self, event: str, **kw: Any) -> None:
            self._log.warning(self._fmt(event, **kw))

        def warn(self, event: str, **kw: Any) -> None:
            self.warning(event, **kw)

        def error(self, event: str, **kw: Any) -> None:
            self._log.error(self._fmt(event, **kw))

        def critical(self, event: str, **kw: Any) -> None:
            self._log.critical(self._fmt(event, **kw))

        def bind(self, **kw: Any) -> "_StdlibBoundLogger":
            return self

    def get_logger(name: str = "") -> Any:  # type: ignore[misc]
        return _StdlibBoundLogger(name)

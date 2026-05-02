import logging
from collections import deque
from datetime import datetime, timezone

_buffer: deque = deque(maxlen=500)
_SKIP_LOGGERS = frozenset({"uvicorn.access"})


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name in _SKIP_LOGGERS:
            return
        try:
            _buffer.append({
                "time": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            })
        except Exception:
            self.handleError(record)


def setup_log_buffer() -> None:
    h = _BufferHandler(level=logging.INFO)
    h.setFormatter(logging.Formatter("%(message)s"))
    # Attach to our app logger (covers all app.* modules) and set to INFO.
    app_log = logging.getLogger("app")
    app_log.addHandler(h)
    if app_log.level == logging.NOTSET or app_log.level > logging.INFO:
        app_log.setLevel(logging.INFO)
    # Also capture uvicorn.error (startup errors, exceptions)
    logging.getLogger("uvicorn.error").addHandler(h)


def get_recent_logs(n: int = 200) -> list[dict]:
    return list(_buffer)[-n:]

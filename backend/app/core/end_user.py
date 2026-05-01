"""LiteLLM 的 end-user 標籤。

LLM 呼叫會讀 current_end_user contextvar，把值塞進 OpenAI API 的 user 參數，
LiteLLM 就能在 /End-Users 頁面看到 per-user 的 spend / token 統計。

格式：
- LINE 使用者：line-{line_user_id}
- Web 使用者：web-{api_key_id}
"""
from contextvars import ContextVar

current_end_user: ContextVar[str | None] = ContextVar(
    "current_end_user", default=None
)


def line_tag(line_user_id: str) -> str:
    return f"line-{line_user_id}"


def web_tag(api_key_id) -> str:
    return f"web-{api_key_id}"

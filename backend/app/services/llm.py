import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.end_user import current_end_user

logger = logging.getLogger(__name__)


def _user_kwargs() -> dict:
    """讀 contextvar，回傳 {'user': '...'}。所有對 LiteLLM 的 OpenAI 呼叫都帶這個，
    確保後台 /End-Users 能歸戶。"""
    user = current_end_user.get()
    return {"user": user} if user else {}


settings = get_settings()
client = AsyncOpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    timeout=360000.0,
    max_retries=0,
)

T = TypeVar("T", bound=BaseModel)


def _deref_schema(node: Any, defs: dict | None = None) -> Any:
    """把 pydantic v2 model_json_schema 裡的 $ref/$defs 全部 inline，
    產出 OpenAI tool 可直接用的 parameters schema。"""
    if defs is None and isinstance(node, dict):
        defs = node.get("$defs", {})
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return _deref_schema(defs.get(name, {}), defs)
        return {k: _deref_schema(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_deref_schema(item, defs) for item in node]
    return node


def _pydantic_to_tool(schema: Type[BaseModel]) -> dict:
    """把 pydantic schema 轉為 OpenAI function tool 定義。"""
    raw = schema.model_json_schema()
    params = _deref_schema(raw)
    name = params.pop("title", "") or schema.__name__
    description = params.pop("description", "") or schema.__name__
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        },
    }


def _strip_think(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def _extract_json_obj(text: str) -> str | None:
    """從文字中抓第一組 balanced JSON object（處理巢狀 {}）。"""
    text = _strip_think(text)
    # 優先匹配 ```json ... ``` code fence
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fence:
        return fence.group(1)
    # balanced brace scan
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


async def structured_call(
    schema: Type[T],
    system: str,
    user: str | list,
    max_tokens: int = 8192,
) -> T:
    """直接用 OpenAI client 的 tool calling 拿結構化輸出。
    Pass 1：function call；Pass 2：JSON prompt + 手動 parse（給不支援 tool 的模型）。
    所有呼叫都帶 user 標籤，確保 LiteLLM 後台能歸戶。
    """
    user_text = _flatten_content(user) if not isinstance(user, str) else user
    tool = _pydantic_to_tool(schema)
    tool_name = tool["function"]["name"]

    # Pass 1: function calling
    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            **_user_kwargs(),
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            args_str = msg.tool_calls[0].function.arguments
            try:
                data = json.loads(args_str)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning("tool_call parse failed: %s; args preview: %s", e, args_str[:300])
        else:
            logger.warning(
                "function_calling returned no tool call; raw preview: %s",
                str(msg.content or "")[:300],
            )
    except Exception as e:
        logger.warning("function_calling path failed: %s", e)

    # Pass 2: force JSON in prompt + manual parse
    fallback_system = (
        f"{system}\n\n"
        "請嚴格以 JSON 回傳，符合以下 schema，不要加 markdown code fence 以外的文字：\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    raw_text = await call_llm(
        system=fallback_system,
        messages=[{"role": "user", "content": user_text}],
        max_tokens=max_tokens,
    )
    obj_str = _extract_json_obj(raw_text)
    if not obj_str:
        raise ValueError(f"No JSON object found in LLM output. preview: {raw_text[:300]}")
    try:
        data = json.loads(obj_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON decode failed: {e}. preview: {obj_str[:300]}") from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"Schema validation failed: {e}. data: {str(data)[:300]}") from e


def _flatten_content(content: str | list) -> str:
    """將 list 型 content（multimodal）壓平為純文字，不支援視覺的模型適用"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block["text"])
        elif block.get("type") == "image_url":
            parts.append("[圖片內容，模型不支援視覺輸入]")
    return "\n".join(parts)


async def call_llm(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> str:
    """呼叫 LiteLLM（OpenAI spec）。
    若模型不支援 multimodal，自動把 list content 壓平為純文字。
    """
    normalized = [
        {**msg, "content": _flatten_content(msg["content"])}
        for msg in messages
    ]
    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, *normalized],
        **_user_kwargs(),
    )
    choice = response.choices[0]
    content = choice.message.content or ""
    # reasoning model（DeepSeek-R1、qwen3 thinking 等）會把答案放在 reasoning_content；
    # content 空時退用它，避免整段被丟掉。
    if not content.strip():
        reasoning = getattr(choice.message, "reasoning_content", None) or ""
        if reasoning.strip():
            logger.info(
                "call_llm content empty but reasoning_content has %d chars; using it",
                len(reasoning),
            )
            content = reasoning
    if not content.strip():
        finish_reason = getattr(choice, "finish_reason", None)
        usage = getattr(response, "usage", None)
        logger.warning(
            "call_llm returned empty content; model=%s finish_reason=%s usage=%s",
            settings.LLM_MODEL, finish_reason, usage,
        )
    return content


async def stream_llm(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> AsyncIterator[str]:
    """串流呼叫 LLM，yield 每個 content delta。
    包含 thinking model 的 <think>...</think> 區塊內容。
    """
    normalized = [
        {**msg, "content": _flatten_content(msg["content"])}
        for msg in messages
    ]
    stream = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, *normalized],
        stream=True,
        **_user_kwargs(),
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # 同時支援 content 與 reasoning_content（某些 Ollama / DeepSeek 實作）
        content = getattr(delta, "content", None)
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield f"<think>{reasoning}</think>"
        if content:
            yield content


VISION_MAX_PX = 1568  # 超過此邊長就縮圖，減少 token 用量


def encode_image_b64(file_path: str) -> tuple[str, str]:
    """將圖片縮圖（若過大）後編碼為 base64，回傳 (base64_data, media_type)。
    縮圖至短邊不超過 VISION_MAX_PX，輸出 JPEG（大小最小化）。
    """
    from PIL import Image
    import io

    suffix = Path(file_path).suffix.lower()
    img = Image.open(file_path).convert("RGB")
    w, h = img.size
    if max(w, h) > VISION_MAX_PX:
        scale = VISION_MAX_PX / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return data, "image/jpeg"


VISION_EXTRACT_PROMPT = """你是圖片轉文字助手。
只做一件事：把圖片裡所有可見資訊忠實轉成純文字。

規則：
- 文字部分逐字輸出（保留原語言，不翻譯）
- 表格 → 用 Markdown 表格表示
- 圖示/流程圖/插圖 → 用文字描述出來（位置、元素、彼此關係）
- 重點是「完整保留資訊」，不要做摘要、不要評論、不要省略
- 如果是手寫字，盡量辨識；認不出的標 [字跡不清]
- 純輸出抽取結果，不要加任何前後綴解釋
- 絕對不要提到「OCR」「視覺模型」「我從圖片辨識到」「以下是抽取結果」等任何描述抽取過程或所用技術的字句。
  直接輸出內容本身，當作這就是文件原文。
"""


async def vision_extract_text(image_path: str) -> str:
    """單一職責：把圖片內容轉成純文字描述，不做任何結構化。
    後續所有 wiki 化邏輯都交給普通 structured_call 處理，
    讓圖片和 PDF 走同一條 pipeline、套用同一份 INGEST_SYSTEM_PROMPT 規則。
    """
    data, media_type = encode_image_b64(image_path)
    response = await client.chat.completions.create(
        model=settings.VISION_MODEL,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": VISION_EXTRACT_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    },
                    {
                        "type": "text",
                        "text": "請把這張圖中的所有資訊忠實轉成純文字。",
                    },
                ],
            },
        ],
        **_user_kwargs(),
    )
    text = _strip_think(response.choices[0].message.content or "")
    if not text.strip():
        raise ValueError("vision model returned empty extraction")
    return text



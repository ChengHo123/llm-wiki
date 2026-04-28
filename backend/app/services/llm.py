import base64
import json
import logging
import re
from pathlib import Path
from typing import AsyncIterator, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
client = AsyncOpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    timeout=360000.0,
    max_retries=0,
)

_chat = ChatOpenAI(
    model=settings.LLM_MODEL,
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
    timeout=360000.0,
    max_retries=0,
)

T = TypeVar("T", bound=BaseModel)


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
    """用 LangChain with_structured_output 拿結構化輸出。
    策略：先試 function_calling（tool call），失敗則退回手動 JSON 解析。
    對 Ollama / thinking model 也能穩定工作。
    """
    chat = _chat.bind(max_tokens=max_tokens)

    # Pass 1: tool calling with include_raw to inspect failures
    try:
        structured = chat.with_structured_output(
            schema, method="function_calling", include_raw=True
        )
        result = await structured.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=user),
        ])
        parsed = result.get("parsed") if isinstance(result, dict) else result
        if parsed is not None:
            return parsed
        raw_msg: AIMessage | None = result.get("raw") if isinstance(result, dict) else None
        raw_text = raw_msg.content if raw_msg else ""
        logger.warning("function_calling returned no parsed object; raw preview: %s", str(raw_text)[:300])
    except Exception as e:
        logger.warning("function_calling path failed: %s", e)
        raw_text = ""

    # Pass 2: force JSON in prompt + manual parse
    fallback_system = (
        f"{system}\n\n"
        "請嚴格以 JSON 回傳，符合以下 schema，不要加 markdown code fence 以外的文字：\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    user_text = _flatten_content(user) if not isinstance(user, str) else user
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
    )
    return response.choices[0].message.content or ""


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


async def vision_structured_call(
    schema: Type[T],
    system: str,
    user_content: list,
    max_tokens: int = 16384,
) -> T:
    """Vision model 專用 structured call。
    保留圖片 content blocks，直接送 OpenAI client（繞過 LangChain）。
    跳過 function_calling pass，直接走 JSON prompt + 手動解析。
    """
    fallback_system = (
        f"{system}\n\n"
        "請嚴格以 JSON 回傳，符合以下 schema，不要加 markdown code fence 以外的文字：\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    resp = await client.chat.completions.create(
        model=settings.VISION_MODEL,
        messages=[
            {"role": "system", "content": fallback_system},
            {"role": "user", "content": user_content},
        ],
        max_tokens=max_tokens,
    )
    raw_text = _strip_think(resp.choices[0].message.content or "")
    obj_str = _extract_json_obj(raw_text)
    if not obj_str:
        raise ValueError(f"vision model 無 JSON output. preview: {raw_text[:300]}")
    try:
        data = json.loads(obj_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON decode failed: {e}. preview: {obj_str[:300]}") from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"Schema validation failed: {e}. data: {str(data)[:300]}") from e


def build_document_message(file_path: str, text_content: str | None = None) -> dict:
    """依檔案類型建立 OpenAI spec 的 message"""
    suffix = Path(file_path).suffix.lower()

    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        data, media_type = encode_image_b64(file_path)
        return {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                },
                {"type": "text", "text": "請分析以上圖片內容"},
            ],
        }
    elif suffix == ".pdf":
        # PDF 先用 pypdf 抽文字，再以文字傳入
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            text = "(PDF 解析失敗)"
        return {
            "role": "user",
            "content": f"以下是 PDF 文件內容：\n\n{text}",
        }
    else:
        return {
            "role": "user",
            "content": text_content or "",
        }

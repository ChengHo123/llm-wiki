import base64
from pathlib import Path

from openai import AsyncOpenAI

from app.core.config import get_settings

settings = get_settings()
client = AsyncOpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
)


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


def encode_image_b64(file_path: str) -> tuple[str, str]:
    """將圖片編碼為 base64，回傳 (base64_data_url, media_type)"""
    suffix = Path(file_path).suffix.lower()
    media_type_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(suffix, "image/png")
    with open(file_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


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

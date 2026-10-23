"""Image helpers: model DESCRIBES (structured observations), plain code DECIDES."""
from __future__ import annotations
import base64
import io
from pathlib import Path

from PIL import Image

from .llm import ask


def image_part(path, max_side: int = 1024) -> dict:
    """Downscale + base64 so token/RPM cost stays low."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def observe(paths: list, instruction: str, schema, system: str):
    """One multimodal call returning a pydantic observation record."""
    content = [{"type": "text", "text": instruction}]
    for p in paths:
        content.append({"type": "text", "text": f"image_id: {Path(p).stem}"})
        content.append(image_part(p))
    return ask(system, content, schema=schema, vision=True)

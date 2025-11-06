# utils_llm.py (or top of llm_agent.py)
import os, io, base64
from PIL import Image

def image_to_data_url(path: str, max_w: int = 1280, quality: int = 70) -> str:
    """
    Load an image, optionally downscale to max_w, JPEG encode (quality),
    and return a data URL suitable for input_image.
    """
    img = Image.open(path).convert("RGB")
    if img.width > max_w:
        h = int(img.height * (max_w / img.width))
        img = img.resize((max_w, h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"

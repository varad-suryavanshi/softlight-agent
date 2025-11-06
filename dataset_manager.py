# dataset_manager.py
import os
import json
import hashlib
import re
from datetime import datetime

def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)     
    text = re.sub(r"[\s-]+", "_", text.strip())
    return text

def _short_slug(text: str, max_len: int = 64) -> str:
    slug = _slugify(text)
    if len(slug) <= max_len:
        return slug
    h = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:max_len-9]}-{h}"

class DatasetManager:
    def __init__(self, base_dir="dataset"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def create_task_dir(self, app_name: str, user_task: str) -> str:
        app_slug = _short_slug(app_name)
        task_slug = _short_slug(user_task)
        path = os.path.join(self.base_dir, app_slug, task_slug)
        os.makedirs(path, exist_ok=True)
        readme = os.path.join(path, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write(user_task)
        return path

    def save_screenshot(self, path, page, step_num):
        img_path = os.path.join(path, f"step_{step_num}.png")
        page.screenshot(path=img_path)
        return img_path

    def save_metadata(self, path, metadata):
        json_path = os.path.join(path, "metadata.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
            

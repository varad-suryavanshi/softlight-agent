# user_input_manager.py
from getpass import getpass

class UserInputManager:
    def __init__(self):
        self._cache = {}  # persist_key -> value

    def request(self, field: str, prompt: str, mask: bool, persist_key: str | None = None) -> str:
        if persist_key and persist_key in self._cache:
            return self._cache[persist_key]

        text = getpass(prompt + ": ") if mask else input(prompt + ": ")

        if persist_key:
            self._cache[persist_key] = text
        return text

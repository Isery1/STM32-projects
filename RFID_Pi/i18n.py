"""
Lightweight UI translations for the ManageIO kiosk (JSON locale files).
"""

import json
import os
from typing import Any, Dict

_LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")
_FALLBACK_LANG = "EN"


def _load_catalog() -> Dict[str, Dict[str, str]]:
    catalog: Dict[str, Dict[str, str]] = {}
    for path in (
        os.path.join(_LOCALES_DIR, "en.json"),
        os.path.join(_LOCALES_DIR, "de.json"),
    ):
        if not os.path.isfile(path):
            continue
        lang = "EN" if path.endswith("en.json") else "DE"
        with open(path, encoding="utf-8") as f:
            catalog[lang] = json.load(f)
    return catalog


_CATALOG = _load_catalog()


class Translator:
    """Resolve UI string keys for the active language with EN fallback."""

    def __init__(self, lang: str = _FALLBACK_LANG):
        self.lang = lang if lang in _CATALOG else _FALLBACK_LANG

    def set_lang(self, lang: str) -> None:
        self.lang = lang if lang in _CATALOG else _FALLBACK_LANG

    def t(self, key: str, **kwargs: Any) -> str:
        text = _CATALOG.get(self.lang, {}).get(key)
        if text is None:
            text = _CATALOG.get(_FALLBACK_LANG, {}).get(key)
        if text is None:
            return key
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                return text
        return text

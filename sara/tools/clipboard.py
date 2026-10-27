"""
sara/tools/clipboard.py
Clipboard read/write tools for Sara AI.

Allows Sara to read whatever text is currently on the clipboard
(e.g. "what's on my clipboard") and to copy text to the clipboard
(e.g. "copy this for me").
"""

import logging
import pyperclip
from config import Config

logger = logging.getLogger(__name__)


def read_clipboard(as_full_sentence: bool = False) -> str:
    """
    Reads the current text content of the system clipboard.

    Args:
        as_full_sentence: If True, returns a complete, speakable sentence
            (e.g. "Your clipboard contains: ...") instead of raw content,
            so the caller doesn't need to add its own prefix.

    Returns:
        The clipboard text, or a human-readable message if empty/unavailable.
    """
    try:
        content = pyperclip.paste()
        if not content or not content.strip():
            return "Your clipboard is currently empty."

        if Config.DEBUG_MODE:
            print(f"[Debug] Clipboard read: {len(content)} characters.")

        clean_content = content.strip()
        if as_full_sentence:
            return f"Your clipboard contains: {clean_content}"
        return clean_content
    except Exception as e:
        logger.error(f"Clipboard read failed: {e}")
        return "Sorry, I couldn't access the clipboard right now."


def write_clipboard(text: str) -> str:
    """
    Copies the given text to the system clipboard.

    Args:
        text: The text to copy.

    Returns:
        A human-readable status message.
    """
    if not text or not text.strip():
        return "There's nothing to copy."

    try:
        pyperclip.copy(text.strip())
        if Config.DEBUG_MODE:
            print(f"[Debug] Clipboard write: {len(text)} characters.")
        return "Copied to your clipboard."
    except Exception as e:
        logger.error(f"Clipboard write failed: {e}")
        return "Sorry, I couldn't access the clipboard right now."

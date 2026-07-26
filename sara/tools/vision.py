"""
sara/tools/vision.py
Screenshot capture and AI-powered visual description for Sara AI.

Captures the current screen using `mss` (a fast, cross-platform
screenshot library) and sends it to Gemini's vision-capable model to
generate a concise, voice-friendly description of what's on screen.
"""

import io
import logging
from datetime import datetime

import mss
try:
    from PIL import Image
    _HAS_PIL = True
except (ImportError, OSError) as e:
    Image = None
    _HAS_PIL = False
    logging.getLogger(__name__).warning(
        "Pillow is not installed or failed to load; vision features will be disabled. "
        "Run: pip install pillow."
        f" ({e})"
    )
from google import genai
from google.genai import types, errors

from config import Config

logger = logging.getLogger(__name__)


class VisionAssistant:
    """Handles screenshot capture and AI-based image description."""

    # BUGFIX (root cause of "preview mode, no backend connected"): self.client
    # below is a genai.Client instance -- unlike every other engine object in
    # this codebase it isn't stored under an underscore-prefixed attribute
    # name, so pywebview's js_api bridge (which recurses into every plain,
    # non-underscore, non-callable attribute reachable off the exposed Api
    # object -- see sara/core/llm/engine.py's SaraLLM._serializable note for
    # the full explanation) was walking straight into this SDK client's large
    # internal object graph on a background thread during window creation.
    # `_serializable = False` is pywebview's documented flag for opting an
    # object out of that walk entirely.
    _serializable = False

    def __init__(self):
        """Initializes a Gemini client for vision requests."""
        if not Config.GEMINI_API_KEY or not Config.GEMINI_API_KEY.strip():
            logger.warning(
                "VisionAssistant: GEMINI_API_KEY not set — vision features "
                "will be unavailable until it's configured."
            )
            self.client = None
            return

        if not _HAS_PIL:
            logger.warning(
                "VisionAssistant: Pillow is unavailable — vision features "
                "will be disabled until pillow is installed."
            )
            self.client = None
            return

        try:
            self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
            self.model_name = Config.VISION_MODEL
            if Config.DEBUG_MODE:
                print("[Debug] VisionAssistant initialized.")
        except Exception as e:
            print(f"[Error] Failed to initialize VisionAssistant: {e}")
            self.client = None

    def capture_screenshot(self) -> "Image.Image | None":
        """
        Captures the current primary screen as a Pillow Image.

        Returns:
            A PIL.Image object, or None on failure.
        """
        if not _HAS_PIL:
            logger.warning(
                "VisionAssistant: cannot capture screenshot because Pillow is not available."
            )
            return None

        try:
            with mss.mss() as sct:
                # Monitor index 1 is the primary display in mss's
                # convention (index 0 is "all monitors combined").
                monitor = sct.monitors[1]
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                return img
        except Exception as e:
            logger.error(f"Screenshot capture failed: {e}")
            return None

    def describe_screen(self, user_question: str = "") -> str:
        """
        Captures the current screen and asks Gemini to describe it,
        optionally focused on a specific user question.

        Args:
            user_question: Optional specific question about the screen
                           (e.g. "what error is shown?"). If empty, a
                           general description is requested.

        Returns:
            A concise, voice-friendly description, or an error message.
        """
        if not self.client:
            return "Vision system is currently offline."

        img = self.capture_screenshot()
        if img is None:
            return "I couldn't capture a screenshot right now."

        try:
            # Convert to JPEG bytes in memory (faster + smaller than
            # PNG for photographic/screen content, no temp file needed).
            buffer = io.BytesIO()
            # Downscale very large screens to keep the upload fast;
            # Gemini doesn't need full 4K resolution to describe content.
            img.thumbnail((1280, 1280))
            img.save(buffer, format="JPEG", quality=85)
            image_bytes = buffer.getvalue()

            prompt_text = (
                user_question.strip()
                if user_question and user_question.strip()
                else "Briefly describe what is shown on this screen in 1-2 sentences, voice-friendly, no markdown."
            )

            # NOTE: No built-in timeout support found in this SDK version for generate_content().
            # Consider wrapping this call in a ThreadPoolExecutor with future.result(timeout=15)
            # at the CALLER level if this call ever needs to be time-bounded.
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    prompt_text,
                ],
                config=types.GenerateContentConfig(temperature=0.4),
            )

            if not response.text:
                logger.warning("Gemini Vision returned empty/blocked response.")
                return "Sorry, I couldn't get a description for that screenshot."
            return response.text.strip()
        except errors.APIError as e:
            logger.error(f"Gemini Vision API error: {e}")
            return "Sorry, I'm having trouble with the vision service right now."
        except Exception as e:
            logger.error(f"Screenshot analysis failed: {e}")
            return "Sorry, I couldn't analyze the screenshot right now."

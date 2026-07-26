"""
sara/tools/web.py
Web-related tools for Sara AI: search, weather/news lookups, reading
and extracting article text from a page, and launching URLs in the
default browser.

PRODUCTION-AUDIT FIXES (this revision)
----------------------------------------
1. search_web() / get_news(): DDGS() now receives an explicit timeout
   so a genuinely-hung network call can no longer tie up a worker
   thread indefinitely (previously _call_with_timeout's outer timeout
   in gui_main.py could only free the CALLING thread, not the actual
   stuck background call).
2. Raw exception text is no longer returned (and therefore never
   spoken aloud by TTS) from search_web, get_news, get_weather,
   play_youtube, play_spotify, or open_url. Every failure path now
   logs the real exception via `logger.error(...)` and returns a
   short, friendly, generic message instead. read_webpage()'s
   existing "Error: ..." prefix convention is unchanged, since
   gui_main.py's _h_summarize_url() specifically checks for that
   prefix to detect failure.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
import webbrowser
from datetime import date
from typing import Any, Callable, Optional

import requests

from config import Config

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDGS_AVAILABLE = True
    except ImportError:
        _DDGS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# NEW: explicit timeout for DDGS() search/news calls. Previously DDGS()
# was constructed with no timeout at all, so a genuinely hung network
# call (not a failed one — a stuck one) could tie up a worker thread
# indefinitely; gui_main.py's _call_with_timeout() outer timeout only
# frees the CALLING thread in that case, not the actual stuck background
# call. The ddgs/duckduckgo_search library accepts a `timeout` kwarg on
# the DDGS() constructor itself (applied per-request internally).
_DDGS_TIMEOUT_SECONDS = 10

_CACHE_TTL_SECONDS = 60
_CACHE_MAX_SIZE = 256
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> Optional[str]:
    with _cache_lock:
        entry = _cache.get(key)
    if entry and (time.monotonic() - entry[1]) < _CACHE_TTL_SECONDS:
        return entry[0]
    return None


def _cache_set(key: str, value: str) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX_SIZE:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            del _cache[oldest]
        _cache[key] = (value, time.monotonic())


def _check_search_backend() -> Optional[str]:
    """Returns an error message if no DuckDuckGo backend is installed, else None."""
    if not _DDGS_AVAILABLE:
        return "Web search requires the 'ddgs' package. Please install it."
    return None


def _normalize_url(url: str) -> str:
    """Ensures a URL has an http(s) scheme, defaulting to https://."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _validate_max_results(max_results: int, name: str = "max_results") -> int:
    """Clamps max_results to a sane [1, 20] range, logging a warning on bad input."""
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        logger.warning("%s must be a positive integer; got %r. Defaulting to 3.", name, max_results)
        return 3
    return min(max_results, 20)


def _retry(fn: "Callable[[], Any]", attempts: int = 2, delay: float = 1.0):
    """
    Calls fn() up to `attempts` times, sleeping `delay` seconds between
    retries. Returns the successful result, or re-raises the last exception.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1.")
    last_exc: Exception = Exception("No attempts made.")
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            logger.debug("_retry attempt %d/%d failed: %s", i + 1, attempts, e)
            if i < attempts - 1:
                time.sleep(delay)
    raise last_exc


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "search_web",
    "get_news",
    "get_weather",
    "read_webpage",
    "open_url",
    "play_youtube",
    "play_spotify",
    "get_youtube_url",
]


# ============================================================
# YOUTUBE
# ============================================================

def get_youtube_url(query: str) -> Optional[str]:
    """
    Searches YouTube and returns the URL of the first non-Shorts video.

    Returns:
        Full YouTube watch URL, or a filtered search URL as fallback.
    """
    encoded = urllib.parse.quote_plus(query)
    fallback = f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAQ%3D%3D"

    if not _BS4_AVAILABLE:
        return fallback

    search_url = (
        f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAQ%3D%3D"
    )

    try:
        resp = requests.get(
            search_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.debug("YouTube fetch error: %s", e)
        return fallback

    # Limit search window to avoid regex on full (potentially huge) HTML
    html_sample = resp.text[:200_000]
    matches = re.findall(r'"(/watch\?v=[a-zA-Z0-9_-]{11})"', html_sample)

    seen: set[str] = set()
    for href in matches:
        if href in seen:
            continue
        seen.add(href)

        if "/shorts/" in href:
            continue

        idx = html_sample.find(f'"{href}"')
        context = html_sample[max(0, idx - 200): idx + 200]
        if "reelwatch" in context.lower() or '"shorts"' in context.lower():
            continue

        return f"https://www.youtube.com{href}"

    return fallback


def play_youtube(query: str) -> str:
    """
    Searches YouTube for the given query and opens the first non-Shorts
    video result in the default browser.
    """
    if not query or not query.strip():
        return "Please tell me what to play on YouTube."

    query = query.strip()
    cache_key = f"youtube:{query.lower()}"
    url = _cache_get(cache_key)

    if not url:
        url = get_youtube_url(query)
        if url:
            _cache_set(cache_key, url)

    if not url:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAQ%3D%3D"

    try:
        opened = webbrowser.open(url)
        logger.debug("YouTube open (%s): %s", "ok" if opened else "warn", url)
        if "/watch?v=" in url:
            return f"Playing {query} on YouTube."
        return f"Opening YouTube search results for {query}."
    except Exception as e:
        logger.error("Failed to open YouTube for '%s': %s", query, e)
        return "Sorry, I couldn't open YouTube right now."


# ============================================================
# SPOTIFY
# ============================================================

def play_spotify(query: str) -> str:
    """
    Attempts to open a song/artist in the Spotify desktop app via the
    spotify: URI scheme. Falls back to the Spotify web search page.
    """
    if not query or not query.strip():
        return "Please tell me what to play on Spotify."

    query = query.strip()
    spotify_uri = f"spotify:search:{urllib.parse.quote(query)}"

    try:
        opened = webbrowser.open(spotify_uri)
        if opened:
            logger.debug("Spotify URI opened: %s", spotify_uri)
            return f"Opening {query} in Spotify."
    except Exception as e:
        logger.debug("Spotify URI scheme failed (app not installed?): %s", e)

    encoded = urllib.parse.quote_plus(query)
    web_url = f"https://open.spotify.com/search/{encoded}"
    try:
        webbrowser.open(web_url)
        return f"Opening Spotify search for {query}."
    except Exception as e:
        logger.error("Failed to open Spotify for '%s': %s", query, e)
        return "Sorry, I couldn't open Spotify right now."


# ============================================================
# WEB SEARCH
# ============================================================

def search_web(query: str, max_results: int = 3) -> str:
    """
    Performs a general web search using DuckDuckGo and returns a
    concise, voice-friendly summary of the top results.
    """
    error = _check_search_backend()
    if error:
        return error

    if not query or not query.strip():
        return "No search query was provided."

    max_results = _validate_max_results(max_results)
    query = query.strip()
    cache_key = f"search:{query.lower()}:{max_results}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Web search cache hit for '%s'.", query)
        return cached

    try:
        def _do_search():
            # NEW: explicit timeout so a hung request can't tie up a
            # worker thread indefinitely (see module docstring).
            with DDGS(timeout=_DDGS_TIMEOUT_SECONDS) as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        results = _retry(_do_search, attempts=2, delay=1.0)

        if not results:
            return f"No results found for '{query}'."

        summary_lines = [
            f"{i}. {r.get('title', 'Untitled')}: {r.get('body', '')}"
            for i, r in enumerate(results, start=1)
        ]
        summary = "\n".join(summary_lines)
        _cache_set(cache_key, summary)
        logger.debug("Web search for '%s' returned %d results.", query, len(results))
        return summary
    except Exception as e:
        logger.error("Web search failed for '%s': %s", query, e)
        return "Sorry, I couldn't complete that search right now."


def get_news(topic: str = "", max_results: int = 3) -> str:
    """
    Fetches recent news headlines, optionally filtered by topic, using
    DuckDuckGo's news search.
    """
    error = _check_search_backend()
    if error:
        return error

    max_results = _validate_max_results(max_results)
    # Include today's date so cached results from a prior day are distinct.
    today = date.today().isoformat()
    query = topic.strip() if topic and topic.strip() else f"top news {today}"
    cache_key = f"news:{query.lower()}:{max_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        def _do_news():
            # NEW: explicit timeout — see module docstring.
            with DDGS(timeout=_DDGS_TIMEOUT_SECONDS) as ddgs:
                return list(ddgs.news(query, max_results=max_results))

        results = _retry(_do_news, attempts=2, delay=1.0)

        if not results:
            return f"No news found for '{query}'."

        headlines = [
            f"{i}. {r.get('title', 'Untitled')} ({r.get('source', 'Unknown source')})"
            for i, r in enumerate(results, start=1)
        ]
        summary = "\n".join(headlines)
        _cache_set(cache_key, summary)
        return summary
    except Exception as e:
        logger.error("Failed to fetch news for '%s': %s", query, e)
        return "Sorry, I couldn't fetch the news right now."


# ============================================================
# WEATHER
# ============================================================

def get_weather(location: str) -> str:
    """
    Fetches a concise current weather report for a given location using
    wttr.in (free, no API key required).
    """
    if not location or not location.strip():
        return "Please specify a location for the weather report."

    location = location.strip()
    cache_key = f"weather:{location.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=8,
            stream=False,
        )
        response.raise_for_status()

        # Guard against unexpectedly large responses from wttr.in
        text = response.text[:500].strip()
        if not text or "Unknown location" in text:
            return f"Could not find weather data for '{location}'."

        _cache_set(cache_key, text)
        return text
    except requests.exceptions.Timeout:
        return "Weather lookup timed out. Please check your internet connection."
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch weather for '%s': %s", location, e)
        return "Sorry, I couldn't fetch the weather right now."


# ============================================================
# WEB PAGE READER
# ============================================================

def read_webpage(url: str, max_chars: int = 4000) -> str:
    """
    Fetches a webpage and extracts its main readable text, stripping
    scripts, styles, navigation, ads, and other boilerplate.

    Returns extracted text on success, or a string prefixed with
    "Error:" on failure so callers can reliably detect failure.

    NOTE: this "Error:" prefix convention is intentionally UNCHANGED —
    gui_main.py's _h_summarize_url() specifically checks for it to
    detect a failed fetch before handing the result to the LLM for
    summarization.
    """
    if not _BS4_AVAILABLE:
        return "Error: Page reading requires the 'beautifulsoup4' package. Please install it."

    if not url or not url.strip():
        return "Error: No URL was provided."

    if not isinstance(max_chars, int) or max_chars < 1:
        return "Error: max_chars must be a positive integer."

    url = _normalize_url(url.strip())
    # Preserve URL path case; only lowercase scheme+host for the cache key.
    parsed = urllib.parse.urlparse(url)
    cache_key = f"page:{parsed.scheme}://{parsed.netloc.lower()}{parsed.path}:{max_chars}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Page cache hit for '%s'.", url)
        return cached

    try:
        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return f"Error: Timed out fetching '{url}'."
    except requests.exceptions.RequestException as e:
        return f"Error: Failed to fetch '{url}'. Details: {e}"

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "form", "noscript"]):
            tag.decompose()

        container = soup.find("article") or soup.find("main") or soup.body or soup

        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = "\n".join(p for p in paragraphs if len(p) > 40)

        if not text:
            # Fallback: limit raw text extraction to avoid huge allocations
            text = container.get_text(" ", strip=True)[:max_chars * 2]

        text = " ".join(text.split())

        if not text:
            return f"Error: Could not extract any readable content from '{url}'."

        truncated = text[:max_chars]
        if len(text) > max_chars:
            truncated += "..."

        _cache_set(cache_key, truncated)
        logger.debug("Extracted %d chars from '%s'.", len(truncated), url)
        return truncated

    except Exception as e:
        logger.error("Failed to parse page content from '%s': %s", url, e)
        return f"Error: Failed to parse page content from '{url}'."


# ============================================================
# URL OPENER
# ============================================================

def open_url(url: str) -> str:
    """
    Opens a URL in the system's default web browser. Automatically
    prepends 'https://' if the scheme is missing.
    """
    if not url or not url.strip():
        return "No URL was provided."

    url = _normalize_url(url.strip())

    try:
        opened = webbrowser.open(url)
        logger.debug("open_url (%s): %s", "ok" if opened else "warn", url)
        return f"Opening {url} in your browser."
    except Exception as e:
        logger.error("Failed to open URL '%s': %s", url, e)
        return "Sorry, I couldn't open that link right now."

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

CONFIRMED-BUG FIX (this revision)
----------------------------------------
3. get_weather(): wttr.in occasionally serves its full HTML homepage
   instead of the short `?format=3` plaintext line (seen under
   rate-limiting / service hiccups). The old code only checked for the
   substring "Unknown location" and otherwise returned response.text
   as-is -- so the raw "<!DOCTYPE html>..." source could get spoken
   aloud by TTS. get_weather() now validates the response looks like
   real format=3 weather text (Content-Type, HTML markers, and length)
   before returning it, and falls back to the same friendly error
   message used for every other weather failure path otherwise.
   get_news() and read_webpage() were audited for the same pattern and
   do NOT have it: get_news() returns structured dicts from the DDGS
   library (never raw HTML), and read_webpage() already parses with
   BeautifulSoup and extracts <p> text rather than returning raw
   response.text. No speculative changes were made to either.

OPTIMIZATIONS (this revision)
----------------------------------------
4. get_weather() now retries transient failures via the existing
   _retry() helper, same as search_web()/get_news() -- previously it
   was the only network tool with no retry at all.
5. get_weather() forces UTF-8 decoding on the response. wttr.in
   doesn't always send a charset header, so requests can guess wrong
   and mangle the weather emoji/degree symbol -- which then gets read
   aloud garbled by TTS.
6. "unknown location" check is now case-insensitive (was previously a
   case-sensitive substring match that could silently miss variants).
7. All outbound HTTP calls (weather, page reads, YouTube HTML
   fallback) now share one requests.Session with connection pooling
   and a transport-level Retry adapter, instead of opening a fresh
   connection (and, for 429/5xx, giving up immediately) on every call.
8. _retry() now adds a small random jitter to its backoff delay, so
   multiple tools retrying at the same moment (e.g. right after a
   brief Wi-Fi drop) don't all hammer the network again in lockstep.
9. read_webpage() now streams the response and stops after a hard
   byte cap (_MAX_PAGE_DOWNLOAD_BYTES) instead of unconditionally
   pulling an entire (possibly huge) page into memory before
   truncating it down to max_chars.
10. User-supplied location/topic/query/url strings are now clamped to
    sane maximum lengths before being used to build outbound requests,
    instead of being passed through unbounded.
11. Magic-number timeouts and repeated fallback-message literals are
    now named constants at the top of the file, so they're tuned/fixed
    in exactly one place instead of being scattered through the file.

None of the above change the success-path behavior or return format
of any function for a normal, valid response.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import urllib.parse
import webbrowser
from datetime import date
from typing import Any, Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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

try:
    import yt_dlp
    _YTDLP_AVAILABLE = True
except ImportError:
    yt_dlp = None
    _YTDLP_AVAILABLE = False

# Small in-process cache so a repeated "next video" request within the
# same session doesn't re-run a yt-dlp search; keyed by lowercased query.
_YTDLP_CACHE_TTL_S = 600
_ytdlp_cache: dict[str, tuple[float, list[str]]] = {}
_ytdlp_cache_lock = threading.Lock()

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

# OPTIMIZATION 11: named timeout / limit constants instead of magic
# numbers scattered across functions.
_WEATHER_TIMEOUT_SECONDS = 8
_PAGE_FETCH_TIMEOUT_SECONDS = 10
_YTDLP_SOCKET_TIMEOUT_SECONDS = 8
_YTDLP_SCRAPE_TIMEOUT_SECONDS = 10
_MAX_PAGE_DOWNLOAD_BYTES = 2 * 1024 * 1024  # 2 MB hard cap for read_webpage()

# OPTIMIZATION 10: sane upper bounds on user-supplied strings that get
# baked into outbound URLs.
_MAX_LOCATION_LENGTH = 100
_MAX_QUERY_LENGTH = 200
_MAX_URL_LENGTH = 2000

# OPTIMIZATION 11: fallback messages centralized so wording only ever
# needs to change in one place.
_WEATHER_FALLBACK_MSG = "Sorry, I couldn't fetch the weather right now."
_SEARCH_FALLBACK_MSG = "Sorry, I couldn't complete that search right now."
_NEWS_FALLBACK_MSG = "Sorry, I couldn't fetch the news right now."

_CACHE_TTL_SECONDS = 60
_CACHE_MAX_SIZE = 256
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


# ============================================================
# SHARED HTTP SESSION  (OPTIMIZATION 7)
# ============================================================

def _build_session() -> requests.Session:
    """
    One shared requests.Session for every plain HTTP call in this
    module (weather, page reads, YouTube HTML fallback). This reuses
    TCP/TLS connections instead of paying a fresh handshake on every
    call, and adds a transport-level retry (with backoff) for
    429/5xx responses, on top of the tool-level _retry() used for
    weather/search/news.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": _USER_AGENT})
    return session


_session = _build_session()


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


def _clamp_text_length(value: str, max_len: int, name: str) -> str:
    """
    OPTIMIZATION 10: truncates an unusually long input (location, search
    query, news topic, URL) instead of passing it straight through into
    an outbound request unbounded.
    """
    if len(value) > max_len:
        logger.warning(
            "%s is unusually long (%d chars); truncating to %d.",
            name, len(value), max_len,
        )
        return value[:max_len]
    return value


def _retry(fn: "Callable[[], Any]", attempts: int = 2, delay: float = 1.0):
    """
    Calls fn() up to `attempts` times, sleeping ~`delay` seconds (plus a
    small random jitter -- OPTIMIZATION 8) between retries. Returns the
    successful result, or re-raises the last exception.
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
                jitter = random.uniform(0, delay * 0.3)
                time.sleep(delay + jitter)
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

def _youtube_search_fallback_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    return f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAQ%3D%3D"


def _get_youtube_urls_ytdlp(query: str, max_results: int = 5) -> list[str]:
    """
    Primary lookup path: asks yt-dlp to search YouTube directly
    (ytsearchN:) and extract real video URLs from its own metadata
    parser, instead of regex-scraping YouTube's search-results HTML
    (which breaks silently whenever YouTube changes its page markup).
    Filters out Shorts by URL shape and by duration (<= 60s).
    """
    if not _YTDLP_AVAILABLE:
        return []

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "default_search": "ytsearch",
        "socket_timeout": _YTDLP_SOCKET_TIMEOUT_SECONDS,
    }
    urls: list[str] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            entries = (info or {}).get("entries") or []
            for entry in entries:
                if not entry:
                    continue
                video_id = entry.get("id")
                if not video_id:
                    continue
                url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                if "/shorts/" in url:
                    continue
                duration = entry.get("duration")
                if duration is not None and duration <= 60:
                    continue
                if url not in urls:
                    urls.append(url)
    except Exception as e:
        logger.debug("yt-dlp search failed for %r: %s", query, e)
        return []

    return urls


def _get_youtube_urls_scrape(query: str) -> list[str]:
    """
    Fallback lookup path (used only if yt-dlp is unavailable or its
    search fails): regex-scrapes YouTube's search-results HTML for the
    first non-Shorts /watch?v= link. Kept as-is from the original
    implementation so behavior degrades gracefully, not silently.
    """
    if not _BS4_AVAILABLE:
        return []

    search_url = _youtube_search_fallback_url(query)
    try:
        # OPTIMIZATION 7: shared session (connection pooling + transport retry).
        resp = _session.get(search_url, timeout=_YTDLP_SCRAPE_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.debug("YouTube fetch error: %s", e)
        return []

    # Limit search window to avoid regex on full (potentially huge) HTML
    html_sample = resp.text[:200_000]
    matches = re.findall(r'"(/watch\?v=[a-zA-Z0-9_-]{11})"', html_sample)

    seen: set[str] = set()
    urls: list[str] = []
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

        urls.append(f"https://www.youtube.com{href}")

    return urls


def get_youtube_urls(query: str, max_results: int = 5) -> list[str]:
    """
    Returns an ordered list of candidate YouTube watch URLs for `query`
    (first entry = what should actually be played), tried yt-dlp-first
    then falling back to HTML-scrape, then to nothing (caller falls
    back to a plain search-results URL). Cached briefly per-query so a
    "next video" follow-up doesn't re-run the search from scratch.
    """
    key = query.strip().lower()
    now = time.time()
    with _ytdlp_cache_lock:
        cached = _ytdlp_cache.get(key)
        if cached and (now - cached[0]) < _YTDLP_CACHE_TTL_S:
            return cached[1]

    urls = _get_youtube_urls_ytdlp(query, max_results=max_results)
    if not urls:
        urls = _get_youtube_urls_scrape(query)

    if urls:
        with _ytdlp_cache_lock:
            _ytdlp_cache[key] = (now, urls)
    return urls


def get_youtube_url(query: str) -> Optional[str]:
    """Back-compat single-URL wrapper around get_youtube_urls()."""
    urls = get_youtube_urls(query)
    return urls[0] if urls else None


def play_youtube(query: str, skip: int = 0) -> str:
    """
    Searches YouTube for the given query and opens a non-Shorts video
    result in the default browser -- the first result by default, or
    the (skip+1)-th candidate from the same cached search when `skip`
    is used (see play_next_youtube() below, for "play next video").
    """
    if not query or not query.strip():
        return "Please tell me what to play on YouTube."

    query = query.strip()
    urls = get_youtube_urls(query)

    if skip and skip < len(urls):
        url = urls[skip]
    elif urls:
        url = urls[0]
    else:
        url = _youtube_search_fallback_url(query)

    try:
        opened = webbrowser.open(url)
        logger.debug("YouTube open (%s): %s", "ok" if opened else "warn", url)
        if "/watch?v=" in url:
            return f"Playing {query} on YouTube."
        return f"Opening YouTube search results for {query}."
    except Exception as e:
        logger.error("Failed to open YouTube for '%s': %s", query, e)
        return "Sorry, I couldn't open YouTube right now."


def play_next_youtube(query: str, current_index: int) -> tuple[str, int]:
    """
    Plays the next cached candidate for the same query (used by the
    'next video' follow-up intent). Returns (message, new_index). If
    there's no next cached candidate, re-searches with a lightly
    varied query so the user still gets *something* rather than a
    dead-end "no more results".
    """
    urls = get_youtube_urls(query)
    next_index = current_index + 1

    if next_index < len(urls):
        msg = play_youtube(query, skip=next_index)
        return msg, next_index

    # Cached list exhausted -- ask yt-dlp for a fresh batch instead of
    # just repeating the same fallback search URL.
    key = query.strip().lower()
    with _ytdlp_cache_lock:
        _ytdlp_cache.pop(key, None)
    urls = get_youtube_urls(query)
    if urls:
        msg = play_youtube(query, skip=0)
        return msg, 0

    return "Sorry, I couldn't find another video for that.", current_index


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
    query = _clamp_text_length(query.strip(), _MAX_QUERY_LENGTH, "search query")
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
        return _SEARCH_FALLBACK_MSG


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
    topic = _clamp_text_length(topic.strip(), _MAX_QUERY_LENGTH, "news topic") if topic else ""
    query = topic if topic else f"top news {today}"
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
        return _NEWS_FALLBACK_MSG


# ============================================================
# WEATHER
# ============================================================

def get_weather(location: str) -> str:
    """
    Fetches a concise current weather report for a given location using
    wttr.in (free, no API key required).

    BUGFIX: previously this returned response.text[:500] unconditionally
    (aside from an "Unknown location" check), so if wttr.in ever served
    its full HTML homepage instead of the short `?format=3` line (seen
    under rate-limiting / hiccups), that raw HTML got returned and then
    spoken aloud by TTS. This is now detected and rejected before it can
    reach the caller.
    """
    if not location or not location.strip():
        return "Please specify a location for the weather report."

    location = _clamp_text_length(location.strip(), _MAX_LOCATION_LENGTH, "weather location")
    cache_key = f"weather:{location.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    def _do_fetch():
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
        # OPTIMIZATION 7: shared session (connection pooling + transport retry).
        resp = _session.get(url, timeout=_WEATHER_TIMEOUT_SECONDS)
        resp.raise_for_status()
        # OPTIMIZATION 5: wttr.in doesn't always send a charset header,
        # which can make requests guess the wrong encoding and mangle the
        # weather emoji/degree symbol before TTS reads it aloud. Force
        # UTF-8 explicitly.
        resp.encoding = "utf-8"
        return resp

    try:
        # OPTIMIZATION 4: get_weather() now retries transient failures,
        # same as search_web()/get_news() -- previously it had none.
        response = _retry(_do_fetch, attempts=2, delay=1.0)
    except requests.exceptions.Timeout:
        return "Weather lookup timed out. Please check your internet connection."
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch weather for '%s': %s", location, e)
        return _WEATHER_FALLBACK_MSG

    content_type = response.headers.get("Content-Type", "").lower()
    # Guard against unexpectedly large responses from wttr.in
    text = response.text[:500].strip()

    # CONFIRMED-BUG FIX: detect wttr.in serving its full HTML homepage
    # instead of the expected short format=3 plaintext line. A real
    # format=3 response is a single short line like "Ajmer: (icon) +31C",
    # so anything HTML-flavored or suspiciously long is rejected here
    # instead of being returned (and spoken) as-is.
    looks_like_html = (
        "text/html" in content_type
        or "<!doctype" in text.lower()
        or "<html" in text.lower()
        or len(text) > 150
    )
    if looks_like_html:
        logger.error(
            "Weather lookup for '%s' returned a non-plaintext/HTML response "
            "(content-type=%r, response_len=%d) instead of the expected "
            "format=3 text; discarding instead of returning raw HTML.",
            location, content_type, len(response.text),
        )
        return _WEATHER_FALLBACK_MSG

    # OPTIMIZATION 6: case-insensitive check (was case-sensitive before).
    if not text or "unknown location" in text.lower():
        return f"Could not find weather data for '{location}'."

    _cache_set(cache_key, text)
    return text


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

    url = _normalize_url(_clamp_text_length(url.strip(), _MAX_URL_LENGTH, "URL"))
    # Preserve URL path case; only lowercase scheme+host for the cache key.
    parsed = urllib.parse.urlparse(url)
    cache_key = f"page:{parsed.scheme}://{parsed.netloc.lower()}{parsed.path}:{max_chars}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Page cache hit for '%s'.", url)
        return cached

    try:
        # OPTIMIZATION 9: stream the response and stop after a hard byte
        # cap instead of unconditionally pulling a (possibly huge) page
        # fully into memory before truncating it down to max_chars.
        response = _session.get(url, timeout=_PAGE_FETCH_TIMEOUT_SECONDS, stream=True)
        response.raise_for_status()

        raw_bytes = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            raw_bytes.extend(chunk)
            if len(raw_bytes) >= _MAX_PAGE_DOWNLOAD_BYTES:
                logger.debug(
                    "read_webpage: hit %d-byte cap for '%s'; stopping download.",
                    _MAX_PAGE_DOWNLOAD_BYTES, url,
                )
                break

        html_text = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
    except requests.exceptions.Timeout:
        return f"Error: Timed out fetching '{url}'."
    except requests.exceptions.RequestException as e:
        return f"Error: Failed to fetch '{url}'. Details: {e}"

    try:
        soup = BeautifulSoup(html_text, "html.parser")

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

    url = _normalize_url(_clamp_text_length(url.strip(), _MAX_URL_LENGTH, "URL"))

    try:
        opened = webbrowser.open(url)
        logger.debug("open_url (%s): %s", "ok" if opened else "warn", url)
        return f"Opening {url} in your browser."
    except Exception as e:
        logger.error("Failed to open URL '%s': %s", url, e)
        return "Sorry, I couldn't open that link right now."
"""
cache.py
--------
Local result cache for the Employability Rating Engine.

WHY THIS EXISTS:
Even with temperature=0 and a tightened two-stage prompt (see
analyzer.py), the OpenAI API does not guarantee bit-identical output on
repeat calls — batched GPU inference introduces floating-point variance
outside our control. That gives you "close" consistency, not guaranteed
consistency.

This file gives you the only thing that CAN be guaranteed: if the exact
same document text has been scored before, return that exact same
stored result instead of calling the API again. No model call, no
variance, no ambiguity — the second, third, and fiftieth upload of an
identical CV returns identical numbers because they're the same dict
read off disk, not a fresh AI judgment.

WHAT THIS DOES NOT FIX:
This does NOT make the model itself more consistent on genuinely new or
slightly-edited input. If someone fixes a typo in their CV and
re-uploads, that's a different hash, a cache miss, and a fresh API call
— which depends on analyzer.py's own consistency, not this file. Don't
treat caching as a substitute for prompt-level consistency work; treat
it as a guarantee layered on top of it for the specific case of
identical input.

HOW IT WORKS:
- The combined extracted text (what actually gets sent to OpenAI) is
  hashed with SHA-256.
- Results are stored as JSON files on disk, one per hash, in a local
  cache/ folder next to this script.
- Before calling the API, analyzer.py checks the cache. Hit -> return
  the stored result immediately, no API call. Miss -> call the API,
  then store the result under that hash for next time.

This is intentionally a flat-file cache, not a database. For a single-
user desktop app this is more than sufficient and adds zero new
dependencies (no sqlite setup, no server). If Employable later becomes
multi-user/cloud, swap _read/_write for a real key-value store — the
hash-based interface (get_cached / store_result) doesn't need to
change.
"""

import hashlib
import json
import os
import time

if os.environ.get("VERCEL"):
    CACHE_DIR = "/tmp/employable_cache"
else:
    CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def hash_text(combined_text: str, extra_context: str = "") -> str:
    """
    Deterministic fingerprint for a given document set + any extra
    context the user typed in. Extra context is included in the hash
    because it can change what the model is asked to weigh (e.g. a
    stated target role) — two identical CVs with different typed-in
    context are not "the same request" and shouldn't share a cached
    result.

    Whitespace is NOT normalized before hashing — a re-extraction of
    literally the same file should produce byte-identical text from a
    deterministic extractor (pypdf/python-docx are deterministic; only
    the OCR path in extract.py is not, which is a separate, real
    limitation worth knowing about: a photo of the same CV reshot or
    re-OCR'd may hash differently even though a human would call it
    "the same document." That's expected — OCR text genuinely can come
    out different, so scoring it as a fresh input rather than silently
    assuming it's identical is the more honest behavior, not a bug to
    paper over here.
    """
    combined = (combined_text or "") + "\n---CONTEXT---\n" + (extra_context or "")
    return hashlib.sha256(combined.encode("utf-8", errors="replace")).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def get_cached(combined_text: str, extra_context: str = "") -> dict | None:
    """
    Returns the stored raw JSON dict for this exact document text +
    context combination, or None if nothing's cached yet (cache miss).

    Returns the RAW dict (same shape analyzer.py's json.loads(raw)
    produces) so the caller can pass it straight into
    CVAnalysis.from_json_dict() exactly as if it had just come back
    from the API — the cache is a transparent stand-in for the API
    call, not a different code path with its own parsing logic.
    """
    _ensure_cache_dir()
    key = hash_text(combined_text, extra_context)
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        return entry.get("data")
    except (json.JSONDecodeError, OSError):
        # A corrupted cache file should never crash the app or block
        # scoring — treat it as a miss and let analyzer.py fall through
        # to a fresh API call.
        return None


def store_result(combined_text: str, extra_context: str, data: dict) -> None:
    """
    Saves the raw JSON dict returned by the model, keyed by the hash of
    its input, so the next identical request can skip the API call
    entirely.

    data should be the same raw dict json.loads(raw) produced in
    analyzer.py — store it before any Python-side recomputation
    (weighted_overall etc.) happens, since that recomputation is cheap,
    deterministic, and already re-runs identically every time off
    rubric.py regardless of caching.
    """
    _ensure_cache_dir()
    key = hash_text(combined_text, extra_context)
    path = _cache_path(key)
    entry = {
        "cached_at": time.time(),
        "data": data,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2)
    except OSError as e:
        # Failing to write the cache should never block showing the
        # user their (freshly computed, still correct) result.
        print(f"Warning: could not write cache file: {e}")


def clear_cache() -> int:
    """
    Deletes all cached results. Returns the number of entries removed.
    Useful during development when you've changed the rubric/prompt and
    want fresh results instead of stale cached ones from before the
    change — a stale cache after a rubric edit would otherwise look
    exactly like "the app still gives the old wrong answer," which is
    confusing to debug if you forget the cache exists.
    """
    _ensure_cache_dir()
    removed = 0
    for fname in os.listdir(CACHE_DIR):
        if fname.endswith(".json"):
            try:
                os.remove(os.path.join(CACHE_DIR, fname))
                removed += 1
            except OSError:
                pass
    return removed

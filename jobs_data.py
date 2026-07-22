"""Job listing data source for JobSwiper.

Every caller downstream (the /api/jobs route, the draft-generation
prompt, the swiper UI) only ever consumes the plain dict shape returned
by get_jobs()/get_job_by_id() below -- never anything source-specific.

STRICTLY real listings only -- there is no mock/synthetic/placeholder
data anywhere in this module, on purpose, so the app can legitimately
promise every listing a user sees is a real one. Real listings come
from Jooble and/or JSearch (RapidAPI) when their API keys are
configured (JOOBLE_API_KEY / RAPIDAPI_KEY -- see .env.example);
results from both are merged, deduped, and cached in-process for
_LIVE_JOBS_TTL_SECONDS so a page of swipes doesn't re-hit either API on
every request. With neither key set, or if both sources fail (network
error, bad key, quota) and there's no still-fresh cache to fall back
on, get_jobs() returns an empty list -- the swiper's existing empty
state ("You're all caught up... check back later") is what a user
sees then, never a fake listing standing in for a real one.
"""

import json
import os
import time
import urllib.error
import urllib.request

JOOBLE_API_KEY = os.environ.get("JOOBLE_API_KEY", "").strip()
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()

# A handful of broad categories (not one all-encompassing query) so the
# swipe deck spans a realistic range of entry-level work, rather than
# skewing toward whatever one keyword happens to return the most
# results.
_LIVE_SEARCH_CATEGORIES = ["retail", "admin", "IT support", "hospitality"]

_LIVE_JOBS_TTL_SECONDS = 30 * 60
_live_jobs_cache = {"jobs": None, "fetched_at": 0.0}


def _strip_html(text):
    """Job snippets from real APIs often carry inline <b> highlight tags
    around matched keywords -- strip them so the swiper shows plain
    text instead of literal escaped tags (the frontend already runs
    escapeHtml() on this before inserting it, which is what would turn
    an un-stripped '<b>' into a visible "&lt;b&gt;")."""
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _fetch_jooble_jobs(keywords):
    if not JOOBLE_API_KEY:
        return []
    url = f"https://jooble.org/api/{JOOBLE_API_KEY}"
    body = json.dumps({"keywords": keywords, "location": "South Africa"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return []

    jobs = []
    for j in data.get("jobs", []):
        job_id = f"jooble-{j.get('id', '')}"
        if not j.get("id"):
            continue
        jobs.append({
            "id": job_id,
            "title": (j.get("title") or "").strip() or "Untitled role",
            "company": (j.get("company") or "").strip(),
            "location": (j.get("location") or "").strip(),
            "salary": (j.get("salary") or "").strip(),
            "description": _strip_html(j.get("snippet", ""))[:1200],
            "posted_at": (j.get("updated") or "")[:10],
            "email": "",
            "url": j.get("link") or "",
        })
    return jobs


def _fetch_jsearch_jobs(query):
    if not RAPIDAPI_KEY:
        return []
    import urllib.parse

    qs = urllib.parse.urlencode({
        "query": f"{query} jobs in South Africa",
        "page": "1",
        "num_pages": "1",
        "country": "za",
    })
    url = f"https://jsearch.p.rapidapi.com/search?{qs}"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return []

    jobs = []
    for j in data.get("data", []):
        job_id = j.get("job_id")
        if not job_id:
            continue
        location_parts = [p for p in (j.get("job_city"), j.get("job_state")) if p]
        salary = ""
        if j.get("job_min_salary") and j.get("job_max_salary"):
            currency = j.get("job_salary_currency") or ""
            salary = f"{currency} {j['job_min_salary']:,.0f} - {j['job_max_salary']:,.0f}".strip()
        jobs.append({
            "id": f"jsearch-{job_id}",
            "title": (j.get("job_title") or "").strip() or "Untitled role",
            "company": (j.get("employer_name") or "").strip(),
            "location": ", ".join(location_parts) or (j.get("job_country") or ""),
            "salary": salary,
            "description": _strip_html(j.get("job_description", ""))[:1200],
            "posted_at": (j.get("job_posted_at_datetime_utc") or "")[:10],
            "email": "",
            "url": j.get("job_apply_link") or "",
        })
    return jobs


def _fetch_live_jobs():
    """Merged, deduped listings from every configured real source, for
    one category at a time across _LIVE_SEARCH_CATEGORIES. Returns None
    (rather than an empty list) when no source is configured or every
    call failed, so _get_live_jobs_cached() can tell "no keys/all
    failed" apart from "a real search legitimately returned nothing"
    and keep serving the last good cache only in the former case."""
    if not JOOBLE_API_KEY and not RAPIDAPI_KEY:
        return None

    seen_ids = set()
    jobs = []
    any_call_succeeded = False
    for category in _LIVE_SEARCH_CATEGORIES:
        for fetch in (_fetch_jooble_jobs, _fetch_jsearch_jobs):
            try:
                results = fetch(category)
            except Exception:
                results = []
            if results:
                any_call_succeeded = True
            for job in results:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    jobs.append(job)

    return jobs if any_call_succeeded else None


def _get_live_jobs_cached():
    now = time.time()
    if _live_jobs_cache["jobs"] is not None and now - _live_jobs_cache["fetched_at"] < _LIVE_JOBS_TTL_SECONDS:
        return _live_jobs_cache["jobs"]

    fresh = _fetch_live_jobs()
    if fresh is not None:
        _live_jobs_cache["jobs"] = fresh
        _live_jobs_cache["fetched_at"] = now
        return fresh
    # A failed refresh keeps serving the last good (real) cache, even
    # if stale, rather than dropping back to nothing underneath a user
    # who was already looking at real listings a moment ago.
    return _live_jobs_cache["jobs"] or []


def _all_jobs():
    """Real listings only -- strictly. There is no synthetic/mock data
    anywhere in this module (deliberately deleted, not just unused) so
    there is no code path that can ever hand the swiper a fake listing.
    With no source configured, or every live call failing, this
    returns an empty list; the swiper's existing empty state ("You're
    all caught up... check back later") is what a user sees then --
    never a placeholder job standing in for a real one."""
    return _get_live_jobs_cached()


def get_jobs(exclude_ids=None):
    """All available listings, minus any the caller already knows about
    (hidden or already applied to)."""
    exclude_ids = exclude_ids or set()
    return [j for j in _all_jobs() if j["id"] not in exclude_ids]


def get_job_by_id(job_id):
    return next((j for j in _all_jobs() if j["id"] == job_id), None)

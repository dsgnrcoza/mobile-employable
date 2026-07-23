"""Job listing data source for JobSwiper.

Every caller downstream (the /api/jobs route, the draft-generation
prompt, the swiper UI) only ever consumes the plain dict shape returned
by get_jobs()/get_job_by_id() below -- never anything source-specific.

STRICTLY real listings only -- there is no mock/synthetic/placeholder
data anywhere in this module, on purpose, so the app can legitimately
promise every listing a user sees is a real one. Real listings come
from Jooble, JSearch (RapidAPI), Serper (Google Search results,
restricted to real SA job board domains), and Careerjet when their API
keys are configured (JOOBLE_API_KEY / RAPIDAPI_KEY / SERPER_API_KEY /
CAREERJET_API_KEY -- see .env.example); Jooble, JSearch, and Serper
results are merged, deduped, and cached in-process for
_LIVE_JOBS_TTL_SECONDS so a page of swipes doesn't re-hit any of them
on every request. With no key set, or if every source fails (network
error, bad key, quota) and there's no still-fresh cache to fall back
on, get_jobs() returns an empty list -- the swiper's existing empty
state ("You're all caught up... check back later") is what a user
sees then, never a fake listing standing in for a real one.

Careerjet is deliberately NOT folded into that shared cache -- its API
requires the real end user's IP and User-Agent on every call (it
attributes searches to the actual person who triggered them, core to
how its affiliate model works), so it's fetched live, per request, by
get_careerjet_jobs() below, called directly from the /api/jobs route
where that real request context still exists.
"""

import base64
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

JOOBLE_API_KEY = os.environ.get("JOOBLE_API_KEY", "").strip()
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
CAREERJET_API_KEY = os.environ.get("CAREERJET_API_KEY", "").strip()
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()

# A handful of broad categories (not one all-encompassing query) so the
# swipe deck spans a realistic range of entry-level work, rather than
# skewing toward whatever one keyword happens to return the most
# results.
_LIVE_SEARCH_CATEGORIES = ["retail", "admin", "IT support", "hospitality"]

_LIVE_JOBS_TTL_SECONDS = 30 * 60
_live_jobs_cache = {"jobs": None, "fetched_at": 0.0}

# Both APIs are asked to search South Africa specifically, but neither
# guarantees every result actually is one (JSearch's underlying sources
# occasionally mislabel a remote/international posting; Jooble's
# location text is a loose match, not a hard filter) -- so every result
# is independently re-checked against this list before it's ever shown.
# A job that can't be confirmed as South African is dropped rather than
# risk presenting a foreign listing as one of ours.
_SA_REGIONS = {
    "Gauteng": ["gauteng", "johannesburg", "joburg", "jozi", "pretoria", "centurion",
                "sandton", "midrand", "soweto", "randburg", "roodepoort", "benoni",
                "boksburg", "kempton park", "germiston", "vereeniging", "vanderbijlpark",
                "alberton", "krugersdorp"],
    "Western Cape": ["western cape", "cape town", "stellenbosch", "paarl", "george",
                      "worcester", "bellville", "somerset west", "mitchells plain",
                      "khayelitsha", "atlantis", "hermanus"],
    "KwaZulu-Natal": ["kwazulu-natal", "kwazulu natal", "kzn", "durban", "pietermaritzburg",
                       "umhlanga", "newcastle", "richards bay", "ballito", "pinetown"],
    "Eastern Cape": ["eastern cape", "port elizabeth", "gqeberha", "east london",
                      "uitenhage", "mthatha", "queenstown"],
    "Free State": ["free state", "bloemfontein", "welkom", "sasolburg"],
    "Limpopo": ["limpopo", "polokwane", "tzaneen", "mokopane", "thohoyandou"],
    "Mpumalanga": ["mpumalanga", "nelspruit", "mbombela", "witbank", "emalahleni", "secunda"],
    "North West": ["north west", "rustenburg", "potchefstroom", "klerksdorp", "mahikeng", "brits"],
    "Northern Cape": ["northern cape", "kimberley", "upington", "springbok", "kathu"],
}


def _sentence_case(text):
    """Normal sentence casing -- only the very first character capitalized,
    everything else lowercase -- regardless of how a source API cased a
    title (ALL CAPS, Title Case, etc.)."""
    text = (text or "").strip()
    return text[:1].upper() + text[1:].lower() if text else text


def _infer_region(location_text):
    """Best-effort South African province, from a free-text location
    string. Empty string when it can't be identified -- callers must not
    invent a region for a job that doesn't clearly name one."""
    text = (location_text or "").lower()
    for region, keywords in _SA_REGIONS.items():
        if any(kw in text for kw in keywords):
            return region
    return ""


def _is_confirmed_south_africa(location_text, country_code=None):
    """True only when there's real evidence a listing is South African --
    an explicit ZA country code, the words "South Africa" in its
    location, or a recognized SA city/province. Anything else (blank
    location, a foreign city, a country code that isn't ZA) is not
    treated as South African, so it never reaches the swiper mislabeled
    as a local listing."""
    if country_code and country_code.strip().upper() == "ZA":
        return True
    text = (location_text or "").lower()
    if "south africa" in text:
        return True
    return bool(_infer_region(text))


def _parse_zar_salary(salary_text):
    """Extracts (min, max) Rand figures from Jooble's free-text salary
    string, only when it actually looks Rand-denominated (an "R" prefix
    or the words rand/zar) -- returns (None, None) rather than guess at
    a currency the source never actually stated."""
    text = salary_text or ""
    if not re.search(r"\br\s?\d|rand|zar", text, re.IGNORECASE):
        return None, None
    values = []
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            values.append(float(n.replace(",", "")))
        except ValueError:
            pass
    return (min(values), max(values)) if values else (None, None)


def _strip_html(text):
    """Job snippets from real APIs often carry inline <b> highlight tags
    around matched keywords -- strip them so the swiper shows plain
    text instead of literal escaped tags (the frontend already runs
    escapeHtml() on this before inserting it, which is what would turn
    an un-stripped '<b>' into a visible "&lt;b&gt;")."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _fetch_jooble_jobs(keywords):
    if not JOOBLE_API_KEY:
        return []
    url = f"https://jooble.org/api/{JOOBLE_API_KEY}"
    body = json.dumps({"keywords": keywords, "location": "South Africa"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Jooble fetch failed for %r: HTTP %s %s -- %s", keywords, e.code, e.reason, e.read()[:300])
        return []
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        logger.warning("Jooble fetch failed for %r: %s", keywords, e)
        return []

    raw_jobs = data.get("jobs", [])
    jobs = []
    rejected_locations = []
    for j in raw_jobs:
        job_id = f"jooble-{j.get('id', '')}"
        if not j.get("id"):
            continue
        location = (j.get("location") or "").strip()
        if not _is_confirmed_south_africa(location):
            rejected_locations.append(location or "(blank)")
            continue
        salary_text = (j.get("salary") or "").strip()
        salary_min, salary_max = _parse_zar_salary(salary_text)
        jobs.append({
            "id": job_id,
            "title": _sentence_case((j.get("title") or "").strip()) or "Untitled role",
            "company": (j.get("company") or "").strip(),
            "location": location,
            "region": _infer_region(location),
            "salary": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "ZAR" if salary_min is not None else "",
            "description": _strip_html(j.get("snippet", ""))[:1200],
            "posted_at": (j.get("updated") or "")[:10],
            "email": "",
            "url": j.get("link") or "",
        })
    logger.warning(
        "Jooble %r: %d raw result(s), %d passed the South-Africa check%s",
        keywords, len(raw_jobs), len(jobs),
        f" -- rejected locations: {rejected_locations[:10]}" if rejected_locations else "",
    )
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
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("JSearch fetch failed for %r: HTTP %s %s -- %s", query, e.code, e.reason, e.read()[:300])
        return []
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        logger.warning("JSearch fetch failed for %r: %s", query, e)
        return []

    raw_jobs = data.get("data", [])
    jobs = []
    rejected_locations = []
    for j in raw_jobs:
        job_id = j.get("job_id")
        if not job_id:
            continue
        country_code = j.get("job_country") or ""
        location_parts = [p for p in (j.get("job_city"), j.get("job_state")) if p]
        # A raw ISO code ("ZA") is accurate but not a real place name --
        # once a country-code-only match has confirmed this is a South
        # African listing, show the country name, not the code, so nothing
        # displayed reads like unprocessed API leftovers.
        location = ", ".join(location_parts) or ("South Africa" if country_code.strip().upper() == "ZA" else country_code)
        if not _is_confirmed_south_africa(location, country_code=country_code):
            rejected_locations.append(f"{location or '(blank)'} [{country_code or '?'}]")
            continue
        salary = ""
        salary_min = float(j["job_min_salary"]) if j.get("job_min_salary") else None
        salary_max = float(j["job_max_salary"]) if j.get("job_max_salary") else None
        salary_currency = (j.get("job_salary_currency") or "").strip().upper()
        if salary_min is not None and salary_max is not None:
            salary = f"{salary_currency} {salary_min:,.0f} - {salary_max:,.0f}".strip()
        jobs.append({
            "id": f"jsearch-{job_id}",
            "title": _sentence_case((j.get("job_title") or "").strip()) or "Untitled role",
            "company": (j.get("employer_name") or "").strip(),
            "location": location,
            "region": _infer_region(location),
            "salary": salary,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
            "description": _strip_html(j.get("job_description", ""))[:1200],
            "posted_at": (j.get("job_posted_at_datetime_utc") or "")[:10],
            "email": "",
            "url": j.get("job_apply_link") or "",
        })
    logger.warning(
        "JSearch %r: %d raw result(s), %d passed the South-Africa check%s",
        query, len(raw_jobs), len(jobs),
        f" -- rejected locations: {rejected_locations[:10]}" if rejected_locations else "",
    )
    return jobs


# Real South African job boards Serper's query is restricted to via
# site: operators -- without this, a plain "X jobs South Africa" search
# mostly surfaces generic articles and aggregator category pages
# instead of pages that are actually individual job postings.
_SERPER_SA_JOB_SITES = ["careers24.com", "pnet.co.za", "careerjunction.co.za", "indeed.co.za"]
_SERPER_SITE_BRAND_NAMES = {"pnet", "careers24", "careerjunction", "career junction", "indeed", "career24"}


def _extract_role_and_company_from_title(title):
    """Best-effort (role, company) split of a job posting page's own
    <title> text, using the "<role> at <company>" and "<role> -/| <company>"
    conventions real job board pages commonly use. Returns ("", "")
    when nothing confident matches -- there's no reliable way to split
    a generic search-result title into role/company otherwise, and
    showing a wrong or made-up company would be worse than showing
    none."""
    for pattern in (
        r"^(?P<role>.+?)\s+at\s+(?P<company>[A-Za-z][\w&.'\- ]{1,60}?)(?:\s*[-|].*)?$",
        r"^(?P<role>.+?)\s*[-|]\s*(?P<company>[A-Za-z][\w&.'\- ]{1,60}?)(?:\s*[-|].*)?$",
    ):
        m = re.match(pattern, title)
        if not m:
            continue
        company = m.group("company").strip(" -|")
        role = m.group("role").strip(" -|")
        if not company or not role or company.lower() in _SERPER_SITE_BRAND_NAMES:
            continue
        if len(company.split()) > 8:
            continue
        # A "Role - X" title is just as often "Role - Location" as
        # "Role - Company" -- if the captured segment is itself a
        # recognized SA place name, it's a location, not an employer,
        # and mislabeling one as the other would be worse than not
        # showing a company at all.
        if _infer_region(company) or "south africa" in company.lower():
            continue
        return role, company
    return "", ""


def _fetch_serper_jobs(category):
    """Serper (Google Search results as JSON) is a fallback, lower-
    fidelity source -- it returns real web pages (title/snippet/link),
    not structured job records. Company is pulled from the page's own
    title via _extract_role_and_company_from_title(); a result with no
    confidently-identified employer is dropped rather than shown with
    "Company not listed", since this source is held to a stricter bar
    than the others (one real role at one real, named company). Salary
    is parsed from the snippet the same way Jooble's free-text salary
    is (_parse_zar_salary) -- present only when actually stated. The
    South-Africa check runs against the visible title+snippet text
    instead of a location field, since none exists here."""
    if not SERPER_API_KEY:
        return []
    site_filter = " OR ".join(f"site:{s}" for s in _SERPER_SA_JOB_SITES)
    query = f"{category} jobs South Africa ({site_filter})"
    body = json.dumps({"q": query, "gl": "za", "num": 10}).encode("utf-8")
    req = urllib.request.Request(
        "https://google.serper.dev/search", data=body,
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Serper fetch failed for %r: HTTP %s %s -- %s", category, e.code, e.reason, e.read()[:300])
        return []
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        logger.warning("Serper fetch failed for %r: %s", category, e)
        return []

    # Real signals that a result is a job board's search/category page
    # (e.g. "IT support jobs in South Africa" / "11165 results for...")
    # rather than one specific posting -- these get surfaced constantly
    # for broad category queries, and showing one as if it were a real
    # listing would itself be misleading information.
    _category_page_re = re.compile(r"\d[\d,]*\s+(?:results|jobs?|vacanc(?:y|ies))\b", re.IGNORECASE)
    _generic_title_re = re.compile(rf"^{re.escape(category.lower())}\s+jobs?\b.*south africa", re.IGNORECASE)

    raw_jobs = data.get("organic", [])
    jobs = []
    rejected = []
    for j in raw_jobs:
        link = j.get("link") or ""
        title = (j.get("title") or "").strip()
        snippet = (j.get("snippet") or "").strip()
        if not link or not title:
            continue
        if _category_page_re.search(snippet) or _generic_title_re.match(title.lower()):
            rejected.append(f"(category/listing page) {title[:60]}")
            continue
        text = f"{title} {snippet}"
        if not _is_confirmed_south_africa(text):
            rejected.append(title[:60])
            continue
        role, company = _extract_role_and_company_from_title(title)
        if not company:
            # A listing with no confidently-identified employer doesn't
            # meet the "real role at a real company" bar this source is
            # held to -- dropped rather than shown half-complete.
            rejected.append(f"(no confident company) {title[:60]}")
            continue
        salary_min, salary_max = _parse_zar_salary(snippet)
        salary_display = f"ZAR {salary_min:,.0f} - {salary_max:,.0f}" if salary_min is not None else ""
        region = _infer_region(text)
        jobs.append({
            "id": f"serper-{hashlib.md5(link.encode()).hexdigest()[:16]}",
            "title": _sentence_case(role),
            "company": company,
            "location": region or "South Africa",
            "region": region,
            "salary": salary_display,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "ZAR" if salary_min is not None else "",
            "description": snippet[:1200],
            "posted_at": (j.get("date") or "")[:10],
            "email": "",
            "url": link,
        })
    logger.warning(
        "Serper %r: %d raw result(s), %d passed the South-Africa check%s",
        category, len(raw_jobs), len(jobs),
        f" -- rejected titles: {rejected[:10]}" if rejected else "",
    )
    return jobs


def _fetch_careerjet_jobs(user_ip, user_agent, referer, keywords=""):
    """Careerjet's API requires user_ip/user_agent on every call -- it
    attributes each search to the real person who triggered it, which is
    why this isn't folded into the shared cache like the other two
    sources (see module docstring). locale_code=en_ZA plus an unset
    location asks for a country-wide South African search, per
    Careerjet's own docs ("location... when not specified, indicates
    country-wide search")."""
    if not CAREERJET_API_KEY:
        return []

    params = {
        "locale_code": "en_ZA",
        "keywords": keywords,
        "user_ip": user_ip or "0.0.0.0",
        "user_agent": user_agent or "Unknown",
        "page_size": "50",
    }
    url = "https://search.api.careerjet.net/v4/query?" + urllib.parse.urlencode(params)
    basic_auth = base64.b64encode(f"{CAREERJET_API_KEY}:".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/json",
        "Referer": referer or "",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Careerjet fetch failed: HTTP %s %s -- %s", e.code, e.reason, e.read()[:300])
        return []
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        logger.warning("Careerjet fetch failed: %s", e)
        return []

    raw_jobs = data.get("jobs", [])
    jobs = []
    rejected_locations = []
    for j in raw_jobs:
        job_url = j.get("url") or ""
        if not job_url:
            continue
        locations = j.get("locations")
        if isinstance(locations, list):
            location = ", ".join(str(p) for p in locations if p)
        else:
            location = (locations or j.get("location") or "").strip()
        if not _is_confirmed_south_africa(location):
            rejected_locations.append(location or "(blank)")
            continue
        salary_text = (j.get("salary") or "").strip()
        salary_min, salary_max = _parse_zar_salary(salary_text)
        jobs.append({
            "id": f"careerjet-{hashlib.md5(job_url.encode()).hexdigest()[:16]}",
            "title": _sentence_case((j.get("title") or "").strip()) or "Untitled role",
            "company": (j.get("company") or "").strip(),
            "location": location,
            "region": _infer_region(location),
            "salary": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "ZAR" if salary_min is not None else "",
            "description": _strip_html(j.get("description", ""))[:1200],
            "posted_at": (j.get("date") or "")[:10],
            "email": "",
            "url": job_url,
        })
    logger.warning(
        "Careerjet %r: %d raw result(s), %d passed the South-Africa check%s",
        keywords, len(raw_jobs), len(jobs),
        f" -- rejected locations: {rejected_locations[:10]}" if rejected_locations else "",
    )
    return jobs


def get_careerjet_jobs(user_ip, user_agent, referer, exclude_ids=None):
    """Live, uncached Careerjet listings for this one request -- never
    reuses another user's fetch, since the results are attributed to
    this specific user_ip/user_agent. Returns [] with no source
    configured or on any failure, same contract as get_jobs()."""
    exclude_ids = exclude_ids or set()
    try:
        jobs = _fetch_careerjet_jobs(user_ip, user_agent, referer)
    except Exception:
        logger.exception("Careerjet fetch raised unexpectedly")
        jobs = []
    return [j for j in jobs if j["id"] not in exclude_ids]


def _fetch_live_jobs():
    """Merged, deduped listings from every configured real source, across
    _LIVE_SEARCH_CATEGORIES. Every category/source combination is fetched
    concurrently (not one at a time) -- these are independent network
    calls, and running them serially could take longer than a serverless
    function is allowed to run, which would kill the request before any
    jobs ever came back. Returns None (rather than an empty list) when no
    source is configured or every call failed, so _get_live_jobs_cached()
    can tell "no keys/all failed" apart from "a real search legitimately
    returned nothing" and keep serving the last good cache only in the
    former case."""
    if not JOOBLE_API_KEY and not RAPIDAPI_KEY and not SERPER_API_KEY:
        logger.warning("No JOOBLE_API_KEY, RAPIDAPI_KEY, or SERPER_API_KEY configured -- JobSwiper has no real source to search.")
        return None

    calls = [
        (fetch, category)
        for category in _LIVE_SEARCH_CATEGORIES
        for fetch in (_fetch_jooble_jobs, _fetch_jsearch_jobs, _fetch_serper_jobs)
    ]

    seen_ids = set()
    jobs = []
    any_call_succeeded = False
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(calls))
    try:
        future_to_call = {pool.submit(fetch, category): (fetch, category) for fetch, category in calls}
        # A bounded wait, not as_completed()'s own timeout, so a single
        # hung call can't make this function raise -- anything still
        # running when the deadline hits is just treated as failed.
        done, not_done = concurrent.futures.wait(future_to_call, timeout=12)
        for future in not_done:
            fetch, category = future_to_call[future]
            logger.warning("%s timed out fetching %r", fetch.__name__, category)
        for future in done:
            fetch, category = future_to_call[future]
            try:
                results = future.result()
            except Exception:
                logger.exception("%s raised while fetching %r", fetch.__name__, category)
                results = []
            if results:
                any_call_succeeded = True
            for job in results:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    jobs.append(job)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if not any_call_succeeded:
        logger.warning("Every live job source returned nothing -- check that JOOBLE_API_KEY/RAPIDAPI_KEY/SERPER_API_KEY are valid.")
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

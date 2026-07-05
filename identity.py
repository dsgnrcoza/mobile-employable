"""
identity.py
-----------
Figures out WHOSE documents a batch of uploads belongs to, so the app
can refuse to mix one person's CV with another person's letter in the
same account.

This matters because the Cubic-Metric score is meant to describe one
real person. If two different people's documents end up in the same
profile, the score, the skills list, and the extracted name/email/
location all become meaningless — and there's no clean way to "undo"
that mixing after analysis has already blended the text together.

HOW IT WORKS (cheapest check first):
1. Filename heuristic — "Keanu_Reeves_CV.pdf" usually contains the
   name right there. Strip common document words (cv, resume, cover,
   letter, reference, certificate, etc.) and title-case what's left.
2. Content heuristic — look at the first few non-empty lines of
   extracted text for something that reads like "Name: ..." or a
   short, title-cased line near the top (the way a CV or letter
   header usually looks).
3. AI fallback — only used when neither heuristic finds a confident
   name for a given file, OR when the heuristics disagree with each
   other for the SAME file. A single small, cheap completion call
   asks the model to read the document text and name the person it
   most plausibly belongs to. This keeps cost and latency down: most
   well-named files never need it.

Names are then clustered with simple fuzzy matching (shared surname
token, or a high string-similarity ratio) so "Keanu Reeves",
"K. Reeves", and "Keanu_Reeves" all land in the same cluster, while
"Sibo Mthembu" lands in a separate one.

This is intentionally NOT a hard biometric or document-forensics
check — it's a reasonable-effort safeguard against the common case
(two people's files accidentally end up in one upload), not a
guarantee against deliberate fraud.
"""

import os
import re
import difflib

NOISE_WORDS = {
    "cv", "curriculum", "vitae", "resume", "resumé", "cover", "letter",
    "reference", "references", "certificate", "certificates", "qualification",
    "qualifications", "transcript", "id", "copy", "final", "updated", "new",
    "draft", "document", "doc", "docx", "pdf", "scan", "scanned", "v1", "v2",
    "version", "latest", "employable",
}

NAME_LINE_RE = re.compile(r"^\s*(?:name|full name|applicant)\s*[:\-]\s*(.+)$", re.IGNORECASE)
TITLE_CASE_LINE_RE = re.compile(r"^[A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3}$")


def _clean_tokens(raw: str) -> list[str]:
    raw = re.sub(r"[._\-]+", " ", raw)
    raw = re.sub(r"\(\d+\)", " ", raw)  # strip the "(1)" de-dupe suffix pipeline.py adds
    tokens = [t for t in re.split(r"\s+", raw.strip()) if t]
    return [t for t in tokens if t.lower() not in NOISE_WORDS and not t.isdigit()]


def guess_name_from_filename(filename: str) -> str | None:
    base = os.path.splitext(os.path.basename(filename or ""))[0]
    tokens = _clean_tokens(base)
    if 1 < len(tokens) <= 4 and all(re.match(r"^[A-Za-z'\-]+$", t) for t in tokens):
        return " ".join(t.capitalize() for t in tokens)
    return None


def guess_name_from_text(text: str) -> str | None:
    if not text:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()][:15]
    for line in lines:
        m = NAME_LINE_RE.match(line)
        if m:
            candidate = m.group(1).strip()
            if 1 < len(candidate.split()) <= 4:
                return candidate.title()
    # Fall back to a short, title-cased line near the top of the
    # document — typical of a CV/letter header — that isn't an
    # obvious section heading.
    skip_words = {"curriculum", "vitae", "resume", "cover", "letter", "profile", "summary", "contact"}
    for line in lines[:6]:
        if TITLE_CASE_LINE_RE.match(line) and not any(w in line.lower() for w in skip_words):
            return line.strip()
    return None


def _ai_guess_name(text: str) -> str | None:
    """
    Last-resort fallback: ask the model to read the document and name
    whose document it is. Only called when heuristics are inconclusive
    for a given file, to keep this cheap and rare.
    """
    try:
        import os as _os
        from openai import OpenAI

        api_key = _os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        client = OpenAI(api_key=api_key)
        snippet = (text or "")[:3000]
        if not snippet.strip():
            return None
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You identify whose personal document this is. Reply with ONLY the "
                        "person's full name as written in the document, or the single word "
                        "UNKNOWN if no clear name is present. No other text."
                    ),
                },
                {"role": "user", "content": snippet},
            ],
            max_tokens=20,
            temperature=0,
        )
        name = (response.choices[0].message.content or "").strip()
        if not name or name.upper() == "UNKNOWN" or len(name.split()) > 5:
            return None
        return name.title()
    except Exception:
        # Identity detection is a safeguard, not the core feature — if
        # the AI call fails for any reason (no key, network, quota),
        # we simply fall back to "unknown" rather than blocking the
        # whole upload.
        return None


def detect_document_identity(filename: str, text: str) -> str | None:
    """
    Best-effort guess at whose document this is. Tries cheap
    heuristics first; only calls the AI fallback when they disagree
    or come up empty.
    """
    from_name = guess_name_from_filename(filename)
    from_text = guess_name_from_text(text)

    if from_name and from_text:
        if names_are_same_person(from_name, from_text):
            return from_text  # content is the more reliable source when both agree
        # Heuristics disagree — let the AI break the tie.
        ai_name = _ai_guess_name(text)
        return ai_name or from_text
    if from_text:
        return from_text
    if from_name:
        return from_name
    return _ai_guess_name(text)


def normalize_name(name: str) -> list[str]:
    return [t.lower() for t in re.split(r"\s+", (name or "").strip()) if t]


def names_are_same_person(a: str, b: str) -> bool:
    """
    Fuzzy match: two names are treated as the same person if they
    share at least one meaningful token (first or last name) or if
    the full strings are highly similar (handles minor OCR/typo
    differences). Deliberately lenient about middle names/initials.
    """
    if not a or not b:
        return False
    tokens_a, tokens_b = set(normalize_name(a)), set(normalize_name(b))
    if not tokens_a or not tokens_b:
        return False
    shared = {t for t in tokens_a & tokens_b if len(t) > 1}
    if shared:
        return True
    ratio = difflib.SequenceMatcher(None, " ".join(tokens_a), " ".join(tokens_b)).ratio()
    return ratio >= 0.72


CV_FILENAME_HINTS = ("cv", "resume", "resumé", "curriculum")

CV_CONTENT_KEYWORDS = (
    "work experience", "employment history", "work history", "education",
    "professional summary", "career objective", "qualifications",
    "skills", "references available", "employment", "internship",
    "objective", "experience",
)


def _ai_classify_cv(text: str) -> bool:
    """
    Last-resort tie-breaker for looks_like_cv(): only reached when the
    filename and content heuristics are both inconclusive. Mirrors
    _ai_guess_name()'s fail-safe behaviour — any error just means "no".
    """
    try:
        import os as _os
        from openai import OpenAI

        api_key = _os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return False
        client = OpenAI(api_key=api_key)
        snippet = (text or "")[:3000]
        if not snippet.strip():
            return False
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You determine whether a document is a CV/resume (a document "
                        "summarizing a person's work experience, education, and skills, "
                        "written for job applications). Reply with ONLY YES or NO."
                    ),
                },
                {"role": "user", "content": snippet},
            ],
            max_tokens=3,
            temperature=0,
        )
        answer = (response.choices[0].message.content or "").strip().upper()
        return answer.startswith("Y")
    except Exception:
        return False


def looks_like_cv(filename: str, text: str) -> bool:
    """
    Best-effort check that an uploaded document is actually a CV/
    resume, used to gate the first step of onboarding. Same
    cheapest-first approach as detect_document_identity(): a filename
    hint decides it immediately; otherwise a keyword scan of the
    extracted text; the AI is only asked when neither is conclusive.
    """
    base = os.path.splitext(os.path.basename(filename or ""))[0].lower()
    if any(hint in base for hint in CV_FILENAME_HINTS):
        return True

    lowered = (text or "").lower()
    hits = sum(1 for kw in CV_CONTENT_KEYWORDS if kw in lowered)
    if hits >= 3:
        return True
    if hits == 0 and len(lowered.strip()) > 200:
        return False

    return _ai_classify_cv(text)


def cluster_identities(file_guesses: list[dict]) -> list[dict]:
    """
    Groups a list of {"document_id", "filename", "guessed_name"}
    dicts into clusters of "this is plausibly the same person".

    Returns a list of clusters:
        [{"name": "Keanu Reeves", "document_ids": [1, 2]}, ...]

    Files with no guessable name at all are placed into their own
    "Unknown" cluster rather than silently merged into someone else's,
    since merging on a blank guess is exactly the mistake this module
    exists to prevent.
    """
    clusters: list[dict] = []
    unknowns: list[int] = []

    for item in file_guesses:
        name = item.get("guessed_name")
        if not name:
            unknowns.append(item["document_id"])
            continue
        placed = False
        for cluster in clusters:
            if names_are_same_person(cluster["name"], name):
                cluster["document_ids"].append(item["document_id"])
                placed = True
                break
        if not placed:
            clusters.append({"name": name, "document_ids": [item["document_id"]]})

    if unknowns:
        clusters.append({"name": "Unknown", "document_ids": unknowns})

    return clusters

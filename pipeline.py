"""
pipeline.py
-----------
Glue between an uploaded file, the extraction/analysis logic that was
already written (extract.py, analyzer.py, rubric.py), and per-user
storage in the database (db.py).

This is the one new piece of "business logic" the web app needed that
didn't exist in any form before — the desktop app called extract.py
and analyzer.py directly from Tkinter button-click handlers. Here, the
same two functions get called from a Flask route instead, with the
results saved under the logged-in user's id rather than just shown in
a window.
"""

import base64
import calendar
import dataclasses
import json
import os
from datetime import datetime, timedelta, timezone

import extract
import analyzer
import identity
import db
from rubric import score_skill_set, weighted_overall, label_for_score, stars_for_score, get_skill_market_value, mechanical_ats_check

PLAN_NAME = "Employable Pro"
PLAN_PRICE_ZAR = 149
TRIAL_DAYS = 3


def _add_one_month(dt):
    """
    Same day-of-month, one month later — clamped to the last day of the
    target month when the anchor day doesn't exist there (e.g. the 31st
    anchored against a 30-day month lands on the 30th, not next month).
    """
    year = dt.year + (dt.month // 12)
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _compute_subscription_info(user: dict) -> dict:
    """
    Everything the Subscription screen needs, derived entirely from the
    account's created_at timestamp (no separate "subscription started"
    field exists yet — signup date IS the trial start). The next
    billing date is always the same calendar day-of-month as signup,
    rolled forward month by month until it's in the future — this is
    what "billed on the 9th every month" actually means once the 9th
    for this cycle has already passed.
    """
    created_at_str = user.get("created_at")
    if not created_at_str:
        return None
    try:
        created_at = datetime.fromisoformat(created_at_str)
    except ValueError:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    trial_end = created_at + timedelta(days=TRIAL_DAYS)
    in_trial = now < trial_end

    next_billing = _add_one_month(created_at)
    while next_billing <= now:
        next_billing = _add_one_month(next_billing)

    return {
        "plan_name": PLAN_NAME,
        "price_zar": PLAN_PRICE_ZAR,
        "trial_days": TRIAL_DAYS,
        "status": "trial" if in_trial else "active",
        "billing_day": created_at.day,
        "trial_end": trial_end.strftime("%-d %B %Y") if os.name != "nt" else trial_end.strftime("%d %B %Y").lstrip("0"),
        "next_billing_date": next_billing.strftime("%-d %B %Y") if os.name != "nt" else next_billing.strftime("%d %B %Y").lstrip("0"),
    }

# On Vercel the local filesystem is ephemeral — use /tmp so uploads can
# at least be processed within the same request. Content is stored in the
# database so re-analysis works without the original file being present.
if os.environ.get("VERCEL"):
    UPLOAD_ROOT = "/tmp/employable_uploads"
else:
    UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")


def user_upload_dir(user_id: int) -> str:
    """
    Every user gets their own subfolder under uploads/<user_id>/ so
    files from different accounts are never mixed on disk, mirroring
    how the database rows are scoped by user_id.
    """
    path = os.path.join(UPLOAD_ROOT, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def save_uploaded_file(user_id: int, file_storage, category: str = "") -> dict:
    """
    Saves one Werkzeug FileStorage (from a Flask request.files entry)
    to this user's folder and records it in the documents table.
    Returns the new document row as a dict.

    `category` is only meaningful during onboarding ("cv" or
    "supporting") so the two onboarding steps can each check their own
    slice of documents without mixing them up. Uploads made from the
    dashboard after onboarding leave it blank.
    """
    filename = _safe_filename(file_storage.filename)
    dest_dir = user_upload_dir(user_id)
    dest_path = os.path.join(dest_dir, filename)

    # Avoid silently overwriting a same-named file from an earlier
    # upload — append a numeric suffix instead, e.g. cv(1).pdf.
    base, ext = os.path.splitext(dest_path)
    counter = 1
    while os.path.exists(dest_path):
        dest_path = f"{base}({counter}){ext}"
        counter += 1

    file_storage.save(dest_path)
    file_type = os.path.splitext(filename)[1].lower().lstrip(".")
    file_size = os.path.getsize(dest_path)

    # Extract text immediately and store in DB so re-analysis never needs the file on disk.
    content = ""
    try:
        content = extract.extract_text(dest_path) or ""
    except Exception:
        pass

    # Also store the original bytes in the DB (base64) -- Vercel's
    # serverless filesystem doesn't reliably persist disk writes across
    # invocations, so `dest_path` alone isn't durable storage. This is
    # the actual source of truth for re-downloading/re-parsing later;
    # the disk copy is just a same-request convenience.
    file_bytes_b64 = None
    try:
        with open(dest_path, "rb") as f:
            file_bytes_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception:
        pass

    doc_id = db.add_document(user_id, filename, dest_path, file_type, content, category, file_size, file_bytes_b64)
    return {
        "id": doc_id,
        "filename": filename,
        "stored_path": dest_path,
        "file_type": file_type,
        "content": content,
        "category": category,
        "file_size": file_size,
    }


def _safe_filename(filename: str) -> str:
    """
    Strips directory separators and other path-traversal characters so
    an uploaded filename like '../../etc/passwd' can never escape the
    per-user upload folder. This is a minimal allowlist-style clean,
    not a full normalization library, but it's enough to guarantee the
    saved path always stays inside dest_dir.
    """
    filename = os.path.basename(filename or "upload")
    safe = "".join(c for c in filename if c.isalnum() or c in "._- ")
    return safe or "upload"


def guess_identities_for_documents(document_records: list[dict]) -> list[dict]:
    """
    For each document row (must have stored_path/filename/id), extract
    its text and guess whose document it is. Returns a list of
    {"document_id", "filename", "guessed_name"} ready for
    identity.cluster_identities().
    """
    results = []
    for doc in document_records:
        stored = (doc.get("content") or "").strip()
        if stored:
            text = stored
        elif doc.get("stored_path") and os.path.exists(doc["stored_path"]):
            text = extract.extract_text(doc["stored_path"]) or ""
        else:
            text = ""
        guessed = identity.detect_document_identity(doc["filename"], text)
        results.append({"document_id": doc["id"], "filename": doc["filename"], "guessed_name": guessed})
    return results


def check_identity_conflict(user_id: int) -> dict:
    """
    Looks at every document currently on file for this user (used
    during onboarding, before anything is confirmed) and reports
    whether they plausibly belong to more than one person.

    Returns:
        {"conflict": False, "name": "Keanu Reeves"}                  -- single person, or no documents
        {"conflict": True, "clusters": [{"name": ..., "documents": [...]}]} -- needs a user decision
    """
    documents = db.get_documents_for_user(user_id)
    if not documents:
        return {"conflict": False, "name": ""}

    guesses = guess_identities_for_documents(documents)
    clusters = identity.cluster_identities(guesses)

    # A single cluster (or a single cluster plus nothing else) means
    # everything plausibly belongs to one person — no decision needed.
    # An "Unknown" cluster on its own (every file unguessable) is also
    # not a conflict; we just can't name the owner yet.
    named_clusters = [c for c in clusters if c["name"] != "Unknown"]
    if len(named_clusters) <= 1:
        name = named_clusters[0]["name"] if named_clusters else ""
        return {"conflict": False, "name": name}

    doc_by_id = {d["id"]: d for d in documents}
    return {
        "conflict": True,
        "clusters": [
            {
                "name": c["name"],
                "documents": [
                    {"id": did, "filename": doc_by_id[did]["filename"]}
                    for did in c["document_ids"]
                    if did in doc_by_id
                ],
            }
            for c in clusters
        ],
    }


def any_document_looks_like_cv(user_id: int) -> bool:
    """
    Used to gate onboarding's first step: true once at least one
    document uploaded there (category == "cv") actually reads like a
    CV/resume, per identity.looks_like_cv(). Only documents from the
    CV step are checked — a supporting-document upload in step two
    should never retroactively satisfy this.
    """
    documents = db.get_documents_for_user(user_id)
    for doc in documents:
        if doc.get("category") != "cv":
            continue
        text = (doc.get("content") or "").strip()
        if identity.looks_like_cv(doc["filename"], text):
            return True
    return False


def resolve_identity_conflict(user_id: int, keep_document_ids: list[int]) -> None:
    """
    Called once the user has picked which person they are during
    onboarding. Deletes every document NOT in keep_document_ids, so
    only one person's files remain on this account.
    """
    documents = db.get_documents_for_user(user_id)
    keep_set = set(keep_document_ids)
    for doc in documents:
        if doc["id"] not in keep_set:
            db.delete_document(user_id, doc["id"])


def matches_confirmed_owner(user_id: int, filename: str, text: str) -> bool:
    """
    Used for uploads made AFTER onboarding is complete: checks a new
    document against the name already confirmed for this account. If
    we can't form a confident guess at all, we don't block the
    upload — the safeguard is for clear mismatches, not every file
    with an unreadable header.
    """
    user = db.get_user_by_id(user_id)
    confirmed_name = (user or {}).get("confirmed_owner_name") or ""
    if not confirmed_name:
        return True
    guessed = identity.detect_document_identity(filename, text)
    if not guessed:
        return True
    return identity.names_are_same_person(confirmed_name, guessed)



def run_analysis_for_user(user_id: int, extra_context: str = "") -> dict:
    """
    Re-extracts text from every document this user has uploaded,
    sends it to the Employability Rating Engine (analyzer.py,
    unchanged from the desktop app), stores the result, syncs the
    AI-detected skills into the skills table, and returns the result
    as a plain dict ready to be sent to the frontend as JSON.

    Raises analyzer.CVAnalyzerError on any failure (missing API key,
    no readable text, bad API response) — the Flask route is
    responsible for turning that into a user-facing error message.
    """
    documents = db.get_documents_for_user(user_id)
    if not documents:
        raise analyzer.CVAnalyzerError("Please upload at least one document first.")

    # Prefer stored content from the database (works on Vercel where the
    # upload directory is ephemeral). Fall back to re-extracting from disk
    # if content is empty and the file still exists locally.
    chunks = []
    for doc in documents:
        stored_content = (doc.get("content") or "").strip()
        if stored_content:
            chunks.append(f"=== FILE: {doc['filename']} ===\n{stored_content}")
        elif doc.get("stored_path") and os.path.exists(doc["stored_path"]):
            text = extract.extract_text(doc["stored_path"]) or "[No text extracted]"
            chunks.append(f"=== FILE: {doc['filename']} ===\n{text}")
        else:
            chunks.append(f"=== FILE: {doc['filename']} ===\n[File content unavailable]")
    combined_text = "\n\n".join(chunks) if chunks else ""

    analysis: analyzer.CVAnalysis = analyzer.analyze_documents(combined_text, extra_context)

    result_dict = dataclasses.asdict(analysis)
    db.save_analysis(user_id, json.dumps(result_dict))

    # Sync AI-detected skills into the skills table without touching
    # any skill the user typed in manually (see db.replace_ai_skills).
    db.replace_ai_skills(user_id, analysis.skills)

    # If this is the user's first analysis, seed their profile fields
    # (name/headline/email/location) from what the AI found — but
    # never overwrite fields the user has already filled in
    # themselves on a later run.
    existing_user = db.get_user_by_id(user_id)
    profile_updates = {}
    if not existing_user.get("full_name") and analysis.full_name and analysis.full_name != "Job Seeker":
        profile_updates["full_name"] = analysis.full_name
    if not existing_user.get("headline") and analysis.headline and analysis.headline != "Aspiring Professional":
        profile_updates["headline"] = analysis.headline
    if not existing_user.get("email") and analysis.email:
        profile_updates["email"] = analysis.email
    if not existing_user.get("location") and analysis.location:
        profile_updates["location"] = analysis.location
    if profile_updates:
        db.update_profile_fields(user_id, **profile_updates)

    # Save score snapshot to history
    try:
        dim_scores = {d["label"]: d["score"] for d in result_dict.get("dimensions", []) if "label" in d}
        if dim_scores:
            db.save_score_history(user_id, result_dict.get("overall_rating", 0), dim_scores)
    except Exception:
        pass  # history saving must never break the main flow

    return result_dict


def _apply_dynamic_skill_strength(analysis_data: dict, skill_rows: list) -> dict:
    """
    Replaces the AI-assigned Skill Strength score with the Python-computed
    deterministic value from rubric.score_skill_set(), then recomputes the
    overall score, star rating, and rating label to match.

    This guarantees:
      - Empty skill list → Skill Strength = exactly 0.0
      - High-demand skills (Python, AWS) move the score far more than
        commodity skills (Typing, Filing)
      - Adding then removing the same skill perfectly reverses the change
      - Score is deterministic: same skills → same number every time
      - Skill add/remove instantly reflects without re-running AI analysis

    The AI's Skill Strength score (from the ensemble runs) is discarded for
    display purposes but kept intact in the raw stored JSON — this function
    works on a copy so the stored analysis is never mutated.
    """
    import copy
    data = copy.deepcopy(analysis_data)

    skill_labels = [s["label"] for s in skill_rows]
    # Skills the AI found actually demonstrated in a job description or
    # achievement (not just listed in a skills section) earn the evidence
    # multiplier in score_skill_set() — this was previously computed by
    # the AI and stored in extracted_facts but never actually passed
    # through here, so the evidence bonus never had any effect.
    evidenced_labels = set(
        (data.get("extracted_facts") or {}).get("skills_evidenced_in_work_history") or []
    )
    python_skill_score = score_skill_set(skill_labels, evidenced_labels=evidenced_labels)

    dimensions = data.get("dimensions") or []
    for dim in dimensions:
        if dim.get("label") == "Skill Strength":
            dim["score"] = python_skill_score
            evidenced_count = sum(1 for l in skill_labels if l.lower().strip() in {e.lower().strip() for e in evidenced_labels})
            raw_power = sum(
                get_skill_market_value(l) * (1.4 if l.lower().strip() in {e.lower().strip() for e in evidenced_labels} else 1.0)
                for l in skill_labels
            )
            # Overwrite "description" (not just an unused side field) since
            # that's what the dashboard actually displays — otherwise the
            # AI's original, now-stale reasoning stays shown next to a
            # score that no longer matches it.
            dim["description"] = (
                f"Computed deterministically from {len(skill_labels)} skill(s) "
                f"using market-demand weighting ({evidenced_count} backed by evidence "
                f"in your work history, scored higher). "
                f"Raw power = {raw_power:.2f}. "
                f"Score = 10 × (1 − exp(−raw_power / 7.0)) = {python_skill_score}."
            ) if skill_labels else (
                "No skills listed. Skill Strength is exactly 0.0 — "
                "add skills to your profile to increase this score."
            )
            dim["simple_explanation"] = (
                f"Think of it like a game score: every skill you've told us about is "
                f"worth some points, and skills companies want a lot right now (like AI "
                f"or cloud computing) are worth way more points than basic ones (like "
                f"typing). You've got {len(skill_labels)} skill(s) listed, and that adds "
                f"up to {python_skill_score} points out of 10 so far. Tell us about more "
                f"in-demand skills — especially ones you've actually used at a real job — "
                f"and the number goes up."
            ) if skill_labels else (
                "This is a zero right now simply because we don't know any of your "
                "skills yet — we can't hand out points for something we can't see. "
                "Add your skills and watch this number jump up."
            )
            break

    dim_scores = {d["label"]: d["score"] for d in dimensions if "label" in d and "score" in d}
    if dim_scores:
        new_overall = weighted_overall(dim_scores)
        data["overall_rating"] = new_overall
        data["star_rating"] = stars_for_score(new_overall)
        data["rating_label"] = label_for_score(new_overall)

    return data


def _apply_mechanical_ats(analysis_data: dict, skill_labels: list, extracted_text: str = "") -> dict:
    """
    Overrides the AI's ATS Compatibility score with a mechanically computed value
    based on actual parsing of the extracted text. Blends 70% mechanical + 30% AI
    so the AI's context knowledge still has some influence but the objective check dominates.
    """
    import copy
    data = copy.deepcopy(analysis_data)
    result = mechanical_ats_check(extracted_text, skill_labels)
    mechanical_score = result["score"]
    findings = result["findings"]

    dimensions = data.get("dimensions") or []
    for dim in dimensions:
        if dim.get("label") == "ATS Compatibility":
            ai_score = float(dim.get("score", 5.0))
            blended = round(0.70 * mechanical_score + 0.30 * ai_score, 1)
            dim["score"] = blended
            dim["ats_findings"] = findings
            # This is a 70/30 blend, not a full override like Skill
            # Strength, so the AI's own read is still worth keeping —
            # but the mechanical findings are what actually drove most
            # of the number, so fold them into the visible description
            # rather than leaving it purely as the AI's pre-blend text.
            ai_description = (dim.get("description") or "").strip()
            mechanical_summary = "; ".join(findings)
            dim["description"] = (
                f"{ai_description} Mechanical check: {mechanical_summary}."
                if ai_description else f"Mechanical check: {mechanical_summary}."
            )
            dim["simple_explanation"] = (
                "Picture a robot reading your CV instead of a person — that's what "
                "this score is about. If your CV is laid out simply, with clear "
                "headings and dates written plainly, the robot reads it just fine. "
                "If it's full of fancy tables, columns, or pictures instead of text, "
                "the robot gets confused and might toss your CV out before any human "
                "even sees it."
            )
            break

    dim_scores = {d["label"]: d["score"] for d in dimensions if "label" in d and "score" in d}
    if dim_scores:
        new_overall = weighted_overall(dim_scores)
        data["overall_rating"] = new_overall
        data["star_rating"] = stars_for_score(new_overall)
        data["rating_label"] = label_for_score(new_overall)

    return data


# The dashboard only ever displays these 5 of the backend's 8 dimensions
# (see static/js/dashboard.js's METRICS array) — Qualification Strength,
# Evidence Credibility, and Career Progression are computed but never
# shown as a bar anywhere.
DASHBOARD_VISIBLE_DIMENSIONS = [
    "Documentation Strength",
    "Experience Strength",
    "Skill Strength",
    "Market Competitiveness",
    "ATS Compatibility",
]

# The 5 dimensions the dashboard's primary cards show, and the only 5
# averaged into the headline "Employability Score" below -- deliberately
# a different, smaller set than DASHBOARD_VISIBLE_DIMENSIONS above,
# which is unrelated and keeps driving the roadmap's own points math
# untouched. All 8 dimensions are still fully computed either way; this
# only decides which 5 feed the new headline number and primary cards.
PRIMARY_SCORE_DIMENSIONS = [
    "ATS Compatibility",
    "Skill Strength",
    "Experience Strength",
    "Qualification Strength",
    "Market Competitiveness",
]


def _add_employability_score(analysis_data: dict) -> dict:
    """
    Adds `employability_score` (plus its own label/star rating) as new,
    additive fields on the analysis dict -- a simple average of only
    the 5 PRIMARY_SCORE_DIMENSIONS. This is distinct from the existing
    `overall_rating` (the weighted average across all 8 dimensions),
    which is left completely untouched and keeps backing score history,
    the AI chat context, and the Full Breakdown exactly as before.
    """
    dim_scores = {d["label"]: d.get("score", 0) for d in (analysis_data.get("dimensions") or [])}
    if not all(label in dim_scores for label in PRIMARY_SCORE_DIMENSIONS):
        return analysis_data
    primary_scores = [float(dim_scores[label] or 0) for label in PRIMARY_SCORE_DIMENSIONS]
    employability_score = round(sum(primary_scores) / len(primary_scores), 2)
    analysis_data["employability_score"] = employability_score
    analysis_data["employability_score_label"] = label_for_score(employability_score)
    analysis_data["employability_score_stars"] = stars_for_score(employability_score)
    return analysis_data


def _filter_and_recompute_roadmap(analysis_data: dict) -> dict:
    """
    Fixes the roadmap "points don't match the bars" problem at its
    root, which is actually two separate mismatches:

    1. A roadmap item can target any of the 8 backend dimensions, but
       only 5 are ever shown on the dashboard. An item aimed at
       Qualification Strength, Evidence Credibility, or Career
       Progression can never visibly move anything the user can see,
       so showing "+points" for it is just misleading — those items
       are dropped entirely here.

    2. Even for the 5 visible dimensions, the AI's own
       projected_score_gain is an estimate against the full 8-dimension
       *weighted* rubric (rubric.weighted_overall) — a completely
       different formula from the one the gauge/bars actually use
       (dashboard.js sums (score / 10) * 2 per visible dimension, capped
       at 2 points each). Those two numbers were never going to agree.
       This recomputes each item's displayed value using the EXACT
       formula the dashboard uses: a dimension currently at score S has
       (2 - (S/10)*2) points of "headroom" left in the visible total,
       full stop — that is the only number that can ever be honest
       here. When several roadmap items target the same dimension, that
       headroom is split across them in proportion to the AI's own
       relative ranking (items it judged more impactful get a bigger
       slice of the headroom), so the numbers stay meaningful while
       their sum can never exceed what that bar actually has left to
       give — completing every item for a dimension can never add up
       to more than that dimension's real remaining room to grow.
    """
    import copy
    data = copy.deepcopy(analysis_data)

    dim_scores = {d["label"]: d.get("score", 0) for d in (data.get("dimensions") or [])}
    roadmap = data.get("improvement_roadmap") or []

    visible_items = [item for item in roadmap if item.get("dimension") in DASHBOARD_VISIBLE_DIMENSIONS]

    by_dimension = {}
    for item in visible_items:
        by_dimension.setdefault(item["dimension"], []).append(item)

    for dimension, items in by_dimension.items():
        score = float(dim_scores.get(dimension, 0) or 0)
        headroom = round(max(0.0, 2.0 - (score / 10.0) * 2.0), 2)
        raw_gains = [max(0.0, float(item.get("projected_score_gain") or 0)) for item in items]
        total_raw = sum(raw_gains)
        for item, raw in zip(items, raw_gains):
            if total_raw > 0:
                item["projected_score_gain"] = round(headroom * (raw / total_raw), 2)
            else:
                item["projected_score_gain"] = round(headroom / len(items), 2)

    data["improvement_roadmap"] = visible_items
    return data


def get_dashboard_state(user_id: int) -> dict:
    """
    Assembles everything the dashboard page needs in one call: profile
    fields, skills (manual + AI, in display order), the most recent
    analysis (if any), and job application count. This is what gets
    serialized to JSON for the frontend to render, and what gets
    re-sent after any change (new upload, skill add/delete, re-run)
    so the Cubic-Metric bars can update live without a full page
    reload.
    """
    user = db.get_user_by_id(user_id)
    skills = db.get_skills_for_user(user_id)
    latest = db.get_latest_analysis(user_id)
    applications = db.get_applications_for_user(user_id)
    documents = db.get_documents_for_user(user_id)

    analysis_data = json.loads(latest["result_json"]) if latest else None

    if analysis_data is not None:
        analysis_data = _apply_dynamic_skill_strength(analysis_data, skills)

    if analysis_data is not None and documents:
        try:
            chunks = []
            for doc in documents:
                sc = (doc.get("content") or "").strip()
                if sc:
                    chunks.append(sc)
                elif doc.get("stored_path") and os.path.exists(doc["stored_path"]):
                    chunks.append(extract.extract_text(doc["stored_path"]) or "")
            combined_text = "\n\n".join(chunks)
            skill_labels = [s["label"] for s in skills]
            analysis_data = _apply_mechanical_ats(analysis_data, skill_labels, combined_text)
        except Exception:
            pass  # ATS override must never break the main flow

    if analysis_data is not None:
        try:
            analysis_data = _filter_and_recompute_roadmap(analysis_data)
        except Exception:
            pass  # roadmap recompute must never break the main flow

    if analysis_data is not None:
        try:
            analysis_data = _add_employability_score(analysis_data)
        except Exception:
            pass  # must never break the main flow

    avatar_path = user.get("avatar_path") or ""
    if avatar_path.startswith("data:"):
        avatar_url = avatar_path
    elif avatar_path:
        avatar_url = f"/static/{avatar_path}"
    else:
        avatar_url = ""

    return {
        "profile": {
            "full_name": user.get("full_name") or "",
            "headline": user.get("headline") or "",
            "email": user.get("email") or "",
            "location": user.get("location") or "",
            "phone": user.get("phone") or "",
            "username": user.get("username"),
            "avatar_url": avatar_url,
            "target_field": user.get("target_field") or "",
        },
        "skills": [{"id": s["id"], "label": s["label"], "source": s["source"]} for s in skills],
        "documents": [
            {
                "id": d["id"],
                "filename": d["filename"],
                "file_type": d["file_type"],
                "file_size": d.get("file_size"),
                "uploaded_at": d.get("uploaded_at"),
            }
            for d in documents
        ],
        "analysis": analysis_data,
        "applications_count": len(applications),
        "applications": [
            {
                "id": a["id"],
                "job_title": a["job_title"],
                "company": a["company"],
                "status": a["status"],
            }
            for a in applications
        ],
        "score_history": db.get_score_history(user_id, limit=20),
        "subscription": _compute_subscription_info(user),
        "pending_friend_request_count": db.count_pending_incoming_requests(user_id),
    }

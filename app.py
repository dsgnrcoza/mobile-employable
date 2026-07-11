"""
app.py
------
The Flask web application. Routes for:
  - landing/marketing page
  - signup, login, logout
  - forgot-password (security question flow)
  - the dashboard (profile, skills, documents, Cubic-Metric breakdown)
  - JSON API endpoints the dashboard's JavaScript calls to upload
    documents, add/delete skills, and re-run analysis — all WITHOUT a
    full page reload, which is what makes the Cubic-Metric bars feel
    "live."

HOW THE LIVE-UPDATE BEHAVIOR WORKS:
The dashboard page itself is rendered once, server-side, with whatever
data exists at page-load. After that, every action that could change
the Cubic-Metric numbers — uploading a document, adding a skill,
deleting a skill, re-running analysis — is a fetch() call from
static/js/dashboard.js to one of the /api/... routes below. Each of
those routes returns the SAME shape of JSON (see
pipeline.get_dashboard_state), and the JavaScript re-renders the
skills list and the dimension bars from whatever comes back. The
server is the single source of truth; the page never trusts its own
old state once a change has been made.
"""

import os
import uuid
import json
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, stream_with_context
import db
import auth
import pipeline
import analyzer
import extract
import identity
import cache as cache_module

load_dotenv()  # reads OPENAI_API_KEY and FLASK_SECRET_KEY from a local .env file

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB upload cap per request

# One database connection is opened per request (on first use) and reused
# by every db.* call for the rest of that request, instead of each call
# opening its own -- this hook is what actually closes that shared
# connection once the response is done.
app.teardown_appcontext(db.close_db)


@app.after_request
def _cache_static_assets(response):
    # Static files (CSS/JS/icons) currently answer every single request
    # with Cache-Control: no-cache, forcing a network round-trip on every
    # page load just to get a 304. An hour of real caching lets repeat
    # loads skip the network entirely while still picking up a deploy
    # within an hour -- ETag-based revalidation (unchanged, still present)
    # keeps correctness beyond that window.
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "jpg", "jpeg", "png", "tiff", "tif"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
AVATAR_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "avatars")


def _allowed_file(filename: str) -> bool:
    if not filename or not filename.strip() or "." not in filename:
        return False
    return filename.rsplit(".", 1)[-1].lower() in ALLOWED_EXTENSIONS


_db_initialized = False


@app.before_request
def _ensure_db():
    # init_db() is idempotent, but it still opens a connection and runs
    # a dozen-plus CREATE TABLE / ALTER TABLE statements — over a real
    # network connection to Postgres, doing that on every single
    # request (every page, every API call) adds real, avoidable latency
    # to everything the app does. The schema can't change mid-process,
    # so once is enough per running instance; a fresh serverless cold
    # start still gets its own fresh check via this same flag reset to
    # False when the process starts.
    global _db_initialized
    if not _db_initialized:
        db.init_db()
        _db_initialized = True


# Endpoints reachable even before a user has accepted the terms
# gate — everything else under login gets redirected to /terms until
# that's done. This check runs before the onboarding gate below, so a
# brand-new account always sees terms first, then onboarding.
_TERMS_EXEMPT_ENDPOINTS = {
    "terms_page",
    "api_disclaimer_accept",
    "api_delete_account",
    "logout_page",
    "static",
    "service_worker",
    "android_asset_links",
    "privacy_policy",
    "landing",
}


@app.before_request
def _require_terms():
    user = auth.current_user()
    if not user:
        return  # not logged in — normal login_required handling applies elsewhere
    if user.get("disclaimer_accepted"):
        return
    if request.endpoint in _TERMS_EXEMPT_ENDPOINTS:
        return
    return redirect(url_for("terms_page"))


# Endpoints reachable even before a user has confirmed their
# documents — everything else under login gets redirected to
# /onboarding until that's done. Static assets and public/auth pages
# are excluded too since they're not behind login_required anyway.
_ONBOARDING_EXEMPT_ENDPOINTS = {
    "onboarding_page",
    "api_onboarding_readout",
    "api_onboarding_upload",
    "api_onboarding_check",
    "api_onboarding_confirm",
    "api_onboarding_remove_document",
    "api_delete_account",
    "logout_page",
    "static",
    "service_worker",
    "android_asset_links",
    "privacy_policy",
    "terms_page",
    "api_disclaimer_accept",
    # The splash screen must always get a chance to render before any
    # further redirect happens — it hands the actual "where next"
    # decision to /dashboard itself, one hop later.
    "landing",
}


@app.before_request
def _require_onboarding():
    user = auth.current_user()
    if not user:
        return  # not logged in — normal login_required handling applies elsewhere
    if user.get("documents_confirmed"):
        return
    if request.endpoint in _ONBOARDING_EXEMPT_ENDPOINTS:
        return
    return redirect(url_for("onboarding_page"))


@app.context_processor
def inject_user():
    return {"current_user": auth.current_user()}


# ---------------- PUBLIC PAGES ----------------

@app.route("/sw.js")
def service_worker():
    # Served from the root (not /static/sw.js) so its default scope is
    # "/" instead of "/static/" — a service worker can only ever control
    # pages under the directory it's served from, and the PWA install
    # prompt (beforeinstallprompt) won't fire unless it actually
    # controls the page the user is on (/dashboard, etc).
    from flask import send_from_directory
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")


@app.route("/.well-known/assetlinks.json")
def android_asset_links():
    # Lets Android's Digital Asset Links verifier confirm the Android
    # TWA app (signed with the matching certificate) is allowed to act
    # as this site's "app" — without this, the TWA still works but
    # falls back to showing a browser-style URL bar instead of the
    # full-screen native feel. The fingerprint below isn't secret (it's
    # meant to be published exactly like this) — it's the SHA-256 of the
    # release signing certificate generated for android/, matching what
    # .github/workflows/build-android-apk.yml signs the APK with.
    return jsonify([
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.employable.app",
                "sha256_cert_fingerprints": [
                    "67:DB:89:8E:9A:BA:6C:28:BF:B5:74:D4:08:C1:16:04:A4:E1:C1:9C:84:8B:B2:F6:78:C4:2F:C1:8D:09:81:35"
                ],
            },
        }
    ])


_ANDROID_APK_RELEASE_URL = "https://github.com/dsgnrcoza/mobile-employable/releases/download/android-latest/employable.apk"


@app.route("/download/android")
def download_android():
    # Proxies the built APK through our own domain instead of pointing
    # the browser straight at a github.com URL. Clicking a raw GitHub
    # link from inside the installed TWA (or this site running as an
    # installed PWA) means navigating outside the app's verified
    # origin, which hands the whole flow off to an external browser tab
    # instead of just downloading in place -- from the user's
    # perspective that looks exactly like "it redirects to GitHub"
    # instead of downloading. Keeping the entire request on our own
    # domain avoids that hand-off.
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(_ANDROID_APK_RELEASE_URL, headers={"User-Agent": "Employable-App"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError):
        return jsonify({"error": "Couldn't fetch the Android app right now. Please try again shortly."}), 502

    return Response(
        data,
        mimetype="application/vnd.android.package-archive",
        headers={"Content-Disposition": 'attachment; filename="employable.apk"'},
    )


@app.route("/")
def landing():
    # Decide and redirect immediately, server-side — no splash screen
    # waiting on a client-side timer/animation-end event to fire before
    # it acts. That deferred pattern could get stuck on a real device
    # (slow network, a resource that never finishes loading, etc.),
    # leaving the user staring at a loading screen that never proceeds.
    # /dashboard itself still redirects on to /terms or /onboarding when
    # needed, so this one destination naturally covers every case: not
    # logged in -> /login, logged in but not onboarded -> /onboarding,
    # fully set up -> /dashboard.
    if not auth.current_user():
        return redirect(url_for("login_page"))
    return redirect(url_for("dashboard"))


@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    if auth.current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            auth.signup(
                full_name=request.form.get("full_name", ""),
                email=request.form.get("email", ""),
                password=request.form.get("password", ""),
                confirm_password=request.form.get("confirm_password", ""),
            )
            flash("Account created. Welcome to Ploy.", "success")
            return redirect(url_for("dashboard"))
        except auth.AuthError as e:
            flash(str(e), "error")

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if auth.current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            auth.login(request.form.get("email", ""), request.form.get("password", ""))
            return redirect(url_for("dashboard"))
        except auth.AuthError as e:
            flash(str(e), "error")

    return render_template("login.html")


@app.route("/logout")
def logout_page():
    auth.log_out_user()
    flash("You've been logged out.", "success")
    return redirect(url_for("login_page"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    """
    Sends a reset link by email. Always shows the same "check your
    email" confirmation regardless of whether the address has an
    account -- auth.request_password_reset() only ever emails a real
    token when one does, so the response can't be used to enumerate
    which emails are registered here.
    """
    if auth.current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "")
        try:
            auth.validate_email(email)
        except auth.AuthError as e:
            flash(str(e), "error")
            return render_template("forgot_password.html")

        auth.request_password_reset(
            email,
            build_reset_url=lambda token: url_for("reset_password_page", token=token, _external=True),
        )
        return render_template("forgot_password.html", sent=True)

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password_page():
    if auth.current_user():
        return redirect(url_for("dashboard"))

    token = request.args.get("token") or request.form.get("token", "")
    if not auth.verify_reset_token(token):
        flash("That reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("forgot_password_page"))

    if request.method == "POST":
        try:
            auth.complete_password_reset(
                token,
                request.form.get("new_password", ""),
                request.form.get("confirm_password", ""),
            )
            flash("Password reset. Please sign in with your new password.", "success")
            return redirect(url_for("login_page"))
        except auth.AuthError as e:
            flash(str(e), "error")

    return render_template("reset_password.html", token=token)


# ---------------- ONBOARDING (required before first dashboard view) ----------------

@app.route("/onboarding")
@auth.login_required
def onboarding_page():
    user = auth.current_user()
    if user.get("documents_confirmed"):
        return redirect(url_for("dashboard"))
    documents = db.get_documents_for_user(user["id"])
    cv_documents = [d for d in documents if d.get("category") == "cv"]
    supporting_documents = [d for d in documents if d.get("category") != "cv"]
    return render_template(
        "onboarding.html",
        cv_documents=cv_documents,
        supporting_documents=supporting_documents,
        has_cv=pipeline.any_document_looks_like_cv(user["id"]),
    )


@app.route("/api/onboarding/readout", methods=["POST"])
@auth.login_required
def api_onboarding_readout():
    """
    The onboarding "wow" moment: parses the CV(s) just uploaded and
    returns a short, honest readout -- years of experience, a skills
    count, and 1-2 concrete problems that would hurt in a real ATS/
    recruiter pass. Real analysis of this specific person's real CV,
    not canned copy.
    """
    from openai import OpenAI
    user = auth.current_user()
    documents = db.get_documents_for_user(user["id"])
    cv_documents = [d for d in documents if d.get("category") == "cv"]
    if not cv_documents:
        return jsonify({"ok": False, "error": "Upload a CV first."}), 400

    doc_texts = []
    for d in cv_documents:
        text = (d.get("content") or "").strip()
        if text:
            doc_texts.append(f"[{d['filename']}]\n{text[:6000]}")
    doc_content_block = "\n\n---\n\n".join(doc_texts) if doc_texts else "No readable content extracted."

    system = (
        "You are Ploy's CV parser -- sharp, direct, zero corporate filler. "
        "Given the real content of a CV below, return a JSON object with exactly these keys:\n"
        "- 'years_experience': a short string like '5+ years', '2 years', or 'Entry-level' -- your best "
        "honest estimate from the actual dates/roles in the CV, never invented.\n"
        "- 'skills_count': an integer count of distinct real skills you can identify in the CV.\n"
        "- 'flags': an array of 1-2 short, specific, concrete problems with THIS CV that would hurt it in "
        "a real ATS scan or recruiter skim (e.g. no measurable results, no dedicated skills section, dates "
        "that don't parse cleanly, a wall of text with no structure). Each flag is one short sentence, "
        "specific to what's actually in this document -- never generic advice. If the CV is genuinely solid "
        "with nothing significant to flag, return an empty array.\n"
        "Never invent facts not present in the CV."
    )
    prompt = f"CV content:\n{doc_content_block}"

    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content.strip())
        return jsonify({
            "ok": True,
            "years_experience": parsed.get("years_experience", ""),
            "skills_count": int(parsed.get("skills_count") or 0),
            "flags": [f for f in (parsed.get("flags") or []) if f][:2],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/onboarding/upload", methods=["POST"])
@auth.login_required
def api_onboarding_upload():
    user = auth.current_user()
    files = request.files.getlist("documents")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"ok": False, "error": "No files were selected."}), 400

    category = (request.form.get("category") or "").strip()

    saved, rejected = [], []
    for f in files:
        if f.filename == "":
            continue
        if not _allowed_file(f.filename):
            rejected.append(f.filename)
            continue
        saved.append(pipeline.save_uploaded_file(user["id"], f, category=category))

    if not saved:
        return jsonify({"ok": False, "error": "None of the selected files are a supported type."}), 400

    result = pipeline.check_identity_conflict(user["id"])
    documents = db.get_documents_for_user(user["id"])
    return jsonify(
        {
            "ok": True,
            "rejected": rejected,
            "documents": [
                {
                    "id": d["id"],
                    "filename": d["filename"],
                    "file_type": d["file_type"],
                    "category": d.get("category", ""),
                    "file_size": d.get("file_size"),
                }
                for d in documents
            ],
            "has_cv": pipeline.any_document_looks_like_cv(user["id"]),
            **result,
        }
    )


@app.route("/api/onboarding/check")
@auth.login_required
def api_onboarding_check():
    user = auth.current_user()
    return jsonify({
        "has_cv": pipeline.any_document_looks_like_cv(user["id"]),
        **pipeline.check_identity_conflict(user["id"]),
    })


@app.route("/api/onboarding/document/<int:document_id>", methods=["DELETE"])
@auth.login_required
def api_onboarding_remove_document(document_id):
    user = auth.current_user()
    db.delete_document(user["id"], document_id)
    return jsonify({
        "ok": True,
        "has_cv": pipeline.any_document_looks_like_cv(user["id"]),
        **pipeline.check_identity_conflict(user["id"]),
    })


@app.route("/api/onboarding/confirm", methods=["POST"])
@auth.login_required
def api_onboarding_confirm():
    """
    Finalizes onboarding. The frontend sends the name the user
    confirmed (e.g. picked from the conflict chooser, or the single
    guessed name when there was no conflict) plus the list of
    document ids that belong to that person. Any document NOT in
    that list is deleted — mixing two people's documents in one
    account is never allowed, so the only way forward is to keep one
    identity's files and drop the rest.
    """
    data = request.json if request.is_json else request.form
    keep_ids = data.get("keep_document_ids") or []
    try:
        keep_ids = [int(i) for i in keep_ids]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid document selection."}), 400

    user = auth.current_user()
    documents = db.get_documents_for_user(user["id"])
    if not documents:
        return jsonify({"ok": False, "error": "Please upload at least one document first."}), 400

    # The name the user typed in onboarding's first step is the
    # source of truth now -- only fall back to a document-guessed name
    # (via the client-sent "name", populated from the identity-conflict
    # chooser) if that first step somehow never ran.
    chosen_name = (data.get("name") or "").strip() or (user.get("full_name") or "").strip()

    pipeline.resolve_identity_conflict(user["id"], keep_ids or [d["id"] for d in documents])

    remaining = db.get_documents_for_user(user["id"])
    if not remaining:
        return jsonify({"ok": False, "error": "No documents left after removing the conflicting set."}), 400

    db.set_documents_confirmed(user["id"], chosen_name)

    try:
        pipeline.run_analysis_for_user(user["id"])
    except analyzer.CVAnalyzerError as e:
        # Onboarding is still considered complete — scoring can be
        # re-run from the dashboard's "Refresh Score" button.
        return jsonify({"ok": True, "warning": f"Documents confirmed, but scoring couldn't run yet: {e}"})

    return jsonify({"ok": True})


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
@auth.login_required
def dashboard():
    # Ploy's chat screen (see home.html) -- kept at this same route/
    # endpoint name since every login/onboarding redirect in this file
    # targets url_for("dashboard"). The old dashboard.html template
    # (Cubic-Metric score, CV Workshop, etc.) is untouched on disk,
    # just no longer rendered from here.
    user = auth.current_user()
    avatar_path = user.get("avatar_path") or ""
    if avatar_path.startswith("data:"):
        avatar_url = avatar_path
    elif avatar_path:
        avatar_url = f"/static/{avatar_path}"
    else:
        avatar_url = ""
    profile = {
        "full_name": user.get("full_name") or "",
        "avatar_url": avatar_url,
        "initials": _initials(user.get("full_name") or ""),
    }
    return render_template("home.html", profile=profile)


def _initials(full_name):
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# ---------------- New tool sections (Qualify / Builder / Trackers / Notes) ----------------

@app.route("/qualify")
@auth.login_required
def qualify_page():
    user = auth.current_user()
    has_documents = len(db.get_documents_for_user(user["id"])) > 0
    return render_template("qualify.html", has_documents=has_documents)


@app.route("/api/qualify", methods=["POST"])
@auth.login_required
def api_qualify():
    from openai import OpenAI
    user = auth.current_user()
    data = request.get_json(force=True)
    job_title = (data.get("job_title") or "").strip()[:200]
    salary_expectation = (data.get("salary_expectation") or "").strip()[:100]
    location = (data.get("location") or "").strip()[:200]
    if not job_title:
        return jsonify({"ok": False, "error": "Missing job title."}), 400

    full_docs = db.get_documents_for_user(user["id"])
    doc_texts = []
    total_chars = 0
    DOC_CHAR_CAP = 3000
    TOTAL_CHAR_CAP = 20000
    for d in full_docs:
        if total_chars >= TOTAL_CHAR_CAP:
            break
        try:
            txt = (d.get("content") or "").strip()
            if not txt and d.get("stored_path") and os.path.exists(d["stored_path"]):
                txt = (extract.extract_text(d["stored_path"]) or "").strip()
            if txt:
                snippet = txt[:DOC_CHAR_CAP]
                doc_texts.append(f"[{d['filename']}]\n{snippet}")
                total_chars += len(snippet)
        except Exception:
            pass
    doc_content_block = "\n\n---\n\n".join(doc_texts) if doc_texts else "No documents uploaded yet."

    system = (
        "You are a blunt, accurate career-fit evaluator built into the Employable platform. "
        "Given a role the user is considering and the real content of their uploaded documents "
        "(CV, certificates, references), decide whether they are currently a strong candidate for "
        "that specific role, at that specific salary expectation if one is given.\n\n"
        "GROUNDING: base your verdict only on what's actually in the documents below. Never invent "
        "experience, skills, or qualifications that aren't there. If there isn't enough real document "
        "content to judge, say so directly in 'reasoning' and set 'qualifies' to false.\n\n"
        "OUTPUT RULES — return a JSON object with exactly these keys:\n"
        "- 'qualifies': true or false.\n"
        "- 'headline': one short, direct sentence stating the verdict (e.g. 'You're a strong match for this role.' "
        "or 'You're not quite there yet for this role.').\n"
        "- 'reasoning': 2-4 sentences explaining exactly why, citing specifics from their real documents "
        "(name the actual skills/experience/employers that support or undercut the verdict).\n"
        "- 'next_steps': 2-4 sentences. If they qualify, explain how to double down and stand out further "
        "for this specific role. If they don't qualify yet, give concrete, specific steps to close the gap.\n\n"
        "Use **double asterisks** to bold a key phrase or two per section, nothing else. No markdown "
        "headers, no bullet lists, no code fences."
    )
    prompt = (
        f"Role the user wants to qualify for: {job_title}\n"
        f"Salary expectation: {salary_expectation or 'Not specified'}\n"
        f"Location: {location or 'Not specified'}\n\n"
        f"Full content of the user's actual uploaded documents:\n{doc_content_block}"
    )

    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        return jsonify({
            "ok": True,
            "qualifies": bool(parsed.get("qualifies")),
            "headline": parsed.get("headline", ""),
            "reasoning": parsed.get("reasoning", ""),
            "next_steps": parsed.get("next_steps", ""),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _real_uploaded_docs(user_id):
    """Documents this user actually uploaded -- excludes CVs/letters Ploy
    itself generated, so every new generation is grounded in the user's
    original source material rather than compounding on its own earlier
    output."""
    return [d for d in db.get_documents_for_user(user_id) if d.get("category") not in ("generated_cv", "generated_letter")]


def _doc_content_block(docs, doc_char_cap=3000, total_char_cap=20000):
    doc_texts = []
    total_chars = 0
    for d in docs:
        if total_chars >= total_char_cap:
            break
        try:
            txt = (d.get("content") or "").strip()
            if not txt and d.get("stored_path") and os.path.exists(d["stored_path"]):
                txt = (extract.extract_text(d["stored_path"]) or "").strip()
            if txt:
                snippet = txt[:doc_char_cap]
                doc_texts.append(f"[{d['filename']}]\n{snippet}")
                total_chars += len(snippet)
        except Exception:
            pass
    return "\n\n---\n\n".join(doc_texts) if doc_texts else "No documents uploaded yet."


@app.route("/api/verdict", methods=["POST"])
@auth.login_required
def api_verdict():
    """Powers the Verdict card (chip 01 / "Am I a fit for this job?").
    Takes a raw pasted job ad, returns a structured fit verdict scored
    against this user's real documents -- rendered client-side as a card,
    never as a wall of chat text."""
    from openai import OpenAI
    user = auth.current_user()
    data = request.get_json(force=True)
    job_ad = (data.get("job_ad") or "").strip()[:8000]
    if not job_ad:
        return jsonify({"ok": False, "error": "Paste a job ad first."}), 400

    doc_content_block = _doc_content_block(_real_uploaded_docs(user["id"]))

    system = (
        "You are Ploy's brutally honest fit evaluator. Given a pasted job ad and the real content of "
        "this user's uploaded documents, decide how strong a candidate they are for THIS specific job, "
        "right now -- not how they could look someday.\n\n"
        "GROUNDING: base the verdict only on what's actually in the documents below. Never invent "
        "experience, skills, or qualifications that aren't there. If there isn't enough real document "
        "content to judge, say so in 'breakdown' and score low.\n\n"
        "OUTPUT RULES -- return a JSON object with exactly these keys:\n"
        "- 'job_title': the job title, extracted from the ad.\n"
        "- 'company': the company name if stated in the ad, else an empty string.\n"
        "- 'location': the job location if stated in the ad, else an empty string.\n"
        "- 'fit_score': an integer 0-100. 0-49 means not currently a fit, 50-74 means a partial/borderline "
        "fit, 75-100 means a strong fit. Be honest, not encouraging -- a mediocre match belongs in the "
        "40s-60s, not inflated into the 80s.\n"
        "- 'strengths': an array of at most 3 short strings (max ~12 words each), specific things in their "
        "real documents that support this exact job.\n"
        "- 'gaps': an array of at most 3 short strings (max ~12 words each), specific things this exact "
        "job needs that their documents don't show. Empty array only if there is truly nothing missing.\n"
        "- 'breakdown': 2-4 sentences of fuller reasoning behind the score, citing specifics from their "
        "real documents and the job ad.\n\n"
        "No markdown formatting inside any field -- plain text only."
    )
    prompt = f"Job ad pasted by the user:\n{job_ad}\n\nFull content of the user's actual uploaded documents:\n{doc_content_block}"

    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content.strip())
        fit_score = max(0, min(100, int(parsed.get("fit_score") or 0)))
        return jsonify({
            "ok": True,
            "type": "verdict",
            "job_title": (parsed.get("job_title") or "").strip()[:200] or "This role",
            "company": (parsed.get("company") or "").strip()[:200],
            "location": (parsed.get("location") or "").strip()[:200],
            "fit_score": fit_score,
            "strengths": [s for s in (parsed.get("strengths") or []) if s][:3],
            "gaps": [s for s in (parsed.get("gaps") or []) if s][:3],
            "breakdown": (parsed.get("breakdown") or "").strip(),
            "job_ad": job_ad,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _filename_stub(full_name):
    first = (full_name or "").strip().split()[:1]
    stub = "".join(ch for ch in (first[0] if first else "My") if ch.isalnum())
    return stub or "My"


def _company_stub(company):
    stub = "".join(ch for ch in (company or "") if ch.isalnum())
    return stub


def _generate_tailored_document(user, kind, job_title="", company="", job_ad=""):
    """Shared by /api/document and /api/letter-document -- generates a
    fresh, ATS-safe CV or cover letter grounded in the user's real
    uploaded documents (never Ploy's own earlier output), tailored to a
    specific job when one is given, renders it to PDF, and stores it as
    a private per-user document. Returns a Document-card-shaped dict, or
    raises on failure (caller turns that into a JSON error response)."""
    from openai import OpenAI

    real_docs = _real_uploaded_docs(user["id"])
    doc_content_block = _doc_content_block(real_docs)
    job_context = (
        f"Tailor this specifically for the following job:\nTitle: {job_title or 'Not specified'}\n"
        f"Company: {company or 'Not specified'}\nJob ad:\n{job_ad}\n\n"
        if job_ad else
        "No specific job was given -- write a strong, general-purpose version tailored to the user's own real experience.\n\n"
    )

    if kind == "cv":
        doc_label = "CV"
        design_guidance = (
            "Apply real CV design sense: a clear name/contact header, short bold section headings "
            "(Experience, Education, Skills, etc.) in a consistent order, reverse-chronological entries, "
            "bullet points for achievements rather than dense paragraphs, generous spacing so it's scannable "
            "in a 6-second recruiter skim. Keep it ATS-friendly: no tables, no unusual layouts, plain section "
            "headings a parser would recognize."
        )
    else:
        doc_label = "cover letter"
        design_guidance = (
            "Write a genuine cover letter: a brief opening naming the role, 2-3 short paragraphs connecting "
            "the user's real, specific experience to what this job actually needs, a direct closing. No "
            "generic filler paragraphs ('I am writing to express my interest...'). Sharp and specific, not "
            "corporate boilerplate."
        )

    system = (
        f"You are Ploy's document writer, generating a {doc_label} for this user.\n\n"
        f"{job_context}"
        f"{design_guidance}\n\n"
        "GROUNDING -- the most important rule, above all others: every fact (employer names, job titles, "
        "dates, schools, achievements, contact details) must come from the user's real documents below. "
        "NEVER invent a person, career, employer, or achievement that isn't actually theirs. If there isn't "
        "enough real information to write this, say so plainly in 'description' instead of fabricating.\n\n"
        "OUTPUT RULES -- return a JSON object with exactly these keys:\n"
        "- 'html': the full document as valid HTML using <p>, <strong>, <em>, <ul>, <li>, <h2>, <h3>, <hr>, "
        "<br> tags only. No markdown, no code fences, no <html>/<head>/<body> wrappers.\n"
        "- 'description': one short sentence (max 20 words) describing what was produced.\n"
        + ("- 'fit_score': an integer 0-100 estimating how strong a fit THIS tailored document now makes for "
           "the job above, judged the same way a recruiter would score the original documents.\n" if job_ad else "") +
        f"\nThis user's name: {user.get('full_name') or 'Unknown'}\n\n"
        f"Full content of the user's actual uploaded documents (the ONLY source of truth):\n{doc_content_block}"
    )

    client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": f"Generate the {doc_label} now."}],
        max_tokens=2400,
        response_format={"type": "json_object"},
    )
    parsed = json.loads(resp.choices[0].message.content.strip())
    html = parsed.get("html") or ""
    if not html.strip():
        raise ValueError(parsed.get("description") or "Not enough real document content to generate this.")

    pdf_bytes = _render_cv_pdf_bytes(cv_html=html)
    if pdf_bytes is None:
        raise ValueError("Couldn't render a document from that content.")

    name_stub = _filename_stub(user.get("full_name"))
    company_stub = _company_stub(company)
    label_stub = "CV" if kind == "cv" else "CoverLetter"
    filename = f"{name_stub}_{label_stub}_{company_stub}.pdf" if company_stub else f"{name_stub}_{label_stub}.pdf"

    import base64 as _b64
    document_id = db.add_document(
        user["id"], filename, stored_path="", file_type="pdf",
        content=html, category=f"generated_{kind}",
        file_size=len(pdf_bytes), file_bytes_b64=_b64.b64encode(pdf_bytes).decode("utf-8"),
    )

    fit_score = parsed.get("fit_score")
    if fit_score is not None:
        try:
            fit_score = max(0, min(100, int(fit_score)))
        except (TypeError, ValueError):
            fit_score = None

    return {
        "ok": True,
        "type": "document",
        "kind": kind,
        "document_id": document_id,
        "filename": filename,
        "job_title": job_title,
        "company": company,
        "fit_score": fit_score,
    }


@app.route("/api/document", methods=["POST"])
@auth.login_required
def api_document():
    """Powers the Document card for a tailored CV -- reached via chip 02
    ("Build my CV") or the Verdict card's "Fix my CV for this job" button."""
    user = auth.current_user()
    data = request.get_json(force=True)
    try:
        card = _generate_tailored_document(
            user, "cv",
            job_title=(data.get("job_title") or "").strip()[:200],
            company=(data.get("company") or "").strip()[:200],
            job_ad=(data.get("job_ad") or "").strip()[:8000],
        )
        return jsonify(card)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/letter-document", methods=["POST"])
@auth.login_required
def api_letter_document():
    """Powers the Document card for a cover letter -- reached via the
    "Cover letter" button on an existing CV Document card."""
    user = auth.current_user()
    data = request.get_json(force=True)
    try:
        card = _generate_tailored_document(
            user, "letter",
            job_title=(data.get("job_title") or "").strip()[:200],
            company=(data.get("company") or "").strip()[:200],
            job_ad=(data.get("job_ad") or "").strip()[:8000],
        )
        return jsonify(card)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/document-download/<int:document_id>")
@auth.login_required
def api_document_download(document_id):
    """Serves a generated CV/cover-letter PDF -- scoped to the requesting
    user's own documents, same as every other per-user document access
    in this app."""
    from flask import send_file
    import io
    user = auth.current_user()
    doc_row = db.get_document_by_id(user["id"], document_id)
    file_bytes = db.get_document_file_bytes(user["id"], document_id)
    if not doc_row or file_bytes is None:
        return jsonify({"ok": False, "error": "Document not found."}), 404
    return send_file(io.BytesIO(file_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=doc_row.get("filename") or "document.pdf")


@app.route("/api/document/<int:document_id>/save", methods=["POST"])
@auth.login_required
def api_document_save(document_id):
    """Writes Builder edits back to a generated CV/cover-letter -- so a
    card's "Download" later reflects the edited version, not the
    original AI draft. Scoped to the requesting user's own documents."""
    import base64 as _b64
    user = auth.current_user()
    doc_row = db.get_document_by_id(user["id"], document_id)
    if not doc_row or doc_row.get("category") not in ("generated_cv", "generated_letter"):
        return jsonify({"ok": False, "error": "Document not found."}), 404

    data = request.get_json(force=True)
    html = (data.get("html") or "").strip()
    pdf_bytes = _render_cv_pdf_bytes(cv_html=html)
    if pdf_bytes is None:
        return jsonify({"ok": False, "error": "Nothing to save."}), 400

    db.update_document_content(user["id"], document_id, content=html,
                               file_bytes_b64=_b64.b64encode(pdf_bytes).decode("utf-8"),
                               file_size=len(pdf_bytes))
    return jsonify({"ok": True})


# ---------------- Notes ----------------

@app.route("/notes")
@auth.login_required
def notes_page():
    return render_template("notes.html")


@app.route("/api/notes", methods=["GET"])
@auth.login_required
def api_get_notes():
    user = auth.current_user()
    return jsonify({"ok": True, "notes": db.get_notes_for_user(user["id"])})


@app.route("/api/notes", methods=["POST"])
@auth.login_required
def api_create_note():
    user = auth.current_user()
    data = request.get_json(force=True)
    note_id = db.create_note(
        user["id"],
        title=(data.get("title") or "").strip()[:200],
        body=(data.get("body") or "").strip()[:20000],
        color=(data.get("color") or "default").strip()[:20],
    )
    return jsonify({"ok": True, "note": db.get_note(note_id, user["id"])})


@app.route("/api/notes/<int:note_id>", methods=["PUT"])
@auth.login_required
def api_update_note(note_id):
    user = auth.current_user()
    if not db.get_note(note_id, user["id"]):
        return jsonify({"ok": False, "error": "Note not found."}), 404
    data = request.get_json(force=True)
    db.update_note(
        note_id,
        user["id"],
        title=(data.get("title") or "").strip()[:200],
        body=(data.get("body") or "").strip()[:20000],
        color=(data.get("color") or "default").strip()[:20],
    )
    return jsonify({"ok": True, "note": db.get_note(note_id, user["id"])})


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
@auth.login_required
def api_delete_note(note_id):
    user = auth.current_user()
    db.delete_note(note_id, user["id"])
    return jsonify({"ok": True})


# ---------------- Trackers ----------------

@app.route("/trackers")
@auth.login_required
def trackers_page():
    return render_template("trackers.html")


@app.route("/api/trackers", methods=["GET"])
@auth.login_required
def api_get_trackers():
    user = auth.current_user()
    return jsonify({"ok": True, "trackers": db.get_trackers_for_user(user["id"])})


@app.route("/api/trackers", methods=["POST"])
@auth.login_required
def api_create_tracker():
    user = auth.current_user()
    data = request.get_json(force=True)
    tracker_id = db.create_tracker(
        user["id"],
        job_title=(data.get("job_title") or "").strip()[:200],
        company=(data.get("company") or "").strip()[:200],
        date_applied=(data.get("date_applied") or "").strip()[:20],
        status=(data.get("status") or "applied").strip()[:20],
        notes=(data.get("notes") or "").strip()[:5000],
    )
    return jsonify({"ok": True, "tracker": db.get_tracker(tracker_id, user["id"])})


@app.route("/api/trackers/<int:tracker_id>", methods=["PUT"])
@auth.login_required
def api_update_tracker(tracker_id):
    user = auth.current_user()
    if not db.get_tracker(tracker_id, user["id"]):
        return jsonify({"ok": False, "error": "Tracker entry not found."}), 404
    data = request.get_json(force=True)
    db.update_tracker(
        tracker_id,
        user["id"],
        job_title=(data.get("job_title") or "").strip()[:200],
        company=(data.get("company") or "").strip()[:200],
        date_applied=(data.get("date_applied") or "").strip()[:20],
        status=(data.get("status") or "applied").strip()[:20],
        notes=(data.get("notes") or "").strip()[:5000],
    )
    return jsonify({"ok": True, "tracker": db.get_tracker(tracker_id, user["id"])})


@app.route("/api/trackers/<int:tracker_id>", methods=["DELETE"])
@auth.login_required
def api_delete_tracker(tracker_id):
    user = auth.current_user()
    db.delete_tracker(tracker_id, user["id"])
    return jsonify({"ok": True})


@app.route("/api/dashboard-state")
@auth.login_required
def api_dashboard_state():
    """
    Returns the full dashboard state as JSON. The frontend calls this
    after any change to refresh the Cubic-Metric bars and skills list
    without a page reload.
    """
    user = auth.current_user()
    return jsonify(pipeline.get_dashboard_state(user["id"]))


@app.route("/api/upload", methods=["POST"])
@auth.login_required
def api_upload():
    user = auth.current_user()
    files = request.files.getlist("documents")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"ok": False, "error": "No files were selected."}), 400

    # Capture score before upload so we can compute per-document delta
    _pre_row = db.get_latest_analysis(user["id"])
    _pre_analysis = json.loads(_pre_row["result_json"]) if _pre_row else {}
    _score_before = float(_pre_analysis.get("overall_score") or 0)
    _dims_before = {d["label"]: d["score"] for d in (_pre_analysis.get("dimensions") or [])}

    saved = []
    rejected = []
    identity_rejected = []
    for f in files:
        if f.filename == "":
            continue
        if not _allowed_file(f.filename):
            rejected.append(f.filename)
            continue

        # Read the file once so we can check identity before deciding
        # whether to keep it, then rewind so save_uploaded_file can
        # still write the original bytes to disk.
        text_preview = ""
        try:
            f.stream.seek(0)
        except Exception:
            pass
        saved_doc = pipeline.save_uploaded_file(user["id"], f)
        text_preview = extract.extract_text(saved_doc["stored_path"])

        if not pipeline.matches_confirmed_owner(user["id"], saved_doc["filename"], text_preview):
            db.delete_document(user["id"], saved_doc["id"])
            identity_rejected.append(saved_doc["filename"])
            continue

        saved.append(saved_doc)

    if identity_rejected and not saved:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "These documents don't appear to belong to "
                    f"{user.get('confirmed_owner_name') or 'the account holder'}: "
                    f"{', '.join(identity_rejected)}. Combining different people's documents "
                    "in one account isn't allowed."
                ),
            }
        ), 400

    if not saved:
        return jsonify({"ok": False, "error": "None of the selected files are a supported type."}), 400

    try:
        pipeline.run_analysis_for_user(user["id"])
    except analyzer.CVAnalyzerError as e:
        # The files are saved either way — only the AI scoring failed
        # (e.g. missing API key) — so tell the user the upload worked
        # but scoring couldn't run, rather than pretending nothing
        # happened.
        return jsonify(
            {
                "ok": True,
                "warning": f"Files uploaded, but scoring couldn't run: {e}",
                "rejected": rejected,
                "state": pipeline.get_dashboard_state(user["id"]),
            }
        )

    # Store score delta + per-dimension deltas on each newly saved document
    _post_row = db.get_latest_analysis(user["id"])
    _post_analysis = json.loads(_post_row["result_json"]) if _post_row else {}
    _score_after = float(_post_analysis.get("overall_score") or 0)
    _delta = round(_score_after - _score_before, 2)
    _dims_after = {d["label"]: d["score"] for d in (_post_analysis.get("dimensions") or [])}
    _dim_deltas = {
        lbl: round(_dims_after.get(lbl, 0) - _dims_before.get(lbl, 0), 2)
        for lbl in set(list(_dims_before) + list(_dims_after))
        if round(_dims_after.get(lbl, 0) - _dims_before.get(lbl, 0), 2) != 0
    }
    if _delta != 0 or _dim_deltas:
        _dim_json = json.dumps(_dim_deltas) if _dim_deltas else None
        for _doc in saved:
            db.set_document_score_delta(user["id"], _doc["id"], _delta, _dim_json)

    warning = None
    if identity_rejected:
        warning = f"Skipped (doesn't match this account's confirmed identity): {', '.join(identity_rejected)}"

    response = {"ok": True, "rejected": rejected, "state": pipeline.get_dashboard_state(user["id"])}
    if warning:
        response["warning"] = warning
    return jsonify(response)


@app.route("/api/documents/<int:document_id>", methods=["DELETE"])
@auth.login_required
def api_delete_document(document_id):
    user = auth.current_user()
    db.delete_document(user["id"], document_id)
    remaining = db.get_documents_for_user(user["id"])
    if not remaining:
        db.reset_documents_confirmed(user["id"])
        return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})
    try:
        pipeline.run_analysis_for_user(user["id"])
    except analyzer.CVAnalyzerError as e:
        return jsonify({
            "ok": True,
            "warning": f"Document removed, but scoring couldn't re-run: {e}",
            "state": pipeline.get_dashboard_state(user["id"]),
        })
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/reanalyze", methods=["POST"])
@auth.login_required
def api_reanalyze():
    user = auth.current_user()
    extra_context = request.json.get("extra_context", "") if request.is_json else ""
    try:
        pipeline.run_analysis_for_user(user["id"], extra_context=extra_context)
    except analyzer.CVAnalyzerError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/skills", methods=["POST"])
@auth.login_required
def api_add_skill():
    user = auth.current_user()
    data = request.json if request.is_json else request.form
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "Skill cannot be empty."}), 400
    if len(label) > 60:
        return jsonify({"ok": False, "error": "Skill is too long."}), 400
    db.add_skill(user["id"], label, source="manual")
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/skills/<int:skill_id>", methods=["DELETE"])
@auth.login_required
def api_delete_skill(skill_id):
    user = auth.current_user()
    db.delete_skill(user["id"], skill_id)
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/profile", methods=["POST"])
@auth.login_required
def api_update_profile():
    user = auth.current_user()
    data = request.json if request.is_json else request.form
    fields = {}
    for key in ("full_name", "headline", "email", "location", "phone"):
        if key in data:
            fields[key] = (data.get(key) or "").strip()

    if "email" in fields:
        try:
            fields["email"] = auth.validate_email(fields["email"])
        except auth.AuthError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        existing = db.get_user_by_email(fields["email"])
        if existing and existing["id"] != user["id"]:
            return jsonify({"ok": False, "error": "That email is already in use by another account."}), 400
    if "full_name" in fields:
        try:
            fields["full_name"] = auth.validate_full_name(fields["full_name"])
        except auth.AuthError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    db.update_profile_fields(user["id"], **fields)
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/profile/password", methods=["POST"])
@auth.login_required
def api_change_password():
    user = auth.current_user()
    data = request.get_json(force=True)
    try:
        auth.change_password(
            user["id"],
            data.get("current_password") or "",
            data.get("new_password") or "",
            data.get("confirm_password") or "",
        )
        return jsonify({"ok": True})
    except auth.AuthError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/profile/documents")
@auth.login_required
def api_profile_documents():
    user = auth.current_user()
    docs = _real_uploaded_docs(user["id"])
    return jsonify({
        "ok": True,
        "documents": [
            {
                "id": d["id"],
                "filename": d["filename"],
                "category": d.get("category", ""),
                "file_type": d.get("file_type", ""),
                "file_size": d.get("file_size"),
                "uploaded_at": d.get("uploaded_at"),
            }
            for d in docs
        ],
    })


@app.route("/profile")
@auth.login_required
def profile_page():
    user = auth.current_user()
    return render_template("profile.html", user=user)


@app.route("/api/friends/invite", methods=["POST"])
@auth.login_required
def api_friends_invite():
    user = auth.current_user()
    data = request.json if request.is_json else request.form
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"ok": False, "error": "Enter a username."}), 400

    target = db.get_user_by_username(username)
    if not target:
        return jsonify({"ok": False, "error": "No account found with that username."}), 404
    if target["id"] == user["id"]:
        return jsonify({"ok": False, "error": "You can't add yourself."}), 400

    existing = db.get_active_friend_request_between(user["id"], target["id"])
    if existing:
        if existing["status"] == "accepted":
            return jsonify({"ok": False, "error": "You're already friends."}), 400
        return jsonify({"ok": False, "error": "A friend request is already pending."}), 400

    db.create_friend_request(user["id"], target["id"])
    return jsonify({"ok": True})


@app.route("/api/friends/requests")
@auth.login_required
def api_friends_requests():
    user = auth.current_user()
    return jsonify({"requests": db.get_pending_incoming_requests(user["id"])})


@app.route("/api/friends/requests/<int:request_id>/respond", methods=["POST"])
@auth.login_required
def api_friends_respond(request_id):
    user = auth.current_user()
    data = request.json if request.is_json else request.form
    accept = bool(data.get("accept"))

    req = db.get_friend_request_by_id(request_id)
    if not req or req["to_user_id"] != user["id"] or req["status"] != "pending":
        return jsonify({"ok": False, "error": "Request not found."}), 404

    db.respond_to_friend_request(request_id, "accepted" if accept else "declined")
    return jsonify({"ok": True})


@app.route("/api/friends")
@auth.login_required
def api_friends_list():
    user = auth.current_user()
    return jsonify({"friends": db.get_friends_for_user(user["id"])})


@app.route("/api/profile/photo", methods=["POST"])
@auth.login_required
def api_upload_avatar():
    import base64
    user = auth.current_user()
    f = request.files.get("photo")
    if not f or f.filename == "":
        return jsonify({"ok": False, "error": "No file selected."}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"ok": False, "error": "Unsupported image type."}), 400
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    encoded = base64.b64encode(f.read()).decode("ascii")
    data_uri = f"data:{mime};base64,{encoded}"
    db.update_profile_fields(user["id"], avatar_path=data_uri)
    return jsonify({"ok": True, "avatar_url": data_uri, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/account/delete", methods=["POST"])
@auth.login_required
def api_delete_account():
    """
    Permanently deletes the logged-in user's account. The DB row delete
    cascades to every other table (documents, skills, analyses, etc. —
    see db.delete_user's docstring), but that cascade doesn't touch the
    filesystem, so the user's uploaded-files folder is removed here too.
    """
    import shutil
    user = auth.current_user()
    user_id = user["id"]
    upload_dir = pipeline.user_upload_dir(user_id)
    db.delete_user(user_id)
    shutil.rmtree(upload_dir, ignore_errors=True)
    auth.log_out_user()
    return jsonify({"ok": True})


@app.route("/api/clear-cache", methods=["POST"])
@auth.login_required
def api_clear_cache():
    user = auth.current_user()
    cache_module.clear_cache()
    db.clear_analyses(user["id"])
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/api/applications", methods=["POST"])
@auth.login_required
def api_add_application():
    user = auth.current_user()
    data = request.json if request.is_json else request.form
    job_title = (data.get("job_title") or "").strip()
    company = (data.get("company") or "").strip()
    if not job_title or not company:
        return jsonify({"ok": False, "error": "Job title and company are required."}), 400
    db.add_application(user["id"], job_title, company)
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")


@app.route("/terms")
@auth.login_required
def terms_page():
    user = auth.current_user()
    return render_template("terms_gate.html", already_accepted=bool(user.get("disclaimer_accepted")))


@app.route("/api/disclaimer-accept", methods=["POST"])
@auth.login_required
def api_disclaimer_accept():
    user = auth.current_user()
    db.set_disclaimer_accepted(user["id"])
    return jsonify({"ok": True})


@app.route("/api/target-field", methods=["POST"])
@auth.login_required
def api_set_target_field():
    user = auth.current_user()
    data = request.json if request.is_json else request.form
    target_field = (data.get("target_field") or "").strip()
    db.set_target_field(user["id"], target_field)
    return jsonify({"ok": True, "state": pipeline.get_dashboard_state(user["id"])})


AI_UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "ai_uploads")


@app.route("/api/chat/upload", methods=["POST"])
@auth.login_required
def api_chat_upload():
    """Upload a file for use in AI chat. Stored server-side, never added to CV profile."""
    user = auth.current_user()
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Empty file"}), 400

    user_upload_dir = os.path.join(AI_UPLOAD_ROOT, str(user["id"]))
    os.makedirs(user_upload_dir, exist_ok=True)

    safe_name = uuid.uuid4().hex + "_" + f.filename.replace(" ", "_")
    stored_path = os.path.join(user_upload_dir, safe_name)
    f.save(stored_path)

    mime_type = f.content_type or ""
    text_content = ""
    if not mime_type.startswith("image/"):
        try:
            text_content = extract.extract_text(stored_path)
            if text_content:
                text_content = text_content.strip()[:8000]
        except Exception:
            pass

    att_id = db.add_chat_attachment(user["id"], f.filename, stored_path, mime_type, text_content)
    return jsonify({"ok": True, "id": att_id, "name": f.filename, "mime": mime_type, "is_image": mime_type.startswith("image/")})


@app.route("/api/chat/conversations", methods=["GET"])
@auth.login_required
def api_get_conversations():
    user = auth.current_user()
    convs = db.get_conversations_for_user(user["id"])
    return jsonify({"ok": True, "conversations": convs})


def _generate_conversation_title(messages):
    """First-exchange AI naming for a brand new chat thread ("auto-named
    by AI from first message"). Returns None on any failure (including
    no API key configured) so the caller can fall back to its own
    truncated-text title instead of leaving a new chat untitled."""
    from openai import OpenAI
    convo_snippet = "\n".join(
        f"{m.get('role')}: {(m.get('text') or '')[:400]}" for m in messages[:4]
    )
    if not convo_snippet.strip():
        return None
    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Give this chat a short title, 3-6 words, plain text, no quotes or trailing punctuation, summarizing what it's about."},
                {"role": "user", "content": convo_snippet},
            ],
            max_tokens=20,
        )
        title = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        return title[:80] if title else None
    except Exception:
        return None


@app.route("/api/chat/conversations", methods=["POST"])
@auth.login_required
def api_save_conversation():
    """Create a new conversation or append messages to an existing one.
    The title is set once, at creation (AI-named from the first
    exchange when possible) -- later saves never touch it, so it can't
    drift back to raw truncated text on a later message, and promoted
    job threads keep the "{Role} · {Company}" title promotion set."""
    user = auth.current_user()
    data = request.get_json(force=True)
    conv_id = data.get("conversation_id")
    fallback_title = (data.get("title") or "Conversation")[:80]
    messages = data.get("messages", [])

    if not conv_id:
        title = _generate_conversation_title(messages) or fallback_title
        conv_id = db.create_conversation(user["id"], title)
    elif not db.conversation_belongs_to_user(conv_id, user["id"]):
        # conversation_id came from the client -- without this check, a
        # crafted id belonging to another user would let their entire
        # conversation be wiped and overwritten by the DELETE+INSERT below.
        return jsonify({"ok": False, "error": "Conversation not found."}), 404

    # Replace all messages for this conversation (simplest sync strategy)
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conv_id,))
        conn.commit()
        for m in messages:
            db.add_chat_message(conv_id, m.get("role", "user"), m.get("text", ""), m.get("attachment_ids", []), m.get("card"))
        db.touch_conversation(conv_id, user["id"])
    finally:
        conn.close()

    return jsonify({"ok": True, "conversation_id": conv_id})


@app.route("/api/chat/conversations/<int:conv_id>", methods=["GET"])
@auth.login_required
def api_get_conversation(conv_id):
    user = auth.current_user()
    msgs = db.get_messages_for_conversation(conv_id, user["id"])
    result = []
    for m in msgs:
        import json as _json
        att_ids = _json.loads(m.get("attachment_ids_json") or "[]")
        card = _json.loads(m["card_json"]) if m.get("card_json") else None
        result.append({"role": m["role"], "text": m["text"], "attachment_ids": att_ids, "card": card})
    return jsonify({"ok": True, "messages": result})


@app.route("/api/chat/conversations/<int:conv_id>", methods=["DELETE"])
@auth.login_required
def api_delete_conversation(conv_id):
    user = auth.current_user()
    db.delete_conversation(conv_id, user["id"])
    return jsonify({"ok": True})


@app.route("/api/chat/conversations/<int:conv_id>/promote", methods=["POST"])
@auth.login_required
def api_promote_conversation(conv_id):
    """The job-thread promotion mechanic: called the moment a Verdict or
    Document card lands in a chat. Title snaps to "{Role} · {Company}",
    the thread gets a live status badge, and it sorts above plain chats
    -- this IS the tracker, there's no separate screen for it."""
    user = auth.current_user()
    data = request.get_json(force=True)
    fit_score = data.get("fit_score")
    try:
        fit_score = int(fit_score) if fit_score is not None else None
    except (TypeError, ValueError):
        fit_score = None
    db.promote_conversation(
        conv_id, user["id"],
        job_title=(data.get("job_title") or "").strip()[:200],
        company=(data.get("company") or "").strip()[:200],
        fit_score=fit_score,
        status_label=(data.get("status_label") or "").strip()[:100],
    )
    return jsonify({"ok": True})


@app.route("/api/chat/conversations/<int:conv_id>/status", methods=["POST"])
@auth.login_required
def api_update_conversation_status(conv_id):
    """Updates a job thread's live status badge -- e.g. the Document
    card's "Mark as applied" action moving a thread from "CV ready" to
    "Sent"."""
    user = auth.current_user()
    data = request.get_json(force=True)
    db.update_conversation_status(conv_id, user["id"], (data.get("status_label") or "").strip()[:100])
    return jsonify({"ok": True})


@app.route("/api/chat/attachment-thumb/<int:att_id>")
@auth.login_required
def api_chat_attachment_thumb(att_id):
    """Serve the stored image file for preview thumbnails."""
    from flask import send_file
    user = auth.current_user()
    att = db.get_chat_attachment(user["id"], att_id)
    if not att or not att["mime_type"].startswith("image/"):
        return ("Not found", 404)
    return send_file(att["stored_path"], mimetype=att["mime_type"])


@app.route("/api/cv-text")
@auth.login_required
def api_cv_text():
    """Return the user's CV as HTML for the editor, extracting text live from the stored file."""
    import extract as _extract
    user = auth.current_user()
    documents = db.get_documents_for_user(user["id"])
    if not documents:
        return jsonify({"html": None})
    # Prefer PDFs/docs; fall back to all documents
    cv_docs = [d for d in documents if d.get("file_type", "").lower() in ("pdf", "docx", "doc", "cv", "resume")]
    ordered = cv_docs or documents
    combined = ""
    for d in ordered:
        text = (d.get("text_content") or "").strip()
        if not text:
            try:
                text = _extract.extract_text(d["stored_path"]).strip()
            except Exception:
                pass
        if text:
            combined = text
            break  # use the first successful CV
    if not combined:
        return jsonify({"html": None})
    # Convert to HTML: detect headings (short ALL-CAPS lines) and paragraphs
    html_parts = []
    for line in combined.split("\n"):
        stripped = line.strip()
        if not stripped:
            html_parts.append("<p><br></p>")
        elif len(stripped) < 60 and stripped.isupper():
            html_parts.append(f"<p><strong>{stripped}</strong></p>")
        else:
            html_parts.append(f"<p>{stripped}</p>")
    return jsonify({"html": "".join(html_parts)})


@app.route("/api/document-insight/<int:doc_id>")
@auth.login_required
def api_document_insight(doc_id):
    """AI explanation of exactly what a specific document contributed to the profile."""
    from openai import OpenAI
    import extract as _extract
    user = auth.current_user()

    docs = db.get_documents_for_user(user["id"])
    doc = next((d for d in docs if d["id"] == doc_id), None)
    if not doc:
        return jsonify({"error": "Document not found."}), 404

    # Return cached insight if available (only if it's HTML — plain text cache is stale)
    cached = doc.get("insight_cache") or ""
    if cached and ("<p>" in cached or "<ul>" in cached or "<strong>" in cached):
        return jsonify({"insight": cached, "filename": doc["filename"],
                        "score_delta": doc.get("score_delta"),
                        "dim_deltas": json.loads(doc["dimension_deltas"]) if doc.get("dimension_deltas") else {}})

    # Extract text
    text = (doc.get("text_content") or "").strip()
    if not text:
        try:
            text = _extract.extract_text(doc["stored_path"]).strip()
        except Exception:
            text = ""

    _analysis_row = db.get_latest_analysis(user["id"])
    _analysis_data = json.loads(_analysis_row["result_json"]) if _analysis_row and _analysis_row.get("result_json") else {}
    skills = db.get_skills_for_user(user["id"])
    skill_names = [s["label"] if isinstance(s, dict) else str(s) for s in skills]
    dimensions = _analysis_data.get("dimensions") or []
    overall = float(_analysis_data.get("overall_score") or 0)
    delta = float(doc.get("score_delta") or 0)
    dim_deltas = json.loads(doc["dimension_deltas"]) if doc.get("dimension_deltas") else {}
    all_filenames = [d["filename"] for d in docs if d["id"] != doc_id]

    client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
    system = (
        "You are a career intelligence analyst inside the Employable platform. "
        "Respond ONLY with valid HTML — use <p>, <ul>, <li>, <strong> tags. "
        "Never use asterisks (*) or markdown. Keep paragraphs short (2-3 sentences max). "
        "Use bullet lists for multiple items. Be specific and reference real content from the document.\n\n"
        "Structure your response as:\n"
        "1. A short intro paragraph — what this document proves to an employer\n"
        "2. A <ul> list of which score dimensions it raised and by how much (use the dim_deltas provided)\n"
        "3. A short paragraph on how it complements the other documents\n"
        "4. One concrete <strong>improvement tip</strong> to make this document stronger\n\n"
        "Be truthful, specific, and genuinely helpful. Never be vague."
    )
    dim_lines = "\n".join(f"  {d['label']}: {d['score']}/10" for d in dimensions)
    dim_delta_lines = "\n".join(f"  {lbl}: +{v} pts" for lbl, v in dim_deltas.items()) if dim_deltas else "  (no specific dimension data)"
    other_docs = ", ".join(all_filenames) if all_filenames else "none"
    prompt = (
        f"Document: {doc['filename']} ({doc['file_type'].upper()})\n"
        f"Overall score impact: +{delta} pts (was {round(overall - delta, 1)} → now {overall})\n\n"
        f"Dimension-level score increases from this document:\n{dim_delta_lines}\n\n"
        f"Current dimension scores:\n{dim_lines}\n\n"
        f"Document content:\n{text[:3000]}\n\n"
        f"Other documents in profile: {other_docs}\n"
        f"Registered skills: {', '.join(skill_names[:20])}\n\n"
        f"Explain specifically what '{doc['filename']}' contributes. Reference the dim_deltas in your bullet list."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=700,
        )
        insight = resp.choices[0].message.content.strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    db.set_document_insight_cache(user["id"], doc_id, insight)
    return jsonify({"insight": insight, "filename": doc["filename"], "score_delta": delta, "dim_deltas": dim_deltas})


@app.route("/api/cv-text-upload", methods=["POST"])
@auth.login_required
def api_cv_text_upload():
    """Accept a CV file uploaded directly from the user's device, return as editor HTML."""
    import extract as _extract, tempfile, os as _os
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file received."}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "txt"
    if ext not in ("pdf", "doc", "docx", "txt", "rtf"):
        return jsonify({"error": "Unsupported file type. Please upload a PDF, DOCX, or TXT."}), 400
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        text = _extract.extract_text(tmp_path).strip()
    except Exception as e:
        return jsonify({"error": f"Could not read file: {e}"}), 500
    finally:
        try: _os.unlink(tmp_path)
        except Exception: pass
    if not text:
        return jsonify({"error": "No readable text found in that file."}), 400
    html_parts = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            html_parts.append("<p><br></p>")
        elif len(stripped) < 60 and stripped.isupper():
            html_parts.append(f"<p><strong>{stripped}</strong></p>")
        else:
            html_parts.append(f"<p>{stripped}</p>")
    return jsonify({"html": "".join(html_parts)})


@app.route("/api/roadmap/completions")
@auth.login_required
def api_roadmap_completions():
    user = auth.current_user()
    completions = db.get_roadmap_completions(user["id"])
    return jsonify({"completions": [dict(c) for c in completions]})


@app.route("/api/roadmap/complete", methods=["POST"])
@auth.login_required
def api_roadmap_complete():
    """
    AI evaluates whether a selected document fulfills a roadmap
    objective. The AI only judges fulfilled/not-fulfilled (plus a
    reason and, if not fulfilled, concrete next steps) — it does NOT
    invent its own point value. The point value awarded is whatever
    the dashboard already displayed for this item (computed in
    pipeline._filter_and_recompute_roadmap using the exact same formula
    as the visible gauge/bars), passed through from the frontend and
    clamped to a sane range here. This is what guarantees the number
    the user saw on the card is the exact number they get — no second,
    independently-invented number from a different AI call.
    """
    from openai import OpenAI
    import extract as _extract
    user = auth.current_user()
    data = request.get_json(force=True)
    item_label = (data.get("item_label") or "").strip()
    item_description = (data.get("item_description") or "").strip()
    doc_id = data.get("doc_id")
    try:
        # 2.0 is the maximum headroom any single visible dimension can
        # have (see DASHBOARD_VISIBLE_DIMENSIONS scoring) — anything
        # outside that range could only be a bad/tampered request.
        displayed_points = max(0.0, min(2.0, float(data.get("points") or 0)))
    except (TypeError, ValueError):
        displayed_points = 0.0
    if not item_label or not doc_id:
        return jsonify({"error": "Missing item_label or doc_id"}), 400

    # Check not already completed
    completions = db.get_roadmap_completions(user["id"])
    if any(c["item_label"] == item_label for c in completions):
        return jsonify({"error": "already_completed"}), 400

    docs = db.get_documents_for_user(user["id"])
    doc = next((d for d in docs if d["id"] == int(doc_id)), None)
    if not doc:
        return jsonify({"error": "Document not found"}), 404

    text = (doc.get("text_content") or "").strip()
    if not text:
        try:
            text = _extract.extract_text(doc["stored_path"]).strip()
        except Exception:
            text = ""

    _analysis_row = db.get_latest_analysis(user["id"])
    _analysis_data = json.loads(_analysis_row["result_json"]) if _analysis_row and _analysis_row.get("result_json") else {}
    overall = float(_analysis_data.get("overall_score") or 0)

    client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
    system = (
        "You are a strict but fair career coach evaluating whether a document fulfills a specific career improvement objective. "
        "Reply ONLY with a JSON object in this exact format:\n"
        '{"fulfilled": true/false, "reason": "one short sentence explaining your verdict", "steps": []}\n'
        "Rules:\n"
        "- steps: if NOT fulfilled, provide 3–5 specific, actionable steps (as an array of strings) that tell the user exactly "
        "what they need to do or add to their document to satisfy this objective. Be specific to the objective — not generic advice. "
        "If fulfilled, steps should be an empty array [].\n"
        "Be truthful — if the document genuinely demonstrates the required skill or output, mark it fulfilled."
    )
    prompt = (
        f"Roadmap objective: {item_label}\n"
        f"Objective detail: {item_description}\n\n"
        f"Document submitted: {doc['filename']}\n"
        f"Document content (first 2500 chars):\n{text[:2500]}\n\n"
        f"Evaluate whether this document fulfills the roadmap objective. Reply with JSON only."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    fulfilled = bool(result.get("fulfilled"))
    reason = result.get("reason", "")
    steps = result.get("steps") or []
    points = displayed_points if fulfilled else 0.0

    if fulfilled and points > 0:
        db.add_roadmap_completion(user["id"], item_label, int(doc_id), points)

    return jsonify({
        "fulfilled": fulfilled,
        "reason": reason,
        "points": points,
        "steps": steps,
        "overall_before": overall,
    })


@app.route("/api/cv-edit", methods=["POST"])
@auth.login_required
def api_cv_edit():
    """AI edits the CV content based on a user instruction. Works with HTML for precise formatting."""
    from openai import OpenAI
    user = auth.current_user()
    data = request.get_json(force=True)
    instruction = (data.get("instruction") or "").strip()
    cv_html = (data.get("cv_html") or "").strip()
    cv_content = (data.get("cv_content") or "").strip()
    if not instruction:
        return jsonify({"error": "Missing instruction."}), 400

    # Same grounding the main chat endpoint uses (see api_chat) -- without
    # this, an empty editor + an instruction like "write me a CV like
    # mine" had nothing real to draw from, and the model would invent a
    # complete fake person (wrong job history, wrong employers, wrong
    # everything) rather than ever admitting it had nothing to go on.
    state = pipeline.get_dashboard_state(user["id"])
    profile = state.get("profile", {})
    full_docs = db.get_documents_for_user(user["id"])
    doc_texts = []
    total_chars = 0
    DOC_CHAR_CAP = 3000
    TOTAL_CHAR_CAP = 20000
    for d in full_docs:
        if total_chars >= TOTAL_CHAR_CAP:
            break
        try:
            txt = (d.get("content") or "").strip()
            if not txt and d.get("stored_path") and os.path.exists(d["stored_path"]):
                txt = (extract.extract_text(d["stored_path"]) or "").strip()
            if txt:
                snippet = txt[:DOC_CHAR_CAP]
                doc_texts.append(f"[{d['filename']}]\n{snippet}")
                total_chars += len(snippet)
        except Exception:
            pass
    doc_content_block = "\n\n---\n\n".join(doc_texts) if doc_texts else "No documents uploaded yet."
    skill_names = [s["label"] for s in state.get("skills", [])]

    import re as _re
    system = (
        "You are an expert CV/document editor built into the Employable platform. "
        "CRITICAL: Always attempt to understand and fulfill the user's intent, even if their instruction contains spelling mistakes, typos, or imprecise phrasing. "
        "Never refuse, do nothing, or ask for clarification — silently make your best reasonable interpretation and act on it. "
        "OUTPUT RULES — you MUST follow these exactly:\n"
        "- Return a JSON object with exactly two keys: 'html' and 'description'.\n"
        "- 'html': the full updated document as valid HTML. Use <p>, <strong>, <em>, <ul>, <li>, <h2>, <h3>, <hr>, <br> tags. "
        "NEVER use markdown asterisks, hyphens for bullets, --- separators, or backticks. "
        "No <html>, <head>, <body> wrappers. No code fences.\n"
        "- 'description': one short, specific sentence (max 20 words) describing exactly what you changed — e.g. "
        "'Made all section headings bold and centered the name at the top.' "
        "Be specific to what was actually done, not generic.\n\n"
        "DESIGN — when writing or restructuring a CV (not just tweaking a sentence), apply real CV design "
        "sense, not just correct facts in a wall of text: a clear name/contact header, short bold section "
        "headings (Experience, Education, Skills, etc.) in a consistent order, reverse-chronological entries "
        "within each section, bullet points for achievements rather than dense paragraphs, consistent bold "
        "usage for role titles or employer names (pick one convention and stick to it), and generous <hr>/"
        "spacing between sections so it's scannable in a 6-second recruiter skim. Keep it ATS-friendly: no "
        "tables or unusual layouts, plain section headings a parser would recognize, no relying on color or "
        "font tricks to convey structure.\n\n"
        "GROUNDING — this is the most important rule, above all others: every fact in the document "
        "(employer names, job titles, dates, schools, achievements, contact details) must come from "
        "the user's real profile/documents below, or already be present in the current document HTML. "
        "NEVER invent a person, career, employer, achievement, or biography that isn't actually theirs "
        "-- not even a plausible-sounding placeholder one. If the document is empty and the user asks you "
        "to write or generate a CV, build it FROM the real document content below. If there isn't enough "
        "real information to do that (no documents uploaded, or nothing usable in them), say so plainly "
        "in 'description' and return the document unchanged rather than fabricating a fake CV.\n\n"
        f"This user's real profile:\n"
        f"- Name: {profile.get('full_name') or 'Unknown'}\n"
        f"- Location: {profile.get('location') or 'Not specified'}\n"
        f"- Skills: {', '.join(skill_names) if skill_names else 'None listed.'}\n\n"
        f"Full content of this user's actual uploaded documents (the ONLY source of truth for any CV "
        f"content you write):\n{doc_content_block}"
    )
    prompt = f"Current document HTML:\n{cv_html if cv_html else '(empty — generate fresh content)'}\n\nInstruction: {instruction}"

    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=3200,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        try:
            parsed = json.loads(raw)
            updated = parsed.get("html", "")
            description = parsed.get("description", "Done.")
        except Exception:
            updated = raw
            description = "Done."
        # Strip accidental code fences
        if updated.startswith("```"):
            updated = updated.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # Convert any markdown that slipped through into HTML
        updated = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', updated)
        updated = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', updated)
        updated = _re.sub(r'(?m)^---+\s*$', '<hr>', updated)
        return jsonify({"updated_html": updated, "description": description})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cv-download/docx", methods=["POST"])
@auth.login_required
def api_cv_download_docx():
    """Generate a DOCX file that reflects the editor's real formatting."""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from flask import send_file
    import io
    from cv_export import parse_cv_html

    data = request.get_json(force=True)
    cv_html = (data.get("cv_html") or "").strip()
    content = (data.get("content") or "").strip()
    blocks = parse_cv_html(cv_html) if cv_html else []
    if not blocks and not content:
        return jsonify({"error": "No content provided."}), 400

    ALIGN_MAP = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    HEADING_SIZE = {"h1": 16, "h2": 13, "h3": 12}
    DOCX_MARGINS = {
        "narrow": (0.6, 0.6),
        "normal": (1.0, 1.15),
        "wide": (1.4, 1.6),
    }
    v_in, h_in = DOCX_MARGINS.get(data.get("margins"), DOCX_MARGINS["normal"])

    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(v_in)
        section.bottom_margin = Inches(v_in)
        section.left_margin = Inches(h_in)
        section.right_margin = Inches(h_in)

    if blocks:
        for block in blocks:
            if block["kind"] == "hr":
                p = doc.add_paragraph("─" * 40)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                continue

            p = doc.add_paragraph(style="List Bullet" if (block["kind"] == "li" and not block["ordered"]) else
                                         "List Number" if (block["kind"] == "li" and block["ordered"]) else None)
            p.paragraph_format.space_after = Pt(2)
            p.alignment = ALIGN_MAP.get(block["align"], WD_ALIGN_PARAGRAPH.LEFT)
            is_heading = block["kind"] in HEADING_SIZE
            for text, fmt in block["runs"]:
                run = p.add_run(text)
                run.font.name = "Arial"
                run.font.size = Pt(HEADING_SIZE.get(block["kind"], 11))
                run.bold = is_heading or fmt.get("bold", False)
                run.italic = fmt.get("italic", False)
                run.underline = fmt.get("underline", False)
                if fmt.get("color"):
                    try:
                        run.font.color.rgb = RGBColor.from_string(fmt["color"].lstrip("#"))
                    except Exception:
                        pass
    else:
        # Fallback for any caller still sending plain text.
        for line in content.split("\n"):
            stripped = line.strip()
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(stripped)
            run.font.name = "Arial"
            run.font.size = Pt(11)
            if stripped and len(stripped) < 60 and stripped.isupper():
                run.bold = True
                run.font.size = Pt(12)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     as_attachment=True, download_name="my-cv.docx")


def _render_cv_pdf_bytes(cv_html="", content="", margins=None):
    """Shared by the Builder's manual PDF download and the card-generated
    Document PDFs (Phase 4) -- same renderer, same ATS-safe output either
    way. Returns raw PDF bytes, or None if there's nothing to render."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    import io
    from cv_export import parse_cv_html

    cv_html = (cv_html or "").strip()
    content = (content or "").strip()
    blocks = parse_cv_html(cv_html) if cv_html else []
    if not blocks and not content:
        return None

    ALIGN_MAP = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY}
    FONT_SIZE = {"h1": 18, "h2": 13, "h3": 12}
    LEADING = {"h1": 24, "h2": 18, "h3": 17}
    PDF_MARGINS = {
        "narrow": (1.5, 1.5),
        "normal": (2.5, 2.8),
        "wide": (3.5, 4.0),
    }
    v_cm, h_cm = PDF_MARGINS.get(margins, PDF_MARGINS["normal"])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=v_cm*cm, bottomMargin=v_cm*cm,
                            leftMargin=h_cm*cm, rightMargin=h_cm*cm)

    def style_for(block):
        is_heading = block["kind"] in FONT_SIZE
        return ParagraphStyle(
            f"cv_{block['kind']}_{block['align']}",
            fontName="Helvetica-Bold" if is_heading else "Helvetica",
            fontSize=FONT_SIZE.get(block["kind"], 11),
            leading=LEADING.get(block["kind"], 16),
            spaceAfter=4 if is_heading else 2,
            spaceBefore=8 if is_heading else 0,
            alignment=ALIGN_MAP.get(block["align"], TA_LEFT),
            leftIndent=14 if block["kind"] == "li" else 0,
            bulletIndent=0,
        )

    story = []
    if blocks:
        ordered_counter = 0
        for block in blocks:
            if block["kind"] != "li" or not block["ordered"]:
                ordered_counter = 0  # reset whenever an ordered-list run breaks

            if block["kind"] == "hr":
                story.append(Spacer(1, 6))
                story.append(HRFlowable(width="100%", thickness=0.6, color="#999999"))
                story.append(Spacer(1, 6))
                continue
            markup = block["markup"]
            if block["kind"] == "li":
                if block["ordered"]:
                    ordered_counter += 1
                    prefix = f"{ordered_counter}. "
                else:
                    prefix = "•  "
                markup = prefix + markup
            story.append(Paragraph(markup, style_for(block)))
    else:
        # Fallback for any caller still sending plain text.
        normal = ParagraphStyle("cv_normal", fontName="Helvetica", fontSize=11, leading=16, spaceAfter=2)
        heading = ParagraphStyle("cv_heading", fontName="Helvetica-Bold", fontSize=12, leading=17, spaceAfter=4)
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 6))
            elif len(stripped) < 60 and stripped.isupper():
                story.append(Paragraph(stripped, heading))
            else:
                safe = stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(safe, normal))

    doc.build(story)
    buf.seek(0)
    return buf.read()


@app.route("/api/cv-download/pdf", methods=["POST"])
@auth.login_required
def api_cv_download_pdf():
    """Generate a PDF that reflects the editor's real formatting -- also
    used, unmodified, as the "print preview" (see cv-preview handling in
    dashboard.js: it fetches this same endpoint and opens the resulting
    blob in a new tab instead of downloading it, since a mobile browser's
    native PDF viewer only ever kicks in on direct navigation, not when
    a PDF is embedded in an iframe)."""
    from flask import send_file
    import io

    data = request.get_json(force=True)
    pdf_bytes = _render_cv_pdf_bytes(data.get("cv_html"), data.get("content"), data.get("margins"))
    if pdf_bytes is None:
        return jsonify({"error": "No content provided."}), 400
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name="my-cv.pdf")


# ---------------- Builder (CV / cover letter drafting) ----------------

@app.route("/builder")
@auth.login_required
def builder_page():
    """Reached only via chip 02 ("Build my CV" runs generation directly
    and shows the result as a Document card) or a card's "Edit" button --
    there's no separate nav entry for it. When it's an Edit link, ?doc=
    identifies which generated document to load into the editor."""
    user = auth.current_user()
    initial = None
    doc_id = request.args.get("doc")
    if doc_id:
        try:
            doc_row = db.get_document_by_id(user["id"], int(doc_id))
        except (TypeError, ValueError):
            doc_row = None
        if doc_row and doc_row.get("category") in ("generated_cv", "generated_letter"):
            initial = {
                "document_id": doc_row["id"],
                "kind": "cv" if doc_row["category"] == "generated_cv" else "letter",
                "html": doc_row.get("content") or "",
            }
    return render_template("builder.html", initial=initial)


@app.route("/api/letter-edit", methods=["POST"])
@auth.login_required
def api_letter_edit():
    """Same grounded-generation approach as /api/cv-edit, but for cover
    letters -- kept as its own endpoint (rather than a branch inside
    cv-edit) since the design guidance and system prompt are genuinely
    different documents, not just a formatting variant."""
    from openai import OpenAI
    user = auth.current_user()
    data = request.get_json(force=True)
    instruction = (data.get("instruction") or "").strip()
    letter_html = (data.get("letter_html") or "").strip()
    if not instruction:
        return jsonify({"error": "Missing instruction."}), 400

    state = pipeline.get_dashboard_state(user["id"])
    profile = state.get("profile", {})
    full_docs = db.get_documents_for_user(user["id"])
    doc_texts = []
    total_chars = 0
    DOC_CHAR_CAP = 3000
    TOTAL_CHAR_CAP = 20000
    for d in full_docs:
        if total_chars >= TOTAL_CHAR_CAP:
            break
        try:
            txt = (d.get("content") or "").strip()
            if not txt and d.get("stored_path") and os.path.exists(d["stored_path"]):
                txt = (extract.extract_text(d["stored_path"]) or "").strip()
            if txt:
                snippet = txt[:DOC_CHAR_CAP]
                doc_texts.append(f"[{d['filename']}]\n{snippet}")
                total_chars += len(snippet)
        except Exception:
            pass
    doc_content_block = "\n\n---\n\n".join(doc_texts) if doc_texts else "No documents uploaded yet."
    skill_names = [s["label"] for s in state.get("skills", [])]

    import re as _re
    system = (
        "You are an expert cover letter writer built into the Employable platform. "
        "CRITICAL: Always attempt to understand and fulfill the user's intent, even if their instruction contains spelling mistakes, typos, or imprecise phrasing. "
        "Never refuse, do nothing, or ask for clarification — silently make your best reasonable interpretation and act on it. "
        "OUTPUT RULES — you MUST follow these exactly:\n"
        "- Return a JSON object with exactly two keys: 'html' and 'description'.\n"
        "- 'html': the full updated cover letter as valid HTML. Use <p>, <strong>, <em>, <br> tags only "
        "(no lists, headings, or <hr> — a cover letter is plain prose paragraphs). "
        "NEVER use markdown asterisks, hyphens for bullets, --- separators, or backticks. "
        "No <html>, <head>, <body> wrappers. No code fences.\n"
        "- 'description': one short, specific sentence (max 20 words) describing exactly what you changed.\n\n"
        "DESIGN — a strong cover letter: a specific, non-generic opening line (never 'I am writing to "
        "apply for...'), 2-3 short paragraphs connecting the person's real, specific experience to what "
        "the role likely needs, and a brief confident closing with a call to action. Keep the whole letter "
        "under 300 words. Never pad with filler or generic enthusiasm ('I am a hard worker who is passionate "
        "about...') — every sentence should carry real information.\n\n"
        "GROUNDING — this is the most important rule, above all others: every fact in the letter "
        "(employer names, job titles, dates, achievements) must come from the user's real profile/documents "
        "below, or already be present in the current letter HTML. NEVER invent a person, career, employer, "
        "achievement, or biography that isn't actually theirs. If there isn't enough real information to "
        "write a grounded letter (no documents uploaded, or nothing usable in them), say so plainly in "
        "'description' and return the document unchanged rather than fabricating one.\n\n"
        f"This user's real profile:\n"
        f"- Name: {profile.get('full_name') or 'Unknown'}\n"
        f"- Location: {profile.get('location') or 'Not specified'}\n"
        f"- Skills: {', '.join(skill_names) if skill_names else 'None listed.'}\n\n"
        f"Full content of this user's actual uploaded documents (the ONLY source of truth for any letter "
        f"content you write):\n{doc_content_block}"
    )
    prompt = f"Current letter HTML:\n{letter_html if letter_html else '(empty — generate fresh content)'}\n\nInstruction: {instruction}"

    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=1600,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        try:
            parsed = json.loads(raw)
            updated = parsed.get("html", "")
            description = parsed.get("description", "Done.")
        except Exception:
            updated = raw
            description = "Done."
        if updated.startswith("```"):
            updated = updated.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        updated = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', updated)
        updated = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', updated)
        return jsonify({"updated_html": updated, "description": description})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
@auth.login_required
def api_chat():
    from openai import OpenAI
    user = auth.current_user()
    data = request.get_json(force=True)
    messages_in = data.get("messages", [])

    full_docs = db.get_documents_for_user(user["id"])
    doc_names = ", ".join(d["filename"] for d in full_docs) if full_docs else "None uploaded."

    # Prefer content already extracted at upload time and stored in the
    # DB (works on Vercel, where the upload directory is ephemeral and
    # won't still have the file by the time a later chat request comes
    # in). Only fall back to re-extracting from disk if that's empty and
    # the file still happens to exist locally. Cap at 3000 chars/doc and
    # 20000 chars total to stay within token limits without silently
    # dropping every document once someone has uploaded a handful.
    doc_texts = []
    total_chars = 0
    DOC_CHAR_CAP = 3000
    TOTAL_CHAR_CAP = 20000
    for d in full_docs:
        if total_chars >= TOTAL_CHAR_CAP:
            break
        try:
            txt = (d.get("content") or "").strip()
            if not txt and d.get("stored_path") and os.path.exists(d["stored_path"]):
                txt = (extract.extract_text(d["stored_path"]) or "").strip()
            if txt:
                snippet = txt[:DOC_CHAR_CAP]
                doc_texts.append(f"[{d['filename']}]\n{snippet}")
                total_chars += len(snippet)
        except Exception:
            pass
    doc_content_block = "\n\n---\n\n".join(doc_texts) if doc_texts else "No document content available."

    # Notes are private by default -- this block is only ever built (and
    # only ever reaches the prompt below) when the client explicitly
    # sends use_notes: true for THIS message, matching the "Use Notes"
    # toggle in the attach sheet. Omitted entirely otherwise, not just
    # hidden -- the model never sees note content unless the user turned
    # this on for that specific message.
    notes_section = ""
    if data.get("use_notes"):
        user_notes = db.get_notes_for_user(user["id"])
        if user_notes:
            notes_lines = "\n\n".join(
                f"[{n.get('title') or 'Untitled'}]\n{(n.get('body') or '').strip()[:2000]}"
                for n in user_notes[:30] if (n.get("title") or n.get("body"))
            )
            notes_section = f"\n\nThe user has turned on \"Use Notes\" for this message, so you may refer to their personal notes below when relevant:\n{notes_lines}\n"

    system_prompt = f"""You are Ploy — a chat-first AI career weapon for South African job seekers aged 18-25. You exist for one loop and everything you do serves it: paste a job, get a brutal-honest fit verdict, get the CV or cover letter rewritten for that exact job, download it, apply.

Voice: sharp, confident, on the user's side, zero corporate filler. Talk like a smart friend who won't waste their time, not a customer-service bot.
- Never say "I'd be happy to help!", "Great question!", "As an AI...". Never lay out a menu of paths and ask them to pick — pick the most likely direction yourself and go there.
- DEFAULT LENGTH IS 1-3 SENTENCES. Only go longer when the user asks for depth or you're walking through something genuinely multi-step.
- Be honest even when it stings — if their fit is weak or their CV has a real problem, say so plainly. Vague encouragement helps nobody and isn't what this product is for.
- Reference what's actually in THEIR documents, not generic examples. Never invent CV details that aren't there.
- Fragments and one-word reactions are fine. Vary rhythm like a real person, not uniform sentences.

The three moves this app is built around — steer the user toward whichever is the useful next step, don't just wait to be asked:
- "Am I a fit for this job?" — paste a job ad, get a fit verdict scored against their real CV.
- "Build my CV" — rewrite their CV tailored to a specific job.
- "What's holding me back?" — a gap analysis against the roles they're targeting.

FORMATTING — this chat only renders bold text and paragraph breaks correctly if you produce them exactly like this:
- Bold: wrap in double asterisks like **this**. Never single asterisks, underscores, or headers.
- New paragraph or list item: a genuine blank line before it, not just a line break.

QUICK REPLIES — after a substantive reply (not a greeting, not a one-word reaction), end with one line in exactly this format so the app can render it as tappable buttons for the user's next move:
[[QUICK_REPLIES]] Option one | Option two
Give 1-3 short options (2-5 words each, phrased as something the user would tap), specific to what you just said — never generic. Omit this line entirely for greetings, small talk, or when there's no clear next action.

This user's name: {user.get('full_name') or 'Unknown'}
Roles they're targeting: {user.get('target_field') or 'Not specified — if this matters for what they just asked (e.g. a gap analysis), ask which roles they mean before answering.'}
Documents uploaded: {doc_names}

Full content of their uploaded documents (ground every piece of advice in this, it's the only source of truth about their real experience):
{doc_content_block}
{notes_section}
Never claim you can't see their documents — if the block above says no content is available, say that plainly instead of guessing. If asked something off-topic, engage briefly and human, then steer back to the job hunt."""

    import re as _re, base64 as _b64
    openai_messages = [{"role": "system", "content": system_prompt}]
    has_images = False
    for m in messages_in:
        role = "user" if m.get("role") == "user" else "assistant"
        text = m.get("text", "")
        attachment_ids = m.get("attachment_ids", [])

        if attachment_ids and role == "user":
            content = []
            extra_text_blocks = []
            for att_id in attachment_ids:
                try:
                    att = db.get_chat_attachment(user["id"], int(att_id))
                except Exception:
                    att = None
                if not att:
                    continue
                if att["mime_type"].startswith("image/"):
                    with open(att["stored_path"], "rb") as img_f:
                        b64 = _b64.b64encode(img_f.read()).decode("utf-8")
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{att['mime_type']};base64,{b64}", "detail": "high"}
                    })
                    has_images = True
                elif att["text_content"]:
                    extra_text_blocks.append(f"[Attached file: {att['filename']}]\n{att['text_content'][:4000]}")

            # Strip the [Attached: filename] suffix added by frontend for display — send clean text to AI
            full_text = _re.sub(r'\s*\[Attached:[^\]]+\]', '', text or '').strip()
            if extra_text_blocks:
                full_text = (full_text + "\n\n" if full_text else "") + "\n\n".join(extra_text_blocks)
            if full_text:
                content.insert(0, {"type": "text", "text": full_text})
            elif not content:
                content = [{"type": "text", "text": "Please analyse the attached content."}]

            openai_messages.append({"role": role, "content": content})
        else:
            openai_messages.append({"role": role, "content": text or " "})

    model_name = "gpt-4o" if has_images else "gpt-4o-mini"

    # Real token streaming: the request to OpenAI (and any auth/key error)
    # happens synchronously in .create(), so a bad key still surfaces as a
    # normal JSON error response here -- only once that's succeeded do we
    # switch to a streamed plain-text body the client reads incrementally.
    # If the underlying platform buffers the response instead of flushing
    # it chunk-by-chunk, this just degrades to the client receiving it all
    # at once -- still correct, just not progressively rendered.
    try:
        client = OpenAI(api_key=analyzer.get_openai_api_key(), timeout=analyzer.get_client_timeout(), max_retries=analyzer.CLIENT_MAX_RETRIES)
        stream = client.chat.completions.create(
            model=model_name,
            messages=openai_messages,
            max_tokens=800,
            temperature=0.8,
            stream=True,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    def generate():
        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            yield "\n\n[[STREAM_ERROR]]" + str(e)

    return Response(stream_with_context(generate()), mimetype="text/plain")


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)

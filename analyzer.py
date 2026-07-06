"""
analyzer.py
-----------
Sends combined CV/document text — plus any extra context the user
types in — to the OpenAI API and gets back a strict JSON object
matching the shape the Dashboard UI needs.

THIS VERSION implements the Employability Rating Engine spec:
- Evidence-based scoring (claims without supporting docs count less)
- Counter-logic (actively penalize MISSING evidence, not just reward
  present evidence)
- A separate Confidence Score (0-100%), independent of the
  employability score itself
- New report sections: evidence_summary, missing_evidence,
  career_competitiveness, interview_readiness, improvement_roadmap

Install:
    pip install openai python-dotenv

Setup:
    Create a file named `.env` next to this script containing:
        OPENAI_API_KEY=sk-...your-key...
    Never commit that file or hardcode the key in source.
"""

import json
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from rubric import (
    DIMENSIONS,
    build_rubric_prompt_block,
    weighted_overall,
    label_for_score,
    stars_for_score,
    confidence_band,
    validate_scores,
    count_quantified_achievements,
    estimate_unexplained_gap_months,
    floor_documentation_strength,
)
from cache import get_cached, store_result

load_dotenv()  # reads OPENAI_API_KEY out of a local .env file

# Model choice: gpt-4o gives meaningfully better reasoning on messy,
# multi-document evidence chains than gpt-4o-mini — this spec asks the
# model to cross-reference claims across documents and reason about
# real-world hiring judgment, which is exactly the kind of task that
# benefits from the bigger model. Costs more per run; ENSEMBLE_RUNS below
# multiplies that cost, so this is a deliberate accuracy-over-cost choice.
MODEL = "gpt-4o"

# DRIFT AUDIT: on a small random sample of cache HITS, silently re-call
# the API in the background to check whether the model still agrees
# with what's cached, WITHOUT making the user wait for it or showing
# them two numbers. See _maybe_audit_cache_hit() below for the full
# reasoning — this exists instead of re-calling the API on every cache
# hit, because doing it every time defeats the entire point of caching
# (cost and speed) for a check you'd only ever act on rarely.
AUDIT_SAMPLE_RATE = 0.02  # ~1 in 50 cache hits gets silently re-checked
DRIFT_LOG_PATH = "/tmp/drift_log.jsonl" if os.environ.get("VERCEL") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "drift_log.jsonl")
DRIFT_SCORE_THRESHOLD = 1.0  # dimension points apart before logging as drift
DRIFT_CONFIDENCE_THRESHOLD = 10.0  # confidence_score points apart before logging

# ENSEMBLE SCORING (cache MISSES only): a cache hit is already guaranteed
# identical to itself — re-running it would cost money for zero benefit.
# A cache miss is a genuinely new document, and that's exactly where a
# single noisy API call risks becoming "the" cached answer forever. So
# on a miss, run the same prompt ENSEMBLE_RUNS times and blend the
# results (see _blend_ensemble_runs below) before ever writing to the
# cache. This intentionally folds the separate background drift-audit
# idea into the miss path: independent samples already give you
# per-dimension spread data for free, so there's no need for a second
# re-check on top of it. The background audit in
# _maybe_audit_cache_hit/_audit_once_against_cache is left untouched and
# still runs (rarely) on cache HITS, where ensembling doesn't apply.
#
# Lowered from 7 -> 3: 7 concurrent full-size gpt-4o calls on the same
# API key routinely queue/throttle each other, which — combined with no
# output cap (see ANALYSIS_MAX_TOKENS below) — is what was pushing a
# first-time (cache-miss) analysis well past 30 seconds. 3 independent
# samples still gives real mode-based consensus for consistency, just
# with less concurrency contention.
ENSEMBLE_RUNS = 3
# If fewer than this many of the ENSEMBLE_RUNS calls succeed, the sample
# is too thin to trust a blended answer — raise instead of silently
# blending a couple of results and calling it an "ensemble."
ENSEMBLE_MIN_SUCCESSFUL = 2

# Hard cap on generated output per scoring call. Generation time scales
# with output length far more than input length for gpt-4o, and the
# requested JSON schema (8 dimensions + several 3-item lists + up to 10
# roadmap entries) had no limit before — an uncapped, verbose response
# was the other big contributor to slow analyses.
ANALYSIS_MAX_TOKENS = 2200

# The openai SDK's default connect timeout (5s) is tight for a cold-started
# serverless function opening several concurrent HTTPS connections at once
# (see ENSEMBLE_RUNS above) — under that kind of cold-start contention, a
# slow DNS/TLS handshake can exceed 5s well before the model has even started
# generating anything, surfacing as a raw "Connection error" with no useful
# detail. The function itself has a generous 300s execution budget, so there
# is no reason to keep such a tight connect timeout; give it real headroom.
CLIENT_TIMEOUT = httpx.Timeout(90.0, connect=20.0)
CLIENT_MAX_RETRIES = 3


def _build_system_prompt() -> str:
    """
    Inserts the rubric from rubric.py directly into the prompt, plus the
    counter-logic / evidence-weighting / confidence-scoring instructions
    that make this an "Employability Rating Engine" rather than a plain
    CV grader. If you add/remove a dimension in rubric.py, the JSON
    schema's dimension_examples block below updates automatically — you
    don't need to hand-edit anything here for that.
    """
    rubric_block = build_rubric_prompt_block()
    dimension_examples = ",\n    ".join(
        f'{{"label": "{d.label}", "score": number, "description": string, "simple_explanation": string}}'
        for d in DIMENSIONS
    )

    return f"""You are an Employability Rating Engine — a professional \
employability assessor, not a CV reviewer. Your job is to determine how \
employable an applicant is based on ACTUAL EVIDENCE found across every \
uploaded document (CVs, certificates, transcripts, references, licenses, \
portfolios, cover letters, or any other supporting material), not on \
assumptions, opinions, or visual design alone. Every score, deduction, \
recommendation, and conclusion you produce must be directly tied to \
evidence you can point to in the provided text. Do not invent facts, \
employers, schools, dates, or contact details that are not present — \
leave those fields as empty strings if you cannot find them.

MANDATORY TWO-STAGE PROCESS — DO NOT SKIP STAGE 1:
Score drift between identical runs happens when a model jumps straight \
from raw document text to a number, forming a vague holistic impression \
and then picking a score that "feels right." You must NOT do this. \
Instead:

STAGE 1 — EXTRACT FIRST. Before assigning any score, populate the \
"extracted_facts" object in the JSON schema below with the concrete, \
literal facts you find in the documents: total years of experience \
(computed from literal dates, not estimated), number of employers, \
highest qualification found, count of supporting documents by type, \
count of skills with corroborating evidence vs. count of skills with \
none, count of quantified achievements (numbers/percentages/currency \
tied to a result) vs. count of plain duty statements, and any explicit \
target role/field stated or clearly implied. These facts are the ONLY \
inputs you are allowed to use in Stage 2 — if a fact is not in \
extracted_facts, it cannot influence a dimension score.

CRITICAL DISTINCTION FOR supporting_document_count_by_type — READ THIS \
CAREFULLY, this is the single most common scoring error: a "certificate" \
in this count means a SEPARATE, INDEPENDENTLY UPLOADED FILE (e.g. a file \
named "RE5_certificate.pdf" or a distinct === FILE: === block containing \
an actual certificate/transcript/reference letter image or document). It \
does NOT mean a qualification, certification, or course that is merely \
MENTIONED OR LISTED AS TEXT inside the CV itself (e.g. a CV's "Education \
& Certifications" section listing "RE5 Regulatory Examination — 2019" is \
a CLAIM written in the CV, worth zero toward this count, not a \
certificate). If only one file was uploaded — the CV itself — then \
supporting_document_count_by_type must be all zeros, no matter how many \
qualifications, certifications, or achievements that CV's text lists, \
because none of them have been independently verified by a separate \
document. Count === FILE: === blocks (or equivalent document boundaries) \
in the input, not bullet points within one document, to determine this.

CRITICAL DISTINCTION FOR quantified_achievement_count — apply the same \
literalism here: a quantified achievement requires an actual number, \
percentage, or currency figure tied to a specific result (e.g. "grew \
the territory's policy base by 22%" or "closed R450,000 in new premiums \
in Q2"). Phrases like "achieved monthly sales targets," "consistently \
met KPIs," or "achieved sales targets" contain the WORD achieved/target \
but contain NO actual number and must be counted as plain duty \
statements, not quantified achievements. If you cannot point to a \
specific digit or percentage in the sentence, it is not a quantified \
achievement, regardless of how achievement-flavored the wording sounds.

STAGE 2 — SCORE FROM THE EXTRACTED FACTS, NOT FROM THE ORIGINAL TEXT. \
For each dimension below, look up which anchor band the EXTRACTED FACTS \
(not your general impression of the document) match, and assign a score \
inside that band. Do not re-read the original prose for "vibes" at this \
stage — score only what you already wrote down in extracted_facts. This \
is what makes two runs over the same input produce the same output: the \
extraction step is the only place interpretation happens, and the \
scoring step after it is a mechanical lookup.

COUNTER-LOGIC — apply this throughout extraction and scoring:
- Actively search for MISSING evidence, not only present evidence. The \
absence of a document type (references, certifications, qualifications, \
portfolio) is itself a finding that should lower relevant scores and be \
named explicitly in missing_evidence.
- If only a CV was uploaded with no supporting documents, every \
dimension score must reflect that — do not score as if claims are \
verified when they are not. HOWEVER, "no supporting documents" and \
"barely any usable CV" are NOT the same finding and must not be \
collapsed into the same score. For Documentation Strength specifically: \
a complete, well-structured, fully readable CV with zero supporting \
documents belongs in the 3-4 band, by definition — NOT the 0-2 band. \
The 0-2 band is reserved ONLY for CVs that are themselves partial, \
unreadable, or too thin to assess (a fragment, a bad scan, missing \
sections) — it is not a "harsher" version of CV-only, it is a \
different, worse condition. If the CV is complete and readable, \
having no extra documents caps this dimension at 3-4, it does not \
drop it to 0-2.
- A claim made in the CV with no supporting document anywhere in the \
upload set must contribute LESS to a dimension's score than the same \
claim backed by a certificate, reference, transcript, license, award, or \
other verifiable document. Treat unverified claims as lower-confidence, \
not as equivalent to verified ones.
- Someone with 8 verified years of relevant experience should score \
meaningfully higher on Experience Strength than someone with 2 years, \
and someone with 10 years of UNRELATED experience should score lower on \
relevance-sensitive dimensions than someone with 5 years of directly \
relevant experience.
- Unexplained employment gaps are a real, material factor a human \
recruiter would notice and weigh — do not ignore \
employment_gap_months_unexplained once you've computed it. A gap of 6+ \
unexplained months should visibly cap Experience Strength and Career \
Progression below where the same work history would land with no gap \
(typically one band lower than it would otherwise sit), not just be \
mentioned in passing text. A short gap (under 3 months) between roles is \
normal and should not be penalised at all.

REAL-WORLD HIRING JUDGMENT — you are not a lenient CV-writing coach, you \
are the honest, experienced hiring manager this candidate will actually \
face. Recruiters and ATS systems reject candidates constantly for \
reasons candidates rarely hear stated plainly. Your job is to say the \
things a recruiter would think but usually won't say to the candidate's \
face:
- Be specific and blunt in critical_issues, hindrances, and \
missing_evidence — name the actual gap, not a softened version of it. \
"Limited demonstrated impact" is weak; "Every bullet point describes a \
duty, none describe a result — a recruiter has no way to tell if this \
person's work actually worked" is the standard to write to.
- If a profile would realistically get auto-rejected by ATS keyword \
filtering, or would not survive a recruiter's 6-second initial scan, or \
would raise an immediate credibility question in an interview, say so \
explicitly in critical_issues or interview_readiness — this is exactly \
the kind of real consequence a candidate needs to hear and cannot infer \
from a polite score alone.
- Grade Market Competitiveness against what the stated or implied target \
role ACTUALLY requires in the real labour market today, not against a \
generic notion of "a good CV." Name the specific, concrete things a \
realistic competing applicant for that same role would likely already \
have that this candidate does not (a certification, a portfolio, a \
specific years-of-experience threshold, a specific tool) — vague filler \
like "could be more competitive" is not acceptable here.
- Do not manufacture praise to soften a low score, and do not manufacture \
criticism to justify a low score either — every working_well and \
critical_issues entry must trace to something specifically named in \
extracted_facts or directly quotable from the documents. Honesty here \
means calibrated to the actual evidence in front of you, not pessimism \
for its own sake and not kindness for its own sake.

SCORING CONSISTENCY — this matters as much as the rubric itself:
- If given the same documents again, your scores must be identical or \
vary by no more than ±0.10. The mechanism for this is the two-stage \
process above — extract literal facts once, then score those facts \
mechanically against the anchor bands. Do not form a general impression \
of the candidate first and then search for a number to justify it.
- Use whole or half-point scores only (e.g. 5.0, 5.5, 6.0) — not \
arbitrary decimals like 7.3. A precise-looking decimal implies a level \
of fine-grained discrimination this process cannot actually deliver \
consistently between runs; coarser increments are MORE accurate to what \
this method can reliably tell you, not less.
- The average applicant — a typical CV with limited or no supporting \
documentation — should land between 3.50 and 6.50 overall. Reserve \
scores above 7.50 for applicants with genuinely strong, well-verified \
profiles (substantial relevant experience, relevant qualifications, AND \
real supporting documentation). Do not inflate scores out of politeness.

EMPLOYABILITY RATING ENGINE — DIMENSIONS:

{rubric_block}

CONFIDENCE SCORE (0-100, separate from the employability score):
Represents how much evidence exists to support this assessment, NOT how \
employable the applicant is. A CV-only submission should produce a \
confidence_score between 40 and 60. A submission with a CV plus \
qualifications, references, licenses, portfolio, or other supporting \
documents should produce a confidence_score above 85, scaling with how \
many independent document types are present and how well they \
corroborate the CV's claims.

If extra context is provided by the candidate (e.g. a target role, career \
stage, or things to disregard), weigh Market Competitiveness specifically \
against that stated context rather than guessing the target role from the \
documents alone.

For every recommendation or finding, reference the SPECIFIC evidence you \
found or could not find — never generic filler like "improve your CV." \
For example: "Six years of retail experience identified but no measurable \
achievements found" or "Grade 12 certificate present but no industry \
certification uploaded" or "Strong employment continuity but no reference \
letters uploaded."

Respond with ONLY a single JSON object — no markdown fences, no preamble, \
no commentary — matching exactly this schema:

{{
  "extracted_facts": {{
    "total_years_experience": number,
    "number_of_employers": integer,
    "highest_qualification": string,
    "supporting_document_count_by_type": {{"certificates": integer, "references": integer, "transcripts": integer, "portfolio_items": integer, "other": integer}},
    "skills_with_evidence_count": integer,
    "skills_without_evidence_count": integer,
    "skills_evidenced_in_work_history": [string, ...],
    "quantified_achievement_count": integer,
    "plain_duty_statement_count": integer,
    "target_role_or_field": string,
    "employment_gap_months_unexplained": integer,
    "contradictions_detected": [string, ...]
  }},
  "full_name": string,
  "headline": string,
  "email": string,
  "location": string,
  "skills": [string, ...],
  "overall_rating": number,
  "rating_label": string,
  "star_rating": integer,
  "confidence_score": number,
  "dimensions": [
    {dimension_examples}
  ],
  "evidence_summary": [string, string, string],
  "working_well": [string, string, string],
  "critical_issues": [string, string, string],
  "hindrances": [string, string, string],
  "missing_evidence": [string, string, string],
  "career_competitiveness": [string, string, string],
  "interview_readiness": string,
  "key_actions": [string, string, string],
  "improvement_roadmap": [
    {{
      "what": string,
      "why": string,
      "how": string,
      "dimension": string,
      "projected_score_gain": number
    }}
  ]
}}

SKILL CROSS-REFERENCING INSTRUCTION: When populating \
skills_evidenced_in_work_history, list only skills that explicitly appear \
in the Work Experience or Employment sections of the document (mentioned \
in a job description, project, or achievement), NOT skills that only \
appear in a standalone skills list or skills section. A skill "evidenced \
in work history" means it was actually used in a documented role, not \
merely claimed.

CONTRADICTIONS INSTRUCTION: Populate contradictions_detected with any \
factual inconsistencies you find across documents — for example: dates \
that imply more experience than the stated years, a qualification claimed \
in the CV but contradicted by a transcript, or a job title that conflicts \
with a reference letter. Be specific (e.g. "CV claims 8 years experience \
but employment dates total 5 years"). Empty array if none found.

SIMPLE_EXPLANATION INSTRUCTION: For every dimension in "dimensions", \
"simple_explanation" must restate the EXACT SAME finding as that \
dimension's "description" — same facts, same conclusion, zero new \
information — but in extremely plain, jargon-free language a young \
child could follow. One short paragraph (2-3 short sentences), a \
concrete everyday comparison if it helps (a puzzle, a backpack, a \
game score, building blocks), and never any career-jargon words like \
"evidence," "credibility," "competitiveness," or "documentation." If \
"description" says a claim is unverified, "simple_explanation" says \
something like "you said it, but nothing proves it yet" — same idea, \
kid-simple words.\n\
\n\
ROADMAP INSTRUCTION: Each improvement_roadmap entry must explain: \
"what" (the specific gap or missing element, naming actual content from \
the documents), "why" (precisely why this matters for employability in \
the candidate's field — name the real-world consequence of NOT fixing \
it), "how" (concrete, actionable steps the candidate can take — specific \
enough that they could act on it immediately), "dimension" (which of the \
8 Cubic-Metric dimensions this primarily improves), and \
"projected_score_gain" (honest estimate of overall score impact, 0-10 \
scale, proportional to the rubric weights — do not inflate). Generate \
at least 3 roadmap items and up to 5, ordered from highest to lowest \
projected gain. Items should cover different dimensions where possible. \
Keep "why" and "how" each to one concise sentence — specific, not padded.

Note: overall_rating, rating_label, and star_rating in your response are \
advisory — the application recomputes these from your dimension scores \
using its own weighting, so focus your effort on accurate, well-justified \
per-dimension scores rather than the overall number. confidence_score, \
however, is used exactly as you return it — apply the confidence \
guidance above carefully.

Scores in "dimensions" must be numbers between 0 and 10, in increments of \
0.5 only (e.g. 5.0, 5.5, 6.0 — never 7.3 or 6.1), falling inside the \
anchor band that the corresponding extracted_facts values match. \
Descriptions and bullet points should be specific to the actual document \
content and constructive rather than generic filler."""


@dataclass
class Dimension:
    label: str
    score: float
    description: str
    simple_explanation: str = ""


@dataclass
class RoadmapItem:
    what: str = ""
    why: str = ""
    how: str = ""
    dimension: str = ""
    projected_score_gain: float = 0.0
    action: str = ""  # backward compat: populated from what for old cached data


@dataclass
class CVAnalysis:
    full_name: str = "Job Seeker"
    headline: str = "Aspiring Professional"
    email: str = ""
    location: str = ""
    skills: list = field(default_factory=list)
    overall_rating: float = 0.0
    rating_label: str = "Unrated"
    star_rating: int = 0
    confidence_score: float = 0.0
    confidence_label: str = "Very Low Confidence"
    extracted_facts: dict = field(default_factory=dict)
    dimensions: list = field(default_factory=list)            # list[Dimension]
    evidence_summary: list = field(default_factory=list)
    working_well: list = field(default_factory=list)
    critical_issues: list = field(default_factory=list)
    hindrances: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)
    career_competitiveness: list = field(default_factory=list)
    interview_readiness: str = ""
    key_actions: list = field(default_factory=list)
    improvement_roadmap: list = field(default_factory=list)   # list[RoadmapItem]

    @classmethod
    def from_json_dict(cls, data: dict, raw_text: str = "") -> "CVAnalysis":
        dims = [
            Dimension(
                label=d.get("label", ""),
                score=float(d.get("score", 0)),
                description=d.get("description", ""),
                simple_explanation=d.get("simple_explanation", ""),
            )
            for d in data.get("dimensions", [])
        ]

        # Mechanical backstop: a model that has just been instructed to
        # penalize missing evidence can drift a CV-only-but-complete
        # submission down into the "fragmentary/unreadable" 0-2 band
        # instead of the correct 3-4 "CV only" band. Floor it using the
        # same independently-verified facts the model itself extracted.
        extracted_facts = data.get("extracted_facts", {}) or {}
        doc_counts = extracted_facts.get("supporting_document_count_by_type", {}) or {}
        for dim in dims:
            if dim.label == "Documentation Strength":
                dim.score = floor_documentation_strength(
                    dim.score, doc_counts, raw_text
                )

        # Recompute overall/label/stars from the dimension scores via
        # rubric.py's weights, instead of trusting the model's own
        # overall_rating verbatim. Falls back to the model's numbers
        # only if dims came back empty (shouldn't normally happen).
        score_map = {d.label: d.score for d in dims}
        if score_map:
            overall = weighted_overall(score_map)
            rating_label = label_for_score(overall)
            star_rating = stars_for_score(overall)
        else:
            overall = round(float(data.get("overall_rating", 0) or 0), 2)
            rating_label = data.get("rating_label") or "Unrated"
            star_rating = int(data.get("star_rating", 0) or 0)

        confidence = max(0.0, min(100.0, float(data.get("confidence_score", 0) or 0)))

        roadmap = [
            RoadmapItem(
                what=item.get("what") or item.get("action", ""),
                why=item.get("why", ""),
                how=item.get("how", ""),
                dimension=item.get("dimension", ""),
                projected_score_gain=float(item.get("projected_score_gain", 0) or 0),
                action=item.get("action") or item.get("what", ""),
            )
            for item in data.get("improvement_roadmap", []) or []
        ]

        return cls(
            full_name=data.get("full_name") or "Job Seeker",
            headline=data.get("headline") or "Aspiring Professional",
            email=data.get("email", ""),
            location=data.get("location", ""),
            skills=data.get("skills", []) or [],
            overall_rating=overall,
            rating_label=rating_label,
            star_rating=star_rating,
            confidence_score=confidence,
            confidence_label=confidence_band(confidence),
            extracted_facts=data.get("extracted_facts", {}) or {},
            dimensions=dims,
            evidence_summary=data.get("evidence_summary", []) or [],
            working_well=data.get("working_well", []) or [],
            critical_issues=data.get("critical_issues", []) or [],
            hindrances=data.get("hindrances", []) or [],
            missing_evidence=data.get("missing_evidence", []) or [],
            career_competitiveness=data.get("career_competitiveness", []) or [],
            interview_readiness=data.get("interview_readiness", "") or "",
            key_actions=data.get("key_actions", []) or [],
            improvement_roadmap=roadmap,
        )


class CVAnalyzerError(Exception):
    """Raised when the API call fails or returns something unusable."""


def _log_drift_event(event: dict) -> None:
    """Appends one JSON line to drift_log.jsonl. Never raises — a logging
    failure must never crash the audit thread or affect the user-facing
    request that's still running in parallel."""
    try:
        with open(DRIFT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError as e:
        print(f"Warning: could not write drift_log.jsonl: {e}")


def _audit_once_against_cache(user_content: str, cached_data: dict) -> None:
    """
    Runs in a background daemon thread. Makes exactly ONE fresh API call
    (no retry logic — this is a spot-check, not a user-facing request)
    using the same prompt/settings as the real call, then compares the
    result to what's currently cached for this exact input.

    Logs a drift event to drift_log.jsonl ONLY if a dimension score
    differs by more than DRIFT_SCORE_THRESHOLD, or confidence_score
    differs by more than DRIFT_CONFIDENCE_THRESHOLD. Agreement is not
    logged at all — drift_log.jsonl staying empty/small over time IS the
    reassurance signal; you're looking for entries appearing, not
    counting how many times it agreed.

    This intentionally does NOT touch the cache, does NOT change what
    any user sees, and does NOT raise exceptions outward — a failure
    here (network blip, API error) is logged and swallowed, never
    allowed to affect the real request that already returned.
    """
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return  # nothing to audit with; silently skip

        client = OpenAI(api_key=api_key, timeout=CLIENT_TIMEOUT, max_retries=CLIENT_MAX_RETRIES)
        system_prompt = _build_system_prompt()
        response = client.chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            seed=42,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        fresh_data = json.loads(response.choices[0].message.content)
    except Exception as e:
        _log_drift_event({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "audit_call_failed",
            "error": str(e),
        })
        return

    cached_dims = {d.get("label"): d.get("score") for d in cached_data.get("dimensions", [])}
    fresh_dims = {d.get("label"): d.get("score") for d in fresh_data.get("dimensions", [])}

    dimension_diffs = {}
    for label in set(cached_dims) | set(fresh_dims):
        cached_score = cached_dims.get(label)
        fresh_score = fresh_dims.get(label)
        if cached_score is None or fresh_score is None:
            continue
        try:
            diff = abs(float(cached_score) - float(fresh_score))
        except (TypeError, ValueError):
            continue
        if diff > DRIFT_SCORE_THRESHOLD:
            dimension_diffs[label] = {"cached": cached_score, "fresh": fresh_score, "diff": diff}

    confidence_diff = None
    try:
        confidence_diff = abs(
            float(cached_data.get("confidence_score", 0))
            - float(fresh_data.get("confidence_score", 0))
        )
    except (TypeError, ValueError):
        pass

    confidence_drifted = confidence_diff is not None and confidence_diff > DRIFT_CONFIDENCE_THRESHOLD

    if dimension_diffs or confidence_drifted:
        _log_drift_event({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "drift_detected",
            "dimension_diffs": dimension_diffs,
            "confidence_diff": confidence_diff,
            "full_name": cached_data.get("full_name", ""),
        })
        print(
            "DRIFT AUDIT: background re-check disagreed with cached result "
            "by more than the threshold. Logged to drift_log.jsonl — this "
            "did NOT change what any user saw."
        )
    # Agreement is intentionally not logged — see docstring above.


def _maybe_audit_cache_hit(user_content: str, cached_data: dict) -> None:
    """
    Called on every cache HIT. With probability AUDIT_SAMPLE_RATE, spins
    up a background daemon thread to silently re-check this cached
    result against a fresh API call. Returns immediately either way —
    this never blocks or slows down the user-facing response, since the
    cached result has already been returned by the time this runs.
    """
    if random.random() >= AUDIT_SAMPLE_RATE:
        return
    thread = threading.Thread(
        target=_audit_once_against_cache,
        args=(user_content, cached_data),
        daemon=True,
    )
    thread.start()


def _mode_with_mean_fallback(values: list) -> float:
    """
    The recurring value across a set of runs, if one exists; otherwise
    the mean. This is the blending rule for every scalar this module
    ensembles (dimension scores, confidence_score): if 3+ of 5 runs
    landed on the same number, that's the model's real answer and the
    odd ones out are noise. If every run disagrees, there's no
    consensus to fall back on, so the mean is the least-arbitrary
    summary of the spread. Ties on the mode are averaged together
    rather than picking one arbitrarily.
    """
    counts = Counter(values)
    top = max(counts.values())
    if top > 1:
        modes = [v for v, c in counts.items() if c == top]
        return sum(modes) / len(modes)
    return sum(values) / len(values)


def _round_to_half(x: float) -> float:
    return round(x * 2) / 2.0


def _blend_ensemble_runs(run_results: list) -> dict:
    """
    Combines N independent scoring runs over the SAME input into one
    blended result dict, in the same shape json.loads(raw) normally
    produces, so it can be cached and parsed exactly like a single-call
    response downstream.

    Per dimension: mode-with-mean-fallback across the runs that
    returned that label, rounded to the nearest 0.5 to stay inside the
    rubric's allowed increments. The kept description is borrowed from
    whichever individual run's score for that dimension landed closest
    to the blended value (ties broken by earliest run) — that run's
    wording is the most representative explanation of the number that
    was actually kept, rather than a generic blended sentence no run
    actually wrote.

    confidence_score gets the same mode-with-mean-fallback treatment,
    since it's just another model-produced scalar.

    Every other field — name/contact/skills, extracted_facts, the
    narrative lists (working_well, critical_issues, missing_evidence,
    etc.), interview_readiness, improvement_roadmap — comes from a
    single "representative" run: whichever run's own dimension scores
    were, on average, closest to the final blended dimension scores.
    That keeps the narrative sections internally consistent with one
    coherent read of the documents, instead of stitching sentences from
    five runs that may disagree on details the blended numbers don't
    capture.

    Also logs per-dimension spread across the runs to drift_log.jsonl
    whenever it exceeds the same thresholds the cache-hit background
    audit uses (DRIFT_SCORE_THRESHOLD / DRIFT_CONFIDENCE_THRESHOLD) —
    this is the "fold drift-audit into the ensemble" piece described in
    the module-level comment above ENSEMBLE_RUNS. The blended value is
    still what gets returned/cached either way; this is a visibility
    signal, not a correction.
    """
    labels = []
    for run in run_results:
        for d in run.get("dimensions", []) or []:
            label = d.get("label", "")
            if label and label not in labels:
                labels.append(label)

    blended_dimensions = []
    dimension_diffs_for_log = {}
    per_run_distance = [0.0] * len(run_results)
    per_run_count = [0] * len(run_results)

    for label in labels:
        scores_by_run = []  # (run_index, score, description, simple_explanation)
        for i, run in enumerate(run_results):
            for d in run.get("dimensions", []) or []:
                if d.get("label") == label:
                    try:
                        scores_by_run.append((
                            i,
                            float(d.get("score", 0)),
                            d.get("description", ""),
                            d.get("simple_explanation", ""),
                        ))
                    except (TypeError, ValueError):
                        pass
                    break

        if not scores_by_run:
            continue

        raw_scores = [s for _, s, _, _ in scores_by_run]
        blended_score = _round_to_half(_mode_with_mean_fallback(raw_scores))

        closest_run_idx, _, closest_desc, closest_simple = min(
            scores_by_run, key=lambda t: (abs(t[1] - blended_score), t[0])
        )

        blended_dimensions.append({
            "label": label,
            "score": blended_score,
            "description": closest_desc,
            "simple_explanation": closest_simple,
        })

        spread = max(raw_scores) - min(raw_scores)
        if spread > DRIFT_SCORE_THRESHOLD:
            dimension_diffs_for_log[label] = {
                "scores_across_runs": raw_scores,
                "spread": spread,
                "blended": blended_score,
            }

        for i, s, _, _ in scores_by_run:
            per_run_distance[i] += abs(s - blended_score)
            per_run_count[i] += 1

    confidence_values = []
    for run in run_results:
        try:
            confidence_values.append(float(run.get("confidence_score", 0) or 0))
        except (TypeError, ValueError):
            pass
    blended_confidence = _mode_with_mean_fallback(confidence_values) if confidence_values else 0.0
    confidence_spread = (max(confidence_values) - min(confidence_values)) if confidence_values else 0.0

    avg_distance = [
        (per_run_distance[i] / per_run_count[i]) if per_run_count[i] else float("inf")
        for i in range(len(run_results))
    ]
    representative_idx = min(range(len(run_results)), key=lambda i: avg_distance[i])
    representative = run_results[representative_idx]

    blended = dict(representative)  # start from the representative run's full shape
    blended["dimensions"] = blended_dimensions
    blended["confidence_score"] = blended_confidence

    if dimension_diffs_for_log or confidence_spread > DRIFT_CONFIDENCE_THRESHOLD:
        _log_drift_event({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "ensemble_spread",
            "runs": len(run_results),
            "dimension_diffs": dimension_diffs_for_log,
            "confidence_spread": confidence_spread,
            "full_name": representative.get("full_name", ""),
        })
        print(
            "ENSEMBLE SPREAD: one or more dimensions disagreed across the "
            f"{len(run_results)} scoring runs by more than the drift "
            "threshold. Logged to drift_log.jsonl — the blended (mode/"
            "mean) value was still used; this is a visibility signal, "
            "not a correction."
        )

    return blended


def _run_one_scoring_attempt(client, base_messages: list, verified_achievement_count: int, verified_gap_months: int) -> dict:
    """
    One full call-and-validate cycle: up to 2 attempts (same retry-on-
    invalid-response logic the old single-call path used), starting
    fresh from base_messages each time this function is called — each
    of the ENSEMBLE_RUNS ensemble runs gets its own independent
    conversation, not a shared/mutated one, so the runs are genuinely
    independent samples rather than turns of the same conversation.

    Returns the validated raw response dict on success. Raises
    CVAnalyzerError if both attempts fail validation.
    """
    messages = list(base_messages)
    last_problems = []
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                response_format={"type": "json_object"},
                temperature=0,
                seed=42,
                max_tokens=ANALYSIS_MAX_TOKENS,
                messages=messages,
            )
        except Exception as e:
            raise CVAnalyzerError(f"OpenAI API request failed: {e}") from e

        raw = response.choices[0].message.content

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CVAnalyzerError(f"Model did not return valid JSON: {e}") from e

        if "extracted_facts" in data and isinstance(data["extracted_facts"], dict):
            model_count = data["extracted_facts"].get("quantified_achievement_count")
            if model_count != verified_achievement_count:
                data["extracted_facts"]["quantified_achievement_count"] = verified_achievement_count
            model_gap = data["extracted_facts"].get("employment_gap_months_unexplained")
            if model_gap != verified_gap_months:
                data["extracted_facts"]["employment_gap_months_unexplained"] = verified_gap_months

        score_map = {
            d.get("label", ""): d.get("score", None)
            for d in data.get("dimensions", [])
        }
        is_valid, problems = validate_scores(score_map)

        if is_valid:
            return data

        last_problems = problems
        messages = messages[:2] + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your previous response had dimension scoring problems: "
                    + "; ".join(problems)
                    + ". Return the full corrected JSON object again, with "
                      "all eight dimensions present, correctly labeled "
                      "exactly as specified, and each score between 0 and 10."
                ),
            },
        ]

    raise CVAnalyzerError(
        f"Model returned invalid dimension scores after 2 attempts: {last_problems}"
    )


def analyze_documents(combined_text: str, extra_context: str = "") -> CVAnalysis:
    """
    Blocking call: sends combined_text (+ optional extra_context) to
    OpenAI, returns a parsed CVAnalysis. Call this from a background
    thread, not the Tkinter main thread.

    extra_context: free-text the user typed in — target role, career
    stage, things to ignore, anything. Pass "" if there's nothing to add;
    it's entirely optional and the analyzer works fine without it.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise CVAnalyzerError(
            "OPENAI_API_KEY not found. Create a .env file with "
            "OPENAI_API_KEY=sk-... next to your script."
        )

    client = OpenAI(api_key=api_key, timeout=CLIENT_TIMEOUT, max_retries=CLIENT_MAX_RETRIES)

    if not combined_text or not combined_text.strip():
        raise CVAnalyzerError("No readable text was extracted from the uploaded files.")

    user_content = combined_text[:60000]  # guard against huge uploads

    # PRE-COMPUTED VERIFIED FACT: count quantified achievements with a
    # real text scan BEFORE the model ever sees the document, and hand
    # it the answer as a stated fact rather than asking it to count and
    # correcting afterward. Correcting extracted_facts after the model
    # has already generated its dimension scores doesn't fix a score
    # that was computed from the wrong premise — the model needs the
    # right number going IN, not a correction coming out. See
    # rubric.py's count_quantified_achievements() docstring for why a
    # mechanical text scan is more reliable here than relying on the
    # model's own counting, even with explicit prompt instructions.
    verified_achievement_count = count_quantified_achievements(user_content)
    user_content = (
        f"--- VERIFIED FACT (pre-computed, do not recount) ---\n"
        f"A text-pattern scan of the document(s) below found exactly "
        f"{verified_achievement_count} bullet/line(s) containing a "
        f"genuine quantified result (an actual digit, percentage, or "
        f"currency figure tied to a specific outcome — not just an "
        f"achievement-flavored word like 'achieved' or 'exceeded' with "
        f"no number attached). You MUST set "
        f"extracted_facts.quantified_achievement_count to exactly "
        f"{verified_achievement_count} in your response — do not "
        f"recount or override this verified figure.\n"
        f"--- END VERIFIED FACT ---\n\n"
        f"{user_content}"
    )

    # Same reasoning as the achievement count above: date-range
    # arithmetic across a whole work history is a well-known LLM weak
    # spot (it will silently miss or invent gaps), so it's computed
    # mechanically here and handed over as a fact, not left to the
    # model's own counting.
    verified_gap_months = estimate_unexplained_gap_months(user_content)
    user_content = (
        f"--- VERIFIED FACT (pre-computed, do not recompute) ---\n"
        f"A date-range scan of the employment history below found "
        f"exactly {verified_gap_months} month(s) of unexplained gap "
        f"between roles (single-month rounding gaps are not counted). "
        f"You MUST set extracted_facts.employment_gap_months_unexplained "
        f"to exactly {verified_gap_months} in your response — do not "
        f"recompute or override this verified figure.\n"
        f"--- END VERIFIED FACT ---\n\n"
        f"{user_content}"
    )

    extra_context = (extra_context or "").strip()
    if extra_context:
        # Inserted as its own labeled block, ahead of the document text,
        # so the model treats it as instructions/context rather than
        # mistaking it for part of the CV itself.
        user_content = (
            f"--- CANDIDATE-PROVIDED CONTEXT ---\n{extra_context[:2000]}\n"
            f"--- END CONTEXT ---\n\n"
            f"--- DOCUMENT TEXT ---\n{user_content}"
        )

    # CACHE CHECK: hash the exact text being sent (post-truncation,
    # post-context-merge — i.e. the literal thing the model would see)
    # and look up a prior result. A hit means this exact input has been
    # scored before, so we return that exact same stored result with NO
    # new API call — this is the only way to GUARANTEE identical output
    # for identical input, since the API itself does not guarantee
    # bit-identical responses even at temperature=0 (see cache.py for
    # the full explanation). A miss falls through to a normal API call,
    # which then gets stored below for next time.
    cached_data = get_cached(user_content, "")
    if cached_data is not None:
        _maybe_audit_cache_hit(user_content, cached_data)
        return CVAnalysis.from_json_dict(cached_data, combined_text)

    system_prompt = _build_system_prompt()
    base_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # ENSEMBLE: run the same prompt ENSEMBLE_RUNS independent times and
    # blend the results (see _blend_ensemble_runs) rather than trusting
    # whichever single call happens to come back. Each run gets its own
    # up-to-2-attempt validation cycle via _run_one_scoring_attempt, same
    # as the old single-call path had — a run that fails both of its own
    # attempts is simply excluded from the ensemble rather than failing
    # the whole request, as long as enough other runs still succeed.
    #
    # The runs are independent of each other (same input, no shared
    # state), so they're fired concurrently via a thread pool instead of
    # one after another — each is I/O-bound waiting on the network, so
    # this cuts wall-clock time roughly from (N × per-call latency) down
    # to about (1 × per-call latency), without reducing the ensemble
    # size or the quality it buys.
    run_results = []
    run_errors = []
    with ThreadPoolExecutor(max_workers=ENSEMBLE_RUNS) as executor:
        futures = [
            executor.submit(_run_one_scoring_attempt, client, base_messages, verified_achievement_count, verified_gap_months)
            for _ in range(ENSEMBLE_RUNS)
        ]
        for future in as_completed(futures):
            try:
                run_results.append(future.result())
            except CVAnalyzerError as e:
                run_errors.append(str(e))

    if len(run_results) < ENSEMBLE_MIN_SUCCESSFUL:
        raise CVAnalyzerError(
            f"Only {len(run_results)} of {ENSEMBLE_RUNS} ensemble scoring "
            f"calls succeeded (need at least {ENSEMBLE_MIN_SUCCESSFUL}): "
            + "; ".join(run_errors)
        )

    blended_data = _blend_ensemble_runs(run_results)

    # Only cache once we have a blended result built from validated runs —
    # never cache a broken/incomplete response just because it parsed as
    # JSON. A bad result cached forever would be worse than no cache at all.
    store_result(user_content, "", blended_data)
    return CVAnalysis.from_json_dict(blended_data, combined_text)

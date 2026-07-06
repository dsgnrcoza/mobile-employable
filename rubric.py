"""
rubric.py
---------
The Employability Rating Engine's scoring rubric. Pure data/config — no
API calls, nothing here can break the OpenAI integration in analyzer.py.

8-DIMENSION EVIDENCE-BASED SPEC:
Documentation Strength, Experience Strength, Qualification Strength,
Skill Strength, Market Competitiveness, Evidence Credibility,
ATS Compatibility, Career Progression.

DETERMINISTIC SKILL SCORING:
Skill Strength is no longer scored by the AI. It is computed entirely in
Python using SKILL_MARKET_VALUES weights and the score_skill_set()
function below. This guarantees:
  - Zero skills → exactly 0.0 (no floor, no default, no baseline)
  - Adding/removing the same skill is perfectly symmetrical
  - High-demand skills (Python, AWS, AI) contribute far more than
    commodity skills (Typing, Filing, Internet browsing)
  - The score is deterministic: same skill list always produces the same number
  - Live updates: skill changes instantly reflect in scores without re-running AI

The AI still scores Skill Strength during analysis (for validation), but
pipeline.py replaces that score with the Python-computed value before
anything is shown to the user.

SCORING FORMULA (score_skill_set):
  raw_power = Σ market_value(skill_i) × evidence_multiplier(skill_i)
  skill_strength = 10 × (1 − exp(−raw_power / SKILL_NORMALIZATION))

This asymptotic function naturally:
  - Returns exactly 0.0 when raw_power = 0 (no skills)
  - Approaches 10.0 as more high-value skills accumulate
  - Is perfectly reversible: add then remove returns to prior state
  - Never exceeds 10.0 regardless of skill count
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


@dataclass
class RubricDimension:
    label: str
    definition: str
    weight: float = 1.0
    anchors: Dict[str, str] = field(default_factory=dict)


# ============================================================
# SKILL MARKET VALUE TABLE
# Weights represent current labour-market demand and employer value.
# Scale: 0.0 (worthless) → 1.0 (maximum demand / highest value).
# Calibration: ~10 high-value technical skills with evidence → ~8.5/10
# ============================================================

SKILL_MARKET_VALUES: Dict[str, float] = {
    # ── AI & Machine Learning (highest demand) ─────────────────────────
    "artificial intelligence": 1.0,
    "ai": 1.0,
    "machine learning": 1.0,
    "ml": 1.0,
    "deep learning": 1.0,
    "neural networks": 0.95,
    "natural language processing": 0.95,
    "nlp": 0.95,
    "computer vision": 0.95,
    "generative ai": 1.0,
    "large language models": 0.95,
    "llm": 0.95,
    "reinforcement learning": 0.9,
    "mlops": 0.9,
    "feature engineering": 0.85,
    "model deployment": 0.85,

    # ── Cloud Platforms ─────────────────────────────────────────────────
    "aws": 1.0,
    "amazon web services": 1.0,
    "azure": 0.95,
    "microsoft azure": 0.95,
    "google cloud": 0.95,
    "gcp": 0.95,
    "cloud computing": 0.9,
    "cloud architecture": 0.9,
    "cloud infrastructure": 0.9,
    "multi-cloud": 0.85,

    # ── DevOps & Infrastructure ──────────────────────────────────────────
    "devops": 0.95,
    "kubernetes": 0.95,
    "docker": 0.9,
    "terraform": 0.9,
    "ansible": 0.85,
    "ci/cd": 0.9,
    "jenkins": 0.8,
    "github actions": 0.85,
    "infrastructure as code": 0.9,
    "site reliability engineering": 0.9,
    "sre": 0.9,
    "linux": 0.8,
    "bash scripting": 0.75,
    "shell scripting": 0.75,

    # ── Cybersecurity ────────────────────────────────────────────────────
    "cybersecurity": 1.0,
    "cyber security": 1.0,
    "information security": 0.95,
    "infosec": 0.95,
    "penetration testing": 0.95,
    "ethical hacking": 0.9,
    "network security": 0.9,
    "soc": 0.85,
    "siem": 0.85,
    "vulnerability assessment": 0.85,
    "incident response": 0.85,
    "zero trust": 0.85,
    "cissp": 0.9,
    "ceh": 0.85,
    "oscp": 0.9,

    # ── Programming Languages ────────────────────────────────────────────
    "python": 1.0,
    "rust": 0.95,
    "golang": 0.9,
    "go": 0.9,
    "typescript": 0.9,
    "java": 0.85,
    "kotlin": 0.85,
    "swift": 0.85,
    "c++": 0.85,
    "c#": 0.8,
    "scala": 0.85,
    "r": 0.8,
    "julia": 0.8,
    "javascript": 0.8,
    "php": 0.6,
    "ruby": 0.65,
    "perl": 0.5,
    "vba": 0.45,
    "cobol": 0.4,

    # ── Web / Frontend ───────────────────────────────────────────────────
    "react": 0.85,
    "next.js": 0.85,
    "vue.js": 0.75,
    "angular": 0.75,
    "node.js": 0.8,
    "graphql": 0.8,
    "rest api": 0.75,
    "html": 0.5,
    "css": 0.5,
    "tailwind": 0.7,
    "sass": 0.6,

    # ── Data & Analytics ─────────────────────────────────────────────────
    "data science": 1.0,
    "data engineering": 0.95,
    "data analysis": 0.85,
    "data analytics": 0.85,
    "big data": 0.85,
    "sql": 0.85,
    "postgresql": 0.8,
    "mysql": 0.75,
    "nosql": 0.8,
    "mongodb": 0.75,
    "apache spark": 0.9,
    "hadoop": 0.8,
    "dbt": 0.85,
    "airflow": 0.85,
    "kafka": 0.85,
    "tableau": 0.75,
    "power bi": 0.75,
    "looker": 0.75,
    "data visualisation": 0.7,
    "data visualization": 0.7,
    "etl": 0.8,
    "data warehousing": 0.8,
    "snowflake": 0.85,
    "databricks": 0.85,
    "pandas": 0.8,
    "numpy": 0.75,
    "tensorflow": 0.85,
    "pytorch": 0.85,
    "scikit-learn": 0.8,

    # ── Finance & Accounting ─────────────────────────────────────────────
    "financial modelling": 0.9,
    "financial modeling": 0.9,
    "financial analysis": 0.85,
    "investment banking": 0.9,
    "equity research": 0.85,
    "risk management": 0.85,
    "quantitative analysis": 0.9,
    "derivatives": 0.85,
    "portfolio management": 0.85,
    "actuarial science": 0.9,
    "actuarial": 0.9,
    "ifrs": 0.8,
    "gaap": 0.8,
    "tax": 0.7,
    "auditing": 0.75,
    "bookkeeping": 0.55,
    "accounting": 0.65,
    "sage": 0.6,
    "xero": 0.65,
    "quickbooks": 0.6,
    "pastel": 0.55,

    # ── Insurance ────────────────────────────────────────────────────────
    "insurance": 0.65,
    "underwriting": 0.75,
    "claims management": 0.7,
    "policy administration": 0.65,
    "re1": 0.75,
    "re5": 0.75,
    "fais": 0.75,
    "short-term insurance": 0.65,
    "long-term insurance": 0.65,
    "life insurance": 0.65,
    "medical aid": 0.6,
    "broker": 0.6,
    "insurance sales": 0.6,
    "wealth management": 0.7,
    "financial advisory": 0.7,
    "financial planning": 0.7,
    "cfp": 0.8,

    # ── Healthcare ───────────────────────────────────────────────────────
    "clinical research": 0.85,
    "medical coding": 0.75,
    "icd-10": 0.7,
    "ehr": 0.7,
    "nursing": 0.75,
    "pharmacology": 0.75,
    "healthcare management": 0.75,

    # ── Engineering ──────────────────────────────────────────────────────
    "electrical engineering": 0.85,
    "mechanical engineering": 0.85,
    "civil engineering": 0.85,
    "chemical engineering": 0.85,
    "systems engineering": 0.85,
    "embedded systems": 0.85,
    "plc programming": 0.8,
    "autocad": 0.7,
    "solidworks": 0.75,
    "matlab": 0.75,
    "pcb design": 0.8,

    # ── Project / Product Management ─────────────────────────────────────
    "project management": 0.8,
    "product management": 0.85,
    "programme management": 0.8,
    "agile": 0.8,
    "scrum": 0.75,
    "kanban": 0.65,
    "safe": 0.75,
    "prince2": 0.75,
    "pmp": 0.8,
    "jira": 0.65,
    "confluence": 0.6,
    "roadmap planning": 0.7,
    "stakeholder management": 0.7,
    "requirements gathering": 0.65,
    "business analysis": 0.75,
    "change management": 0.7,

    # ── Marketing & Digital ──────────────────────────────────────────────
    "digital marketing": 0.75,
    "seo": 0.7,
    "sem": 0.7,
    "google ads": 0.7,
    "meta ads": 0.65,
    "social media marketing": 0.65,
    "content marketing": 0.6,
    "email marketing": 0.6,
    "marketing automation": 0.7,
    "hubspot": 0.65,
    "google analytics": 0.65,
    "crm": 0.65,
    "salesforce": 0.75,
    "copywriting": 0.6,
    "brand management": 0.65,
    "marketing": 0.6,

    # ── Sales ────────────────────────────────────────────────────────────
    "sales": 0.65,
    "b2b sales": 0.7,
    "b2c sales": 0.65,
    "enterprise sales": 0.75,
    "account management": 0.7,
    "business development": 0.75,
    "negotiation": 0.65,
    "client relationship management": 0.65,
    "customer relationship management": 0.65,
    "pipeline management": 0.65,
    "cold calling": 0.5,
    "telesales": 0.5,
    "inbound sales": 0.6,
    "outbound sales": 0.6,
    "sales closing techniques": 0.6,
    "inbound and outbound sales": 0.6,
    "policy sales": 0.6,

    # ── Operations & Supply Chain ────────────────────────────────────────
    "supply chain management": 0.8,
    "logistics": 0.7,
    "procurement": 0.7,
    "inventory management": 0.65,
    "erp": 0.75,
    "sap": 0.8,
    "oracle": 0.75,
    "lean": 0.7,
    "six sigma": 0.75,
    "quality management": 0.7,
    "iso": 0.65,

    # ── Legal & Compliance ───────────────────────────────────────────────
    "compliance": 0.75,
    "regulatory compliance": 0.75,
    "contract management": 0.7,
    "legal research": 0.7,
    "corporate law": 0.8,
    "gdpr": 0.75,
    "popia": 0.7,
    "kyc": 0.7,
    "aml": 0.75,

    # ── Design & Creative ────────────────────────────────────────────────
    "ux design": 0.8,
    "ui design": 0.8,
    "ux/ui": 0.8,
    "figma": 0.8,
    "sketch": 0.7,
    "adobe xd": 0.7,
    "graphic design": 0.65,
    "illustrator": 0.65,
    "photoshop": 0.65,
    "video editing": 0.6,
    "after effects": 0.65,

    # ── Soft / Professional skills (moderate value) ──────────────────────
    "leadership": 0.55,
    "team leadership": 0.6,
    "people management": 0.6,
    "strategic planning": 0.65,
    "critical thinking": 0.4,
    "problem solving": 0.4,
    "problem-solving": 0.4,
    "analytical thinking": 0.45,
    "decision making": 0.4,
    "communication": 0.35,
    "presentation": 0.4,
    "public speaking": 0.45,
    "written communication": 0.35,
    "report writing": 0.4,
    "research": 0.45,
    "training": 0.45,
    "coaching": 0.45,
    "mentoring": 0.45,
    "facilitation": 0.4,
    "conflict resolution": 0.4,
    "teamwork": 0.3,
    "collaboration": 0.3,
    "attention to detail": 0.3,
    "time management": 0.3,
    "organisation": 0.3,
    "organization": 0.3,
    "multitasking": 0.25,
    "adaptability": 0.3,
    "customer service": 0.45,
    "client service": 0.45,
    "client needs analysis": 0.5,
    "needs analysis": 0.45,
    "relationship management": 0.5,
    "relationship building": 0.45,
    "target-driven": 0.4,
    "target-driven performance": 0.4,
    "call centre": 0.4,
    "call centre operations": 0.4,

    # ── Basic office tools (low value) ──────────────────────────────────
    "microsoft office": 0.3,
    "ms office": 0.3,
    "microsoft excel": 0.5,
    "excel": 0.5,
    "advanced excel": 0.6,
    "microsoft word": 0.25,
    "word": 0.25,
    "microsoft powerpoint": 0.3,
    "powerpoint": 0.3,
    "outlook": 0.25,
    "google workspace": 0.35,
    "g suite": 0.35,

    # ── Very low value / near-zero ───────────────────────────────────────
    "typing": 0.15,
    "filing": 0.12,
    "data entry": 0.2,
    "internet browsing": 0.1,
    "email": 0.15,
    "faxing": 0.05,
    "photocopying": 0.05,
    "ms paint": 0.05,
    "microsoft paint": 0.05,
    "handyman": 0.2,
}

# Weight for any skill not found in the table
_DEFAULT_SKILL_WEIGHT = 0.3

# Logarithmic normalization constant: calibrated so that ~10 high-value
# technical skills (avg weight 0.9) without evidence gives ~7.2/10, and
# with evidence gives ~8.5/10. Adjust this value to recalibrate the curve.
SKILL_NORMALIZATION = 7.0

# Multiplier applied to skills backed by independently verified evidence
# (a certificate, reference, portfolio item, or separate uploaded document)
SKILL_EVIDENCE_MULTIPLIER = 1.4


def get_skill_market_value(label: str) -> float:
    """
    Returns the market-demand weight (0.0–1.0) for a skill label.

    Lookup order:
      1. Exact case-insensitive match
      2. Input contains a known high-value skill keyword (e.g. "Python
         Programming" matches "python")
      3. Known keyword contains the input as a meaningful substring
         (length ≥ 4 to avoid 'go' matching 'golang' accidentally)
      4. Default weight for unknown skills
    """
    lower = label.lower().strip()
    if not lower:
        return 0.0

    # 1. Exact match
    if lower in SKILL_MARKET_VALUES:
        return SKILL_MARKET_VALUES[lower]

    # 2. Input contains a known keyword (longer known keywords take priority)
    best_value = None
    best_len = 0
    for known, value in SKILL_MARKET_VALUES.items():
        if len(known) >= 4 and known in lower:
            if len(known) > best_len:
                best_len = len(known)
                best_value = value
    if best_value is not None:
        return best_value

    # 3. Known keyword contained in input (for single-word inputs like "python")
    for known, value in SKILL_MARKET_VALUES.items():
        if len(lower) >= 4 and lower in known:
            if best_value is None or value > best_value:
                best_value = value
    if best_value is not None:
        return best_value

    return _DEFAULT_SKILL_WEIGHT


def score_skill_set(
    skill_labels: List[str],
    evidenced_labels: Set[str] = None,
) -> float:
    """
    Deterministic, symmetrical Skill Strength score (0.0–10.0).

    Guarantees:
      - Returns exactly 0.0 when skill_labels is empty (no floor, no baseline)
      - Adding then removing the same skill always returns to the prior value
      - High-demand skills contribute significantly more than commodity skills
      - Evidence-backed skills score higher (SKILL_EVIDENCE_MULTIPLIER applied)
      - Same input always produces the same output

    Formula:
      raw_power = Σ market_value(skill_i) × evidence_multiplier(skill_i)
      score     = 10 × (1 − exp(−raw_power / SKILL_NORMALIZATION))

    The exponential curve means:
      0 skills         →  0.00
      1 Python (no ev) →  1.33
      5 high-value     →  5.28
      10 high-value    →  7.77
      10 + evidence    →  ~8.5
      15 high-value    →  8.95
    """
    if not skill_labels:
        return 0.0

    evidenced_lower = {s.lower().strip() for s in (evidenced_labels or set())}

    raw_power = 0.0
    for label in skill_labels:
        weight = get_skill_market_value(label)
        multiplier = (
            SKILL_EVIDENCE_MULTIPLIER
            if label.lower().strip() in evidenced_lower
            else 1.0
        )
        raw_power += weight * multiplier

    score = 10.0 * (1.0 - math.exp(-raw_power / SKILL_NORMALIZATION))
    return round(min(10.0, max(0.0, score)), 2)


# ============================================================
# DIMENSION DEFINITIONS
# ============================================================

DIMENSIONS: List[RubricDimension] = [
    RubricDimension(
        label="Documentation Strength",
        definition=(
            "The completeness and quality of the full set of uploaded "
            "materials — not just the CV, but certificates, references, "
            "transcripts, licenses, portfolios, and other supporting "
            "documents. More independent document types that corroborate "
            "the CV's claims produce a higher score."
        ),
        weight=1.0,
        anchors={
            "9-10": (
                "CV plus multiple strong supporting documents present "
                "(e.g. qualifications, references, certifications, "
                "portfolio). Documents corroborate each other from "
                "multiple independent angles."
            ),
            "7-8": (
                "CV plus one or two solid supporting documents "
                "(e.g. one certificate or one reference letter). "
                "Some claims are independently backed."
            ),
            "5-6": (
                "CV plus a single weak or partial supporting document "
                "(e.g. an unverifiable short-course certificate), or a "
                "CV with unusually thorough internal detail standing in "
                "for missing external verification."
            ),
            "3-4": (
                "CV only, reasonably detailed and structured — no "
                "supporting documentation of any kind. Claims are "
                "entirely self-reported with no independent verification."
            ),
            "0-2": (
                "Minimal or fragmentary documentation — a partial CV, "
                "unreadable scan, or documents too thin to assess."
            ),
        },
    ),
    RubricDimension(
        label="Experience Strength",
        definition=(
            "Years of experience, industry relevance, career "
            "progression, employment stability, measurable impact, "
            "and seniority. Roles described with quantified outcomes "
            "score higher than identical roles described only as "
            "duty lists."
        ),
        weight=1.3,
        anchors={
            "9-10": (
                "8+ years of verified, industry-relevant experience. "
                "Clear upward progression (documented promotions or "
                "expanding scope). Minimal unexplained gaps, low "
                "job-hopping. Multiple roles described with quantified "
                "outcomes (numbers, percentages, currency figures)."
            ),
            "7-8": (
                "5–8 years of relevant experience with at least some "
                "progression evidence (title change or scope increase). "
                "Generally stable employment. At least one role includes "
                "a measurable outcome, not only duties."
            ),
            "5-6": (
                "2–5 years of relevant experience, or longer experience "
                "only partially relevant to the target field. Flat roles "
                "with no title progression and no quantified outcomes."
            ),
            "3-4": (
                "Under 2 years of relevant experience, or experience "
                "mostly in an unrelated field, or significant unexplained "
                "employment gaps or frequent short stints."
            ),
            "0-2": (
                "No identifiable relevant work experience, or employment "
                "history too unclear to calculate duration."
            ),
        },
    ),
    RubricDimension(
        label="Qualification Strength",
        definition=(
            "Educational achievement and professional certifications, "
            "weighted by academic level, field relevance, institution "
            "credibility, and evidence of continued professional "
            "development. Unverified qualifications score lower than "
            "those backed by uploaded certificates or transcripts."
        ),
        weight=1.0,
        anchors={
            "9-10": (
                "Degree or diploma directly relevant to the target field, "
                "from an identifiable institution, PLUS relevant "
                "professional certifications, AND evidence of recent or "
                "continued professional development."
            ),
            "7-8": (
                "A relevant degree/diploma OR a strong combination of "
                "relevant industry certifications — but not both."
            ),
            "5-6": (
                "A general qualification (e.g. Grade 12 / matric) with "
                "no field-specific certification, or a relevant "
                "qualification with no verifiable institution or document."
            ),
            "3-4": (
                "Only minor or largely unrelated short courses/workshops "
                "with no formal qualification evidenced."
            ),
            "0-2": (
                "No qualification of any kind identifiable in the "
                "uploaded documents."
            ),
        },
    ),
    RubricDimension(
        label="Skill Strength",
        definition=(
            "Quality and market value of the candidate's skills, weighted "
            "by current labour-market demand. Computed deterministically "
            "in Python using SKILL_MARKET_VALUES — the AI score for this "
            "dimension is replaced by the Python calculation in pipeline.py. "
            "Score reflects the skill list at the time of display, so "
            "adding or removing a skill immediately changes this score."
        ),
        weight=1.0,
        anchors={
            "9-10": (
                "Multiple high-demand, industry-valued skills (AI/ML, "
                "Cloud, advanced programming, etc.) with several "
                "independently verified through certificates or portfolio."
            ),
            "7-8": (
                "A solid mix of industry-relevant, market-valued skills. "
                "At least some corroborated by experience or credentials."
            ),
            "5-6": (
                "A moderate number of relevant skills, mostly commodity "
                "level or soft skills. Limited high-demand technical depth."
            ),
            "3-4": (
                "Primarily generic or low-market-value skills (soft skills, "
                "basic office tools). Few skills of specific employer value."
            ),
            "0-2": (
                "Very few skills listed, or skills entirely at the lowest "
                "value tier (typing, filing, internet browsing)."
            ),
        },
    ),
    RubricDimension(
        label="Market Competitiveness",
        definition=(
            "How closely the applicant's overall profile matches what "
            "is typically expected for their stated or implied target "
            "field. Identifies concrete missing pieces rather than "
            "giving a vague impression."
        ),
        weight=1.0,
        anchors={
            "9-10": (
                "Profile matches or exceeds typical field expectations "
                "across experience, qualifications, and skills — no "
                "significant gap an interviewer would flag."
            ),
            "7-8": (
                "Profile is competitive with at most one identifiable "
                "gap against typical field expectations."
            ),
            "5-6": (
                "Profile is workable but has multiple identifiable gaps — "
                "would face real competition from better-documented "
                "candidates."
            ),
            "3-4": (
                "Profile falls clearly short of typical field expectations "
                "in several major areas (experience, qualifications, or "
                "required skills)."
            ),
            "0-2": (
                "Little to no alignment with the target field's typical "
                "requirements, or no target field identifiable."
            ),
        },
    ),
    RubricDimension(
        label="Evidence Credibility",
        definition=(
            "How much of the applicant's claims — across experience, "
            "qualifications, and skills — can be independently verified "
            "through uploaded documentation, as opposed to being "
            "self-reported in the CV alone. Contradictions between "
            "documents actively reduce this score."
        ),
        weight=1.2,
        anchors={
            "9-10": (
                "The large majority of significant claims are each backed "
                "by at least one independent supporting document or a "
                "specific, checkable, quantified detail. No contradictions "
                "detected between documents."
            ),
            "7-8": (
                "Most major claims have independent support; a minority "
                "rest on the CV's word alone. No significant contradictions."
            ),
            "5-6": (
                "Roughly half of major claims are independently supported; "
                "the rest are unverified self-reports. Minor inconsistencies "
                "may be present."
            ),
            "3-4": (
                "Only one or two claims have any independent support. "
                "Rest of the profile is entirely self-reported. Or: "
                "contradictions detected (e.g. dates don't add up, "
                "claimed experience exceeds what dates show)."
            ),
            "0-2": (
                "Nothing is independently verifiable — CV-only with zero "
                "supporting documentation. Or: significant contradictions "
                "detected across documents."
            ),
        },
    ),
    RubricDimension(
        label="ATS Compatibility",
        definition=(
            "Whether the CV's structure and formatting can be reliably "
            "parsed by automated applicant-tracking software — independent "
            "of content quality. A well-qualified candidate can score low "
            "here if their document is structured in a way ATS software "
            "cannot parse."
        ),
        weight=0.8,
        anchors={
            "9-10": (
                "Standard, conventionally-named sections in a predictable "
                "order. Consistent date formatting. No tables/columns/images "
                "carrying essential information. Role-relevant keywords in "
                "plain text."
            ),
            "7-8": (
                "Mostly standard structure with one minor parsing risk "
                "(one non-standard section header, or one inconsistent "
                "date format). Core sections still machine-readable."
            ),
            "5-6": (
                "Readable but with structural friction — skills or dates "
                "embedded inside paragraph text, inconsistent section "
                "naming, or moderate reliance on columns/tables."
            ),
            "3-4": (
                "Significant parsing risk — non-standard section names, "
                "heavy table/column layout, or key information embedded "
                "in a way that text extraction would scramble."
            ),
            "0-2": (
                "Essential content is unextractable as plain text — "
                "exists only as an image or scanned document with no "
                "OCR layer."
            ),
        },
    ),
    RubricDimension(
        label="Career Progression",
        definition=(
            "The trajectory of the applicant's career over time — "
            "promotion history, growth in responsibility, employment "
            "stability (tenure at each employer), and the overall arc "
            "from early roles to the most recent position. Influences "
            "the overall score; surfaces through insight cards."
        ),
        weight=0.9,
        anchors={
            "9-10": (
                "Clear upward trajectory: explicit promotions or expanding "
                "responsibility, consistent tenure of 2+ years at most "
                "employers, coherent arc from earlier to current roles."
            ),
            "7-8": (
                "Some evidence of progression — at least one promotion or "
                "scope increase. Generally stable (most roles 12+ months). "
                "Broadly consistent career direction."
            ),
            "5-6": (
                "Flat trajectory — similar level over extended period with "
                "no title change or scope growth. Mix of stable and "
                "short-tenure roles suggesting unclear direction."
            ),
            "3-4": (
                "Multiple short stints (under 12 months at several "
                "employers), unexplained gaps, or career direction appears "
                "to have reversed with no contextual explanation."
            ),
            "0-2": (
                "No discernible career progression — work history too "
                "sparse, dates too unclear, or too fragmented to assess."
            ),
        },
    ),
]


# ============================================================
# RUBRIC FUNCTIONS
# ============================================================

def build_rubric_prompt_block() -> str:
    lines = []
    for dim in DIMENSIONS:
        lines.append(f"### {dim.label}")
        lines.append(f"Definition: {dim.definition}")
        lines.append("Score anchors (use these as a literal lookup table):")
        for band in ["9-10", "7-8", "5-6", "3-4", "0-2"]:
            if band in dim.anchors:
                lines.append(f"  - {band}: {dim.anchors[band]}")
        lines.append("")
    return "\n".join(lines)


def weighted_overall(dimension_scores: Dict[str, float]) -> float:
    """
    Weighted overall score from dimension scores. Missing dimensions
    are treated as 0.0 (penalises incomplete model output).
    Returns a 0–10 float rounded to 2 decimal places.
    """
    total_weight = sum(d.weight for d in DIMENSIONS)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(
        dimension_scores.get(d.label, 0.0) * d.weight for d in DIMENSIONS
    )
    return round(weighted_sum / total_weight, 2)


# The dashboard only surfaces 5 of the 8 scored dimensions (see
# static/js/dashboard.js's METRICS array -- keep this list in sync with
# that one). Each one contributes up to 2 points there, so 5 full bars
# sum to exactly 10, deliberately different from weighted_overall()'s
# full 8-dimension score, which is used for score-history/roadmap
# tracking rather than what's actually shown on the gauge.
DASHBOARD_VISIBLE_LABELS = [
    "Documentation Strength",
    "Experience Strength",
    "Skill Strength",
    "Market Competitiveness",
    "ATS Compatibility",
]


def dashboard_visible_score(dimension_scores: Dict[str, float]) -> float:
    """
    Replicates dashboard.js's exact "visible total" calculation (5
    dimensions, each capped at 2 points), so anything quoting the
    dashboard's rating -- the AI chat, in particular -- reports the
    same number a user is actually looking at on screen, rather than
    weighted_overall()'s different, full 8-dimension score.
    """
    total = sum(
        (dimension_scores.get(label, 0.0) or 0.0) / 10.0 * 2.0
        for label in DASHBOARD_VISIBLE_LABELS
    )
    return round(total, 1)


def validate_scores(dimension_scores: Dict[str, float]) -> Tuple[bool, List[str]]:
    problems: List[str] = []
    expected_labels = {d.label for d in DIMENSIONS}
    received_labels = set(dimension_scores.keys())

    for label in sorted(expected_labels - received_labels):
        problems.append(f"Missing dimension: '{label}'")

    for label in sorted(received_labels - expected_labels):
        problems.append(f"Unrecognized dimension label: '{label}'")

    for label, score in dimension_scores.items():
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            problems.append(f"Non-numeric score for '{label}': {score!r}")
            continue
        if not (0.0 <= score_f <= 10.0):
            problems.append(f"Score out of range for '{label}': {score_f}")

    return (len(problems) == 0, problems)


def flag_achievement_gap(dimension_descriptions: Dict[str, str]) -> bool:
    description = dimension_descriptions.get("Experience Strength", "")
    if not description:
        return False
    has_digit = any(ch.isdigit() for ch in description)
    tokens = set(re.findall(r"[a-z]+", description.lower()))
    measurable_words = {"percent", "rating", "growth", "increase", "decrease", "reduced", "reduction"}
    has_word_marker = bool(tokens & measurable_words)
    symbol_markers = ("%", "$")
    has_symbol_marker = any(m in description for m in symbol_markers)
    return not (has_digit or has_word_marker or has_symbol_marker)


def count_quantified_achievements(raw_text: str) -> int:
    """
    Mechanical text scan for quantified achievements — a digit,
    percentage, or currency figure tied to a specific result.
    More reliable than trusting the model's own count.
    """
    currency_or_percent = re.compile(r"[R$€£]\s?\d|%|\bpercent\b", re.IGNORECASE)
    has_digit = re.compile(r"\d")
    results_word = re.compile(
        r"\b(increased?|decreased?|reduced?|grew|growth|saved|generated|"
        r"closed|exceeded|surpassed|achieved|delivered|managed|handled|"
        r"resolved|served|processed)\b",
        re.IGNORECASE,
    )
    fragments = re.split(r"[●\n]", raw_text)
    count = 0
    for frag in fragments:
        frag = frag.strip()
        if not frag:
            continue
        if currency_or_percent.search(frag):
            count += 1
        elif has_digit.search(frag) and results_word.search(frag):
            count += 1
    return count


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DATE_RANGE_RE = re.compile(
    r"(?P<start_month>[a-zA-Z]{3,9}\.?)?\s*(?P<start_year>(?:19|20)\d{2})\s*"
    r"(?:-|–|—|to)\s*"
    r"(?P<end_month>[a-zA-Z]{3,9}\.?)?\s*(?P<end_year>(?:19|20)\d{2}|present|current)",
    re.IGNORECASE,
)


def estimate_unexplained_gap_months(raw_text: str) -> int:
    """
    Mechanically scans for employment-style date ranges (e.g. "March
    2023 - Present", "June 2020 - January 2022") and sums the months of
    genuine gap between consecutive periods. This exists for the same
    reason count_quantified_achievements() does: date arithmetic across
    a whole document is exactly the kind of thing an LLM gets wrong
    silently (misses a gap, or invents one), so it's computed here and
    handed to the model as a verified fact rather than trusted to its
    own counting.

    Overlapping or back-to-back date ranges are merged into single
    blocks first, so two roles held at once (or a role ending the same
    month the next begins) never register as a gap. Gaps of a single
    month are treated as normal rounding noise, not a real gap.

    This is a best-effort text scan, not a guarantee — non-employment
    date ranges (e.g. a degree's start/end years) could still be picked
    up. Bare year-only ranges ("2015-2018", no month on either side) are
    deliberately excluded rather than guessed at: they're both too
    imprecise for month-level gap math and are overwhelmingly education
    entries rather than employment ones — counting a normal
    graduation-to-first-job transition as an "unexplained gap" would be
    a real error, not a subtle one, so it's safer to skip a range
    entirely than to default a missing month to January and let that
    default manufacture a gap that was never really there.
    """
    from datetime import date

    today = date.today()
    periods = []
    for m in _DATE_RANGE_RE.finditer(raw_text or ""):
        try:
            end_raw = m.group("end_year").lower()
            is_open_ended = end_raw in ("present", "current")
            has_start_month = bool(m.group("start_month"))
            has_end_month = bool(m.group("end_month")) or is_open_ended
            if not has_start_month and not has_end_month:
                continue  # bare year-only range — too imprecise, usually education

            start_month = _MONTH_NAMES.get((m.group("start_month") or "").lower().strip("."), 1)
            start_year = int(m.group("start_year"))
            if is_open_ended:
                end_year, end_month = today.year, today.month
            else:
                end_month = _MONTH_NAMES.get((m.group("end_month") or "").lower().strip("."), 1)
                end_year = int(end_raw)
            start_idx = start_year * 12 + start_month
            end_idx = end_year * 12 + end_month
            if 0 <= end_idx - start_idx <= 600:  # sanity bound: 0-50 years
                periods.append((start_idx, end_idx))
        except (ValueError, TypeError):
            continue

    if len(periods) < 2:
        return 0

    periods.sort()
    merged = [periods[0]]
    for start, end in periods[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    total_gap = 0
    for i in range(1, len(merged)):
        gap = merged[i][0] - merged[i - 1][1] - 1
        if gap > 1:  # ignore 1-month edge rounding between adjacent roles
            total_gap += gap
    return total_gap


def floor_documentation_strength(
    score: float,
    supporting_doc_counts: Dict[str, int],
    raw_text: str,
    min_words_for_complete_cv: int = 150,
) -> float:
    """
    Floor Documentation Strength at 3.0 for a complete CV-only submission.
    A CV-only submission should never fall into the 0-2 'fragmentary'
    band unless the CV itself is genuinely thin or unreadable.
    """
    if score >= 3.0:
        return score
    counts = supporting_doc_counts or {}
    has_any_supporting_doc = any(int(v or 0) > 0 for v in counts.values())
    if has_any_supporting_doc:
        return score
    word_count = len((raw_text or "").split())
    if word_count < min_words_for_complete_cv:
        return score
    return 3.0


def mechanical_ats_check(extracted_text: str, skill_labels: List[str] = None) -> dict:
    """
    Parses extracted plain text to compute an objective ATS Compatibility score.
    Returns {"score": float (0-10), "findings": list[str]}.
    More reliable than asking the AI to guess whether its own input was parseable.
    """
    text = extracted_text or ""
    lower = text.lower()
    findings = []
    score = 4.5  # baseline: assume CV-only, partially structured

    # 1. Standard section headers
    standard_headers = {
        "experience": ["work experience", "employment history", "professional experience", "career history", "experience"],
        "education": ["education", "qualifications", "academic background", "academic qualifications"],
        "skills": ["skills", "competencies", "technical skills", "core competencies", "areas of expertise"],
        "contact": ["contact", "personal details", "personal information", "profile summary", "professional summary", "summary", "objective"],
    }
    found_categories = set()
    for category, keywords in standard_headers.items():
        if any(kw in lower for kw in keywords):
            found_categories.add(category)

    if len(found_categories) >= 4:
        score += 2.0
        findings.append(f"All key sections detected ({', '.join(sorted(found_categories))})")
    elif len(found_categories) == 3:
        score += 1.0
        missing = [c for c in standard_headers if c not in found_categories]
        findings.append(f"Most sections found; missing or non-standard: {', '.join(missing)}")
    elif len(found_categories) == 2:
        missing = [c for c in standard_headers if c not in found_categories]
        findings.append(f"Only {len(found_categories)} standard sections detected — missing: {', '.join(missing)}")
    else:
        score -= 1.5
        findings.append("Fewer than 2 standard section headers found — ATS may not correctly categorise your content")

    # 2. Date patterns (employment timeline readability)
    date_patterns = [
        r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b',
        r'\b\d{4}\s*[-–—]\s*(?:present|current|\d{4})\b',
        r'\b\d{1,2}[/\-]\d{4}\b',
    ]
    date_matches = sum(1 for p in date_patterns if re.search(p, lower))
    if date_matches >= 2:
        score += 1.5
        findings.append("Clear, consistent date formatting found — employment timeline is ATS-readable")
    elif date_matches == 1:
        score += 0.5
        findings.append("Some date formatting found — ensure all roles have start and end dates in a standard format")
    else:
        score -= 1.0
        findings.append("No standard date patterns detected — employment history timeline may be unreadable by ATS")

    # 3. Keyword density — skills appearing in body text (not just skills section)
    if skill_labels:
        skills_in_body = [s for s in skill_labels if s.lower() in lower]
        ratio = len(skills_in_body) / len(skill_labels)
        if ratio >= 0.6:
            score += 1.0
            if len(skills_in_body) == len(skill_labels):
                findings.append(f"All {len(skills_in_body)} of your listed skills appear as keywords in the document body")
            else:
                findings.append(f"{len(skills_in_body)} of your {len(skill_labels)} listed skills appear as keywords in the document body")
        elif ratio >= 0.3:
            score += 0.5
            findings.append(f"Only {len(skills_in_body)} of your {len(skill_labels)} listed skills appear in body text — more keyword integration would improve ATS ranking")
        else:
            findings.append(f"Very few listed skills appear in document body text (just {len(skills_in_body)} of {len(skill_labels)}) — skills may only appear in a separate skills section, which some ATS systems weight lower")

    # 4. Content volume check
    word_count = len(text.split())
    if word_count < 80:
        score -= 2.0
        findings.append(f"Very little text extracted ({word_count} words) — document may be image-only or unreadable as plain text")
    elif word_count < 200:
        score -= 0.5
        findings.append(f"Document is relatively short ({word_count} words) — ATS may flag thin content")
    else:
        score += 0.5
        findings.append(f"Sufficient text content extracted ({word_count} words)")

    score = round(min(10.0, max(0.0, score)), 1)
    return {"score": score, "findings": findings}


def label_for_score(overall: float) -> str:
    if overall >= 7.5:
        return "Highly Employable"
    if overall >= 6.5:
        return "Job Ready"
    if overall >= 5.0:
        return "Competitive"
    if overall >= 3.5:
        return "Needs Work"
    if overall >= 2.0:
        return "Highly Hindered"
    return "Critical Gaps"


def stars_for_score(overall: float) -> int:
    if overall >= 7.5:
        return 5
    if overall >= 6.5:
        return 4
    if overall >= 5.0:
        return 3
    if overall >= 3.5:
        return 2
    return 1


def confidence_band(confidence_pct: float) -> str:
    if confidence_pct >= 85:
        return "High Confidence"
    if confidence_pct >= 60:
        return "Moderate Confidence"
    if confidence_pct >= 40:
        return "Low Confidence"
    return "Very Low Confidence"

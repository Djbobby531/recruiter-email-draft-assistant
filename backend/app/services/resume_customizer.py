"""
Deterministic resume match scoring + truthful, format-preserving customization.

By explicit configuration choice, this is NEVER LLM-assisted - an earlier
version of this module optionally sent the resume/JD to an AI provider to
refine the score and phrase additive points more naturally, but that path has
been removed: `evaluate_and_customize` is purely deterministic now, and
always was for the score/points that matter. It runs AFTER the deterministic
resume_matcher has already picked the best resume - this module never changes
WHICH resume is selected, only (a) carries over that resume's match
score/explanation, and (b) optionally adds a short "Additional Relevant
Skills" section to a COPY of the resume (never the original file) listing
skills the candidate genuinely already has - never anything invented.

Truthfulness guardrail: the only skills ever eligible to be added are ones
that already appear in the resume's own `extracted_metadata.skills` AND are
also asked for by the JD - i.e. things the candidate has already told us
about themselves, just not necessarily worded prominently in their resume's
prose.

Format preservation: only `.docx` resumes are customized, and always starting
fresh from the ORIGINAL library file (resume_matcher always selects from the
Resume table, never a previously-customized copy - those live only as a path
on the Application row, never as a Resume row of their own) - a COPY is
written to disk, the original is never touched. New points are inserted
directly INTO the Experience section (preferred) or Summary section as new
paragraphs cloning the exact style/formatting (including bullet/numbering) of
an existing paragraph in that section, so they read as native resume content
rather than a bolted-on appendix. If neither section can be confidently
located, this falls back to appending a clearly-labeled "Additional Relevant
Skills" section at the very end instead - existing paragraphs are NEVER
modified either way. PDF and legacy `.doc` resumes are never rewritten
(there's no safe way to insert text into either without risking corrupting
the layout), so the original file is attached unchanged for those.
"""
from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import docx
from docx.document import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from app.ai.base import AIProvider
from app.config import Settings
from app.models import Resume

# Truthfulness/quality guards (hedge language, unapproved-technology
# detection, canonical skill aliasing) are shared with draft_service.py's
# email skills-pitch validation - see app/services/ai_text_guardrails.py.
# Re-imported under these names so the rest of this module (and its tests)
# are unaffected by the move.
from app.services.ai_text_guardrails import canonical_skill as _canonical_skill
from app.services.ai_text_guardrails import validate_generated_statement as _validate_generated_statement
from app.services.resume_matcher import ResumeScore
from app.utils.text_cleaning import clean_role_text

logger = logging.getLogger("app.resume_customizer")

# The attachment filename shown to every recruiter, for every customized
# resume, regardless of role or which customization path produced it (by
# explicit configuration choice - previously this varied per JD role/
# original filename, which the candidate found needlessly inconsistent).
# The FILE ITSELF on disk is always named exactly this too - no prefix or
# suffix - uniqueness across applications instead comes from each one living
# in its own randomly-named subdirectory (see the two call sites below),
# which is never visible to anyone - only the attachment's filename is.
CUSTOMIZED_RESUME_DISPLAY_FILENAME = "Diwakar_Resume.docx"


def _discard_customization_output(new_path: Path) -> None:
    """Deletes a rejected/invalid customization's output file AND its
    now-empty per-customization subdirectory (see
    CUSTOMIZED_RESUME_DISPLAY_FILENAME above) - otherwise every rejected
    attempt would leave an orphaned empty directory behind forever."""
    try:
        new_path.unlink(missing_ok=True)
        new_path.parent.rmdir()
    except OSError:
        pass  # directory not empty (unexpected) or already gone - harmless either way


@dataclass
class CustomizationResult:
    match_score: float
    match_explanation: str
    additional_points: list[str] = field(default_factory=list)
    source: str = "deterministic"  # or "ai"


def _candidate_metadata_skills(resume: Resume) -> set[str]:
    metadata = resume.extracted_metadata or {}
    skills: set[str] = set()
    for terms in metadata.get("skills", {}).values():
        skills.update(t.lower() for t in terms)
    return skills


# Line-oriented mirrors of the heading patterns used for DOCX section
# detection below - applied against resume.extracted_text (plain text, one
# resume paragraph per line, see utils/text_extraction.py) so "prominence"
# can be judged the same way for both plain-text analysis and DOCX editing.
_SUMMARY_HEADING_LINE_RE = re.compile(
    r"^(professional\s+|career\s+|executive\s+)?(summary|profile)\s*:?$|^objective\s*:?$", re.IGNORECASE,
)
_ANY_HEADING_LINE_RE = re.compile(
    r"^(summary|professional summary|career summary|executive summary|profile|objective|"
    r"experience|professional experience|work experience|relevant experience|employment history|"
    r"education|skills|technical skills|core competencies|certifications?|projects?|"
    r"achievements?|awards?|publications?)\s*:?$",
    re.IGNORECASE,
)


def _extract_summary_section_text(resume_text: str) -> str:
    """Best-effort isolation of just the Summary/Profile section's text (the
    part of a resume a recruiter actually reads first), stopping at the next
    recognized section heading. Empty string if no such heading is found."""
    lines = resume_text.splitlines()
    for i, line in enumerate(lines):
        if not _SUMMARY_HEADING_LINE_RE.match(line.strip()):
            continue
        section_lines = []
        for later_line in lines[i + 1:]:
            if later_line.strip() and _ANY_HEADING_LINE_RE.match(later_line.strip()):
                break
            section_lines.append(later_line)
        return "\n".join(section_lines)
    return ""


def find_underemphasized_overlap_skills(resume: Resume, jd_requirements: list[str]) -> list[str]:
    """
    Skills that are BOTH (a) genuinely already in the candidate's own resume
    metadata and (b) asked for by the JD, but that aren't prominently called
    out where a recruiter actually looks first - safe, truthful, additive
    keywords. Never includes anything the candidate hasn't already claimed.

    "Prominently" is judged against the resume's Summary/Profile section
    specifically (not the whole document) whenever one can be identified:
    metadata.skills is itself extracted by scanning the FULL resume text for
    known terms, so a skill already in metadata is - by construction - always
    present *somewhere* in the full text (e.g. buried in a bullet point from
    a job years ago); checking the whole text again would never find
    anything "missing" and this feature would silently never fire. Checking
    just the Summary instead surfaces genuinely useful, still 100% truthful
    reinforcement: a real skill the candidate has, relevant to this JD, that
    isn't yet called out where it would actually get noticed. Falls back to
    the whole-text check only when no Summary section can be located at all.
    """
    candidate_skills = _candidate_metadata_skills(resume)
    jd_terms = {r.lower() for r in jd_requirements}
    overlap = candidate_skills & jd_terms
    if not overlap:
        return []

    summary_text = _extract_summary_section_text(resume.extracted_text or "").lower()
    if summary_text.strip():
        return sorted(term for term in overlap if term not in summary_text)

    resume_text_lower = (resume.extracted_text or "").lower()
    return sorted(term for term in overlap if term not in resume_text_lower)


def _format_deterministic_points(missing_terms: list[str]) -> list[str]:
    if not missing_terms:
        return []
    formatted = ", ".join(t.upper() if len(t) <= 3 else t.title() for t in missing_terms[:6])
    return [f"Additional Relevant Skills: {formatted}"]


def evaluate_and_customize(
    resume: Resume,
    jd_title: str | None,
    jd_text: str,
    jd_requirements: list[str],
    base_score: ResumeScore,
    ai_provider: AIProvider | None = None,
) -> CustomizationResult:
    """Purely deterministic, by explicit configuration choice - `ai_provider`
    is accepted only for call-site compatibility and is never consulted. The
    match score/explanation are carried over unchanged from `base_score`, and
    the only additive points ever produced are the pre-approved
    "Additional Relevant Skills" line built from skills the candidate's own
    resume metadata already claims."""
    del ai_provider  # never used - see module docstring
    missing_terms = find_underemphasized_overlap_skills(resume, jd_requirements)
    return CustomizationResult(
        match_score=base_score.score,
        match_explanation=base_score.explanation,
        additional_points=_format_deterministic_points(missing_terms),
        source="deterministic",
    )


SUMMARY_HEADING_RE = re.compile(
    r"^(professional\s+|career\s+|executive\s+)?(summary|profile)\s*:?$|^objective\s*:?$", re.IGNORECASE,
)
EXPERIENCE_HEADING_RE = re.compile(
    r"^(professional\s+|work\s+|relevant\s+)?experience\s*:?$|^employment\s+history\s*:?$", re.IGNORECASE,
)
# Used only to find where a section ENDS (the next recognized heading) -
# deliberately broader than the two above.
ANY_SECTION_HEADING_RE = re.compile(
    r"^(summary|professional summary|career summary|executive summary|profile|objective|"
    r"experience|professional experience|work experience|relevant experience|employment history|"
    r"education|skills|technical skills|core competencies|certifications?|projects?|"
    r"achievements?|awards?|publications?)\s*:?$",
    re.IGNORECASE,
)
_BULLET_PREFIX_RE = re.compile(r"^[•\-•▪\*]\s*")


def _looks_like_heading(paragraph: Paragraph) -> bool:
    text = paragraph.text.strip()
    return bool(text) and len(text) <= 60 and bool(ANY_SECTION_HEADING_RE.match(text))


def _find_section_content_bounds(paragraphs: list[Paragraph], heading_re: re.Pattern) -> tuple[int, int] | None:
    """Returns (first_content_index, end_index) for the first section whose
    heading matches `heading_re` - end_index is where the NEXT recognized
    heading starts (or len(paragraphs) if this is the last section). None if
    no matching heading is found at all."""
    for i, paragraph in enumerate(paragraphs):
        text = paragraph.text.strip()
        if not text or not heading_re.match(text):
            continue
        end = len(paragraphs)
        for j in range(i + 1, len(paragraphs)):
            if _looks_like_heading(paragraphs[j]):
                end = j
                break
        return i + 1, end
    return None


def _reference_paragraph_for_section(paragraphs: list[Paragraph], start: int, end: int) -> Paragraph | None:
    """Prefers the LAST paragraph in the section that's bullet/list-styled
    (matching the section's dominant formatting even when its very last
    line is an outlier, e.g. a trailing "TECH STACK: ..." summary line with
    no explicit style) - falls back to the last non-blank paragraph of any
    kind for a prose-only section like Summary."""
    last_any: Paragraph | None = None
    for idx in range(end - 1, start - 1, -1):
        paragraph = paragraphs[idx]
        if not paragraph.text.strip():
            continue
        if last_any is None:
            last_any = paragraph
        style_name = (paragraph.style.name if paragraph.style is not None else "") or ""
        if "list" in style_name.lower() or _BULLET_PREFIX_RE.match(paragraph.text.strip()):
            return paragraph
    return last_any


def _clone_paragraph_formatting(source: Paragraph, target: Paragraph) -> None:
    """Copies style + numbering (bullet/numbered list) + basic run-level
    formatting from `source` onto `target`, so the new paragraph is visually
    indistinguishable from the resume's existing content."""
    target.style = source.style

    source_pPr = source._p.find(qn("w:pPr"))
    if source_pPr is not None:
        source_numPr = source_pPr.find(qn("w:numPr"))
        if source_numPr is not None:
            target_pPr = target._p.get_or_add_pPr()
            existing_numPr = target_pPr.find(qn("w:numPr"))
            if existing_numPr is not None:
                target_pPr.remove(existing_numPr)
            target_pPr.append(copy.deepcopy(source_numPr))

    if source.runs and target.runs:
        src_font = source.runs[0].font
        dst_font = target.runs[0].font
        dst_font.name = src_font.name
        dst_font.size = src_font.size
        dst_font.bold = src_font.bold
        dst_font.italic = src_font.italic
        try:
            if src_font.color is not None and src_font.color.type is not None:
                dst_font.color.rgb = src_font.color.rgb
        except AttributeError:
            pass  # theme-color or other non-RGB color - leave target's default


def _insert_paragraphs_before(
    document: Document, anchor: Paragraph | None, texts: list[str], reference: Paragraph,
) -> None:
    """Adds one new paragraph per text (cloning `reference`'s formatting),
    positioned immediately before `anchor` - or at the very end of the
    document if `anchor` is None (the matched section was the last one)."""
    uses_bullet_char = bool(_BULLET_PREFIX_RE.match(reference.text.strip()))
    for text in texts:
        prefixed = f"• {text}" if uses_bullet_char and not _BULLET_PREFIX_RE.match(text) else text
        new_paragraph = document.add_paragraph(prefixed)
        _clone_paragraph_formatting(reference, new_paragraph)
        if anchor is not None:
            anchor._p.addprevious(new_paragraph._p)


def _is_subsequence_of_texts(original_texts: list[str], new_texts: list[str]) -> bool:
    """True iff every string in `original_texts` appears in `new_texts`, in
    the same relative order and byte-for-byte unchanged - i.e. `new_texts`
    is exactly `original_texts` plus some additional entries inserted here
    and there, never a modification, reordering, or loss of an existing
    one."""
    it = iter(new_texts)
    for original_text in original_texts:
        for candidate in it:
            if candidate == original_text:
                break
        else:
            return False
    return True


def _customization_is_format_safe(original_document: Document, new_path: str) -> bool:
    """Re-opens the just-saved file as an independent, real integrity check
    (not just trusting that the in-memory edit "should" have worked) and
    confirms: (a) the file still opens cleanly as a valid docx, (b) every
    original paragraph survived, unmodified and in order, and (c) something
    was actually added. Anything else - a corrupted save, lost/reordered
    content, an exception anywhere in the edit - is treated as a formatting
    failure."""
    try:
        original_texts = [p.text for p in original_document.paragraphs]
        reopened = docx.Document(new_path)
        new_texts = [p.text for p in reopened.paragraphs]
    except Exception:
        logger.warning("customized resume failed to re-open cleanly; treating as a formatting failure", exc_info=True)
        return False

    if len(new_texts) <= len(original_texts):
        logger.warning("customized resume has no more content than the original; treating as a formatting failure")
        return False

    if not _is_subsequence_of_texts(original_texts, new_texts):
        logger.warning(
            "customized resume lost or reordered original content; treating as a formatting failure"
        )
        return False

    return True


def customize_resume_file(resume: Resume, additional_points: list[str], dest_dir: str) -> tuple[str, str] | None:
    """
    Returns (new_file_path, display_filename), or None if there's nothing to
    add, the format can't be safely customized in place, or the edit fails a
    post-save formatting-integrity check - in every one of those cases the
    caller falls back to attaching the original resume unchanged (RULE: for
    every single email, a formatting problem always reverts to the plain,
    unmodified resume - a customization is only ever used once verified safe).
    Only ever writes a NEW file - the original resume in the library is never
    touched.
    """
    if not additional_points:
        return None

    suffix = Path(resume.filename).suffix.lower()
    if suffix != ".docx":
        return None  # PDF/DOC: never rewritten, to avoid corrupting the layout

    try:
        document = docx.Document(resume.file_path)
        paragraphs = document.paragraphs

        # Prefer inserting into Experience (these are resume-bullet-style
        # points, they read most naturally there), then Summary, so they
        # blend in as native content rather than a bolted-on appendix.
        target_bounds = (
            _find_section_content_bounds(paragraphs, EXPERIENCE_HEADING_RE)
            or _find_section_content_bounds(paragraphs, SUMMARY_HEADING_RE)
        )

        if target_bounds is not None:
            start, end = target_bounds
            reference = _reference_paragraph_for_section(paragraphs, start, end)
            if reference is not None:
                anchor = paragraphs[end] if end < len(paragraphs) else None
                _insert_paragraphs_before(document, anchor, additional_points, reference)
            else:
                target_bounds = None  # empty section (heading with no content) - fall through

        if target_bounds is None:
            # Safety net: neither section could be confidently located -
            # append a clearly-labeled section at the very end instead,
            # exactly as before, rather than guessing where to insert.
            reference_style = None
            for paragraph in reversed(paragraphs):
                if paragraph.text.strip():
                    reference_style = paragraph.style
                    break

            heading = document.add_paragraph("Additional Relevant Skills")
            if reference_style is not None:
                heading.style = reference_style
            if heading.runs:
                heading.runs[0].bold = True

            for point in additional_points:
                bullet = document.add_paragraph(f"• {point}")
                if reference_style is not None:
                    bullet.style = reference_style

        display_filename = CUSTOMIZED_RESUME_DISPLAY_FILENAME
        # The FILE ITSELF is always named exactly "Diwakar_Resume.docx" - no
        # prefix/suffix - so uniqueness (every past application's dashboard
        # "view customized resume" must keep showing ITS OWN content, not
        # get silently overwritten by a later application's file of the same
        # name) comes from a per-customization subdirectory instead, which
        # is never visible to anyone - only the attachment's filename is.
        customization_dir = Path(dest_dir) / uuid.uuid4().hex[:8]
        customization_dir.mkdir(parents=True, exist_ok=True)
        new_path = customization_dir / display_filename
        document.save(str(new_path))
    except Exception:
        logger.warning("resume customization failed while editing the document; keeping the original resume", exc_info=True)
        return None

    # Independent post-save check, every time, for every email - a
    # formatting problem always reverts to attaching the plain resume.
    original_document = docx.Document(resume.file_path)
    if not _customization_is_format_safe(original_document, str(new_path)):
        _discard_customization_output(new_path)
        return None

    return str(new_path), display_filename


# =============================================================================
# LLM-assisted structured customization: header role rewrite, 1-2 summary
# lines, verified skills placed into their correct EXISTING category, and 1-2
# experience bullets per relevant existing job - opt-in
# (RESUME_LLM_CUSTOMIZATION_ENABLED), only ever engaged for .docx resumes with
# a configured ai_provider. The LLM produces a structured JSON PLAN ONLY -
# never the document itself; python-docx applies every edit, and every field
# of the plan is independently re-validated against the candidate's own
# verified resume metadata before anything is trusted. On ANY doubt at any
# stage this falls back to None, and the caller (pipeline.py) falls back
# further to the plain deterministic customize_resume_file() above.
# =============================================================================

# Buckets used only to pick which EXISTING resume category a verified skill
# belongs in when the AI's own `category` guess doesn't match a real section
# in this specific resume - never used to invent a new heading.
_SKILL_CATEGORY_BUCKETS: dict[str, list[str]] = {
    "cloud": [
        "aws", "azure", "gcp", "glue", "emr", "lambda", "redshift", "s3", "ec2",
        "data factory", "dataflow", "synapse", "bigquery",
    ],
    "programming_languages": ["python", "java", "scala", "sql", "javascript", "typescript", "go", "c#", "c++", "r"],
    "big_data": ["spark", "kafka", "hadoop", "databricks", "flink", "hive", "pyspark"],
    "databases": [
        "postgres", "postgresql", "mysql", "mongodb", "dynamodb", "cassandra", "oracle",
        "sql server", "snowflake", "cosmos db", "elasticsearch", "redis", "mariadb",
    ],
    "orchestration": ["airflow", "control-m", "cloud composer", "step functions"],
    "devops": [
        "terraform", "kubernetes", "docker", "cloudformation", "ansible", "jenkins",
        "github actions", "gitlab ci", "helm", "git", "github", "gitlab", "openshift",
    ],
}
_CATEGORY_LABEL_HINTS: dict[str, list[str]] = {
    "cloud": ["cloud"],
    "programming_languages": ["language", "programming"],
    "big_data": ["big data", "data engineering"],
    "databases": ["database"],
    "orchestration": ["orchestrat", "scheduling"],
    "devops": ["devops", "infrastructure", "version control", "ci/cd", "tools", "container"],
}


def _skill_bucket(skill: str) -> str | None:
    canonical = _canonical_skill(skill)
    for bucket, keywords in _SKILL_CATEGORY_BUCKETS.items():
        if canonical in keywords:
            return bucket
    return None


SKILLS_HEADING_RE = re.compile(
    r"^(technical\s+)?skills\s*:?$|^technical\s+expertise\s*:?$|^core\s+competencies\s*:?$|"
    r"^technology\s+stack\s*:?$|^skills\s*(&|and)\s*technologies\s*:?$",
    re.IGNORECASE,
)
_SKILL_LABEL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 /&\-]{1,40}):\s*(.+)$")


def _split_skill_items(items_text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,]", items_text) if p.strip()]


@dataclass
class SkillCategoryLocation:
    label: str
    existing_items: list[str]
    paragraph: Paragraph | None = None       # "Label: a, b, c" style
    cell_paragraph: Paragraph | None = None  # table "Label | a, b, c" style

    @property
    def existing_canonical(self) -> set[str]:
        return {_canonical_skill(i) for i in self.existing_items}


@dataclass
class ExperienceBlockLocation:
    identifier: str
    bullet_reference: Paragraph | None
    insert_before: Paragraph | None


@dataclass
class ValidatedSkillAddition:
    skill: str
    location: SkillCategoryLocation


@dataclass
class ValidatedExperienceUpdate:
    block: ExperienceBlockLocation
    points: list[str]


@dataclass
class ValidatedCustomizationPlan:
    header_role: str | None = None
    summary_points: list[str] = field(default_factory=list)
    skills: list[ValidatedSkillAddition] = field(default_factory=list)
    experience_updates: list[ValidatedExperienceUpdate] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.header_role or self.summary_points or self.skills or self.experience_updates)


def _primary_header_candidate_paragraphs(document: Document) -> list[Paragraph]:
    """The single most reliable, least ambiguous header container: a small
    header table's FIRST cell (Name / Role tagline / Contact stacked as
    separate paragraphs), or - if there's no such table - the very top of
    the body. Deliberately narrow: a job title that also happens to appear
    later, e.g. as an Experience entry's own title line, must never be
    confused with the actual header."""
    if document.tables:
        first_row = document.tables[0].rows[0] if document.tables[0].rows else None
        if first_row is not None and first_row.cells:
            return list(first_row.cells[0].paragraphs)
    return list(document.paragraphs)[:6]


def _fallback_header_candidate_paragraphs(document: Document):
    """A broader last-resort scan (other early tables/cells, section
    headers) for the rarer layouts the primary/narrow scan above doesn't
    cover - only ever used for an EXACT title match, never the looser
    role-line heuristic, since it's not scoped tightly enough to trust a
    heuristic guess."""
    for table in list(document.tables)[:2]:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for section in document.sections:
        yield from section.header.paragraphs
        for table in section.header.tables:
            for row in table.rows:
                for cell in row.cells:
                    yield from cell.paragraphs


_ROLE_LINE_TITLE_KEYWORDS = (
    "engineer", "developer", "architect", "analyst", "scientist", "manager",
    "consultant", "administrator", "lead", "specialist", "director", "designer",
)
# A role/tagline line is never contact info, never a full sentence (title
# lines are short noun phrases), and is short enough to plausibly be one.
_CONTACT_INFO_LINE_RE = re.compile(r"@|https?://|linkedin\.com|\d{3}[-.\s)]\d{3}")


def _looks_like_role_line(text: str) -> bool:
    if not text or len(text) > 80 or len(text.split()) > 12:
        return False
    if _CONTACT_INFO_LINE_RE.search(text):
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in _ROLE_LINE_TITLE_KEYWORDS)


def _find_header_role_paragraph(document: Document, known_titles: list[str]) -> Paragraph | None:
    """Finds the header's "role" paragraph in order of precision:
    (1) an EXACT match against one of this resume's own already-detected job
    titles, but ONLY within the narrow, unambiguous primary header container
    (see _primary_header_candidate_paragraphs) - the most precise signal
    when it happens to line up there;
    (2) a heuristic fallback within that SAME narrow container, for header
    taglines that don't exactly match any auto-detected title (e.g. "Azure
    Data Engineer - Databricks, ADF & Data Security" vs. a plainer "Senior
    Azure Data Engineer" picked up from an Experience entry elsewhere) - a
    short, non-contact-info line containing a job-title keyword;
    (3) an EXACT match against the broader fallback scan, for the rarer
    layout where the header genuinely lives somewhere else.
    Deliberately never runs the heuristic against the broad fallback scan -
    that's wide enough that a heuristic guess there risks landing on an
    unrelated sentence."""
    candidates = {t.strip().lower() for t in known_titles if isinstance(t, str) and t.strip()}
    primary = _primary_header_candidate_paragraphs(document)

    if candidates:
        for paragraph in primary:
            text = paragraph.text.strip()
            if text and text.lower() in candidates:
                return paragraph

    for paragraph in primary:
        if _looks_like_role_line(paragraph.text.strip()):
            return paragraph

    if candidates:
        for paragraph in _fallback_header_candidate_paragraphs(document):
            text = paragraph.text.strip()
            if text and text.lower() in candidates:
                return paragraph
    return None


def _replace_role_text_preserving_format(paragraph: Paragraph, new_text: str) -> None:
    """Only the RUN TEXT changes - every run's font/size/bold/italic/color
    and the paragraph's own style/alignment/spacing are left completely
    untouched. A multi-run role (e.g. "Senior" bold + "Data Engineer" not)
    collapses onto the first run, which keeps its original formatting; the
    other runs are emptied rather than deleted, so nothing else in the
    paragraph shifts."""
    if not paragraph.runs:
        paragraph.add_run(new_text)
        return
    paragraph.runs[0].text = new_text
    for run in paragraph.runs[1:]:
        run.text = ""


def _find_skill_category_locations(document: Document) -> list[SkillCategoryLocation]:
    locations: list[SkillCategoryLocation] = []

    bounds = _find_section_content_bounds(document.paragraphs, SKILLS_HEADING_RE)
    if bounds is not None:
        start, end = bounds
        for paragraph in document.paragraphs[start:end]:
            text = paragraph.text.strip()
            if not text:
                continue
            m = _SKILL_LABEL_LINE_RE.match(text)
            if m:
                locations.append(SkillCategoryLocation(
                    label=m.group(1).strip(), existing_items=_split_skill_items(m.group(2)), paragraph=paragraph,
                ))

    # Table-based skills: a row whose first cell looks like a short category
    # label and whose second cell holds a comma/semicolon-separated list.
    for table in document.tables:
        for row in table.rows:
            cells = row.cells
            if len(cells) < 2:
                continue
            label_text = cells[0].text.strip()
            items_text = cells[1].text.strip()
            if not label_text or not items_text or len(label_text) > 40:
                continue
            if "," not in items_text and ";" not in items_text:
                continue
            if not cells[1].paragraphs:
                continue
            locations.append(SkillCategoryLocation(
                label=label_text.rstrip(":"), existing_items=_split_skill_items(items_text),
                cell_paragraph=cells[1].paragraphs[0],
            ))
    return locations


def _select_target_category(
    skill: str, ai_named_category: str, locations: list[SkillCategoryLocation]
) -> SkillCategoryLocation | None:
    if not locations:
        return None
    if ai_named_category:
        named_lower = ai_named_category.strip().lower()
        if named_lower:
            for loc in locations:
                label_lower = loc.label.lower()
                if named_lower in label_lower or label_lower in named_lower:
                    return loc
    bucket = _skill_bucket(skill)
    if bucket:
        for hint in _CATEGORY_LABEL_HINTS.get(bucket, []):
            for loc in locations:
                if hint in loc.label.lower():
                    return loc
    return None


def _format_skill_display(skill: str) -> str:
    """Same display convention as the deterministic "Additional Relevant
    Skills" fallback: short acronyms (AWS, GCP) get upper-cased, everything
    else gets title-cased - so a lowercase "airflow" from the model reads as
    "Airflow", matching the capitalization style of its resume neighbors."""
    return skill.upper() if len(skill) <= 3 else skill.title()


def _append_skill_to_paragraph(paragraph: Paragraph, skill: str) -> None:
    """Appends ", {skill}" onto the paragraph's LAST run - preserves the
    category label, every other skill, existing separators, and formatting
    exactly as they were; only the tail of the line grows."""
    display_skill = _format_skill_display(skill)
    current_text = paragraph.text
    trailing = current_text.rstrip()
    separator = ", " if trailing and not trailing.endswith((",", ";", ":")) else " "
    if paragraph.runs:
        paragraph.runs[-1].text = f"{paragraph.runs[-1].text}{separator}{display_skill}"
    else:
        paragraph.add_run(f"{separator}{display_skill}")


def _is_bullet_paragraph(paragraph: Paragraph) -> bool:
    text = paragraph.text.strip()
    style_name = (paragraph.style.name if paragraph.style is not None else "") or ""
    return bool(_BULLET_PREFIX_RE.match(text)) or "list" in style_name.lower()


_TRAILING_SUMMARY_LINE_RE = re.compile(
    r"^(tech(nical)?\s*stack|technologies\s+used|key\s+technologies|environment)\s*:", re.IGNORECASE,
)


def _find_experience_block_locations(document: Document) -> list[ExperienceBlockLocation]:
    """Splits the Experience section into one block per job: a run of
    non-bullet "header" lines (company/title/dates) followed by its bullet
    points, ending where the next non-bullet line starts a new job. Only
    blocks with BOTH an identifying line and at least one bullet are used as
    safe insertion targets.

    A trailing "TECH STACK: ..."/"Environment: ..." summary line is a very
    common way to close out a job's bullets - it's a NON-bullet paragraph,
    so without special-casing it here it gets swept up as the first "header"
    line of the NEXT job instead (it belongs to the job just finished, not
    the one after it), bloating every experience_identifier after the first
    with an irrelevant wall of comma-separated tech names. On a resume with
    several jobs this made the identifiers so long and noisy that a real
    local LLM call reliably lost track of the JSON schema entirely - this
    line is simply dropped from both blocks' identifiers."""
    paragraphs = document.paragraphs
    bounds = _find_section_content_bounds(paragraphs, EXPERIENCE_HEADING_RE)
    if bounds is None:
        return []
    start, end = bounds

    raw_blocks: list[tuple[list[int], list[int]]] = []
    header_idxs: list[int] = []
    bullet_idxs: list[int] = []
    for idx in range(start, end):
        text = paragraphs[idx].text.strip()
        if not text:
            continue
        if _is_bullet_paragraph(paragraphs[idx]):
            bullet_idxs.append(idx)
        else:
            just_closed_a_block = bool(bullet_idxs)
            if bullet_idxs:
                raw_blocks.append((header_idxs, bullet_idxs))
                header_idxs, bullet_idxs = [], []
            if just_closed_a_block and _TRAILING_SUMMARY_LINE_RE.match(text):
                continue  # closes out the PREVIOUS job, not the header of the next one
            header_idxs.append(idx)
    if header_idxs or bullet_idxs:
        raw_blocks.append((header_idxs, bullet_idxs))

    blocks: list[ExperienceBlockLocation] = []
    for h_idxs, b_idxs in raw_blocks:
        if not h_idxs or not b_idxs:
            continue
        identifier = " — ".join(paragraphs[i].text.strip() for i in h_idxs)
        last_bullet = b_idxs[-1]
        insert_before = paragraphs[last_bullet + 1] if last_bullet + 1 < end else (
            paragraphs[end] if end < len(paragraphs) else None
        )
        blocks.append(ExperienceBlockLocation(
            identifier=identifier, bullet_reference=paragraphs[b_idxs[-1]], insert_before=insert_before,
        ))
    return blocks


def _match_experience_block(identifier: object, blocks: list[ExperienceBlockLocation]) -> ExperienceBlockLocation | None:
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    needle = identifier.strip().lower()
    for block in blocks:
        haystack = block.identifier.lower()
        if needle in haystack or haystack in needle:
            return block
    return None


def _build_resume_structure_summary(
    header_paragraph: Paragraph | None,
    skill_locations: list[SkillCategoryLocation],
    experience_blocks: list[ExperienceBlockLocation],
    has_summary: bool,
) -> dict:
    return {
        "current_header_role": header_paragraph.text.strip() if header_paragraph is not None else None,
        "has_summary_section": has_summary,
        "skill_categories": {loc.label: loc.existing_items for loc in skill_locations},
        "experience_identifiers": [b.identifier for b in experience_blocks],
    }


def _validate_plan(
    raw_plan: dict,
    header_paragraph: Paragraph | None,
    skill_locations: list[SkillCategoryLocation],
    experience_blocks: list[ExperienceBlockLocation],
    approved_skills: set[str],
    settings: Settings,
) -> ValidatedCustomizationPlan:
    if not isinstance(raw_plan, dict):
        return ValidatedCustomizationPlan()
    approved_canonical = {_canonical_skill(s) for s in approved_skills}

    header_role = None
    raw_header_role = raw_plan.get("header_role")
    if header_paragraph is not None and isinstance(raw_header_role, str) and raw_header_role.strip():
        # Defense-in-depth: even though jd_extractor already strips
        # emoji/symbols from the JD title the model is given, the model can
        # still reintroduce its own decorative punctuation in its answer -
        # never let that land in the resume header.
        candidate = clean_role_text(" ".join(raw_header_role.split()).strip())
        current = header_paragraph.text.strip()
        if candidate and 0 < len(candidate) <= 120 and candidate.lower() != current.lower():
            header_role = candidate

    summary_points: list[str] = []
    raw_summary = raw_plan.get("summary_points")
    if isinstance(raw_summary, list):
        for item in raw_summary:
            if len(summary_points) >= max(0, settings.RESUME_LLM_MAX_SUMMARY_POINTS):
                break
            cleaned = _validate_generated_statement(item, approved_canonical)
            if cleaned:
                summary_points.append(cleaned)

    skills: list[ValidatedSkillAddition] = []
    seen_canonical: set[str] = set()
    raw_skills = raw_plan.get("skills_to_add")
    if isinstance(raw_skills, list):
        for item in raw_skills:
            if len(skills) >= max(0, settings.RESUME_LLM_MAX_SKILLS):
                break
            if not isinstance(item, dict):
                continue
            skill_name = item.get("skill")
            if not isinstance(skill_name, str) or not skill_name.strip():
                continue
            skill_name = skill_name.strip()
            canonical = _canonical_skill(skill_name)
            if canonical not in approved_canonical or canonical in seen_canonical:
                continue  # unverified, or already queued once in this plan
            location = _select_target_category(skill_name, str(item.get("category") or ""), skill_locations)
            if location is None or canonical in location.existing_canonical:
                continue  # no safe existing category, or already listed there
            seen_canonical.add(canonical)
            skills.append(ValidatedSkillAddition(skill=skill_name, location=location))

    experience_updates: list[ValidatedExperienceUpdate] = []
    raw_updates = raw_plan.get("experience_updates")
    if isinstance(raw_updates, list):
        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            block = _match_experience_block(item.get("experience_identifier"), experience_blocks)
            if block is None:
                continue
            raw_points = item.get("points")
            if not isinstance(raw_points, list):
                continue
            points: list[str] = []
            for point in raw_points:
                if len(points) >= max(0, settings.RESUME_LLM_MAX_EXPERIENCE_POINTS):
                    break
                cleaned = _validate_generated_statement(point, approved_canonical)
                if cleaned:
                    points.append(cleaned)
            if points:
                experience_updates.append(ValidatedExperienceUpdate(block=block, points=points))

    return ValidatedCustomizationPlan(header_role, summary_points, skills, experience_updates)


def _apply_customization_plan(
    document: Document,
    plan: ValidatedCustomizationPlan,
    header_paragraph: Paragraph | None,
    summary_bounds: tuple[int, int] | None,
    summary_reference: Paragraph | None,
) -> None:
    if plan.header_role and header_paragraph is not None:
        _replace_role_text_preserving_format(header_paragraph, plan.header_role)

    if plan.summary_points and summary_reference is not None:
        anchor = None
        if summary_bounds is not None:
            _, end = summary_bounds
            paragraphs = document.paragraphs
            anchor = paragraphs[end] if end < len(paragraphs) else None
        _insert_paragraphs_before(document, anchor, plan.summary_points, summary_reference)

    for skill_addition in plan.skills:
        target = skill_addition.location.paragraph or skill_addition.location.cell_paragraph
        if target is not None:
            _append_skill_to_paragraph(target, skill_addition.skill)

    for update in plan.experience_updates:
        if update.block.bullet_reference is not None:
            _insert_paragraphs_before(document, update.block.insert_before, update.points, update.block.bullet_reference)


def _all_paragraph_texts(document: Document) -> list[str]:
    """Body paragraphs, then every table cell's paragraphs (tables and
    section headers/footers aren't reachable through `document.paragraphs`
    at all) - so a header/skill line that happens to live inside a table
    cell (a very common resume layout) is still covered by the integrity
    check below, not silently invisible to it."""
    return [p.text for p in _all_paragraphs(document)]


def _all_paragraphs(document: Document) -> list[Paragraph]:
    """Same traversal/order as `_all_paragraph_texts`, but the Paragraph
    objects themselves - lets a specific paragraph be correlated to its
    POSITION in that ordering (see `_llm_customization_is_valid`)."""
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    return paragraphs


def _llm_customization_is_valid(
    original_document: Document, new_path: str, modified_paragraphs: list[tuple[int, str, str]],
) -> bool:
    """Like `_customization_is_format_safe`, but aware that a small, known
    set of EXISTING paragraphs were intentionally changed IN PLACE (the
    header role line, and any skill-category line that received an appended
    skill) rather than purely added-to. Every other original paragraph must
    still survive byte-for-byte, in order; every intentional change must
    have actually landed.

    `modified_paragraphs` identifies each changed paragraph by its POSITION
    in `_all_paragraph_texts` order (captured before editing), not by its
    text value - a resume's header role very commonly appears verbatim a
    second time elsewhere (e.g. as an Experience entry's own job title), and
    matching by value with `list.remove` would silently remove the WRONG
    (unchanged) occurrence, then fail validation on the real one."""
    try:
        original_texts = _all_paragraph_texts(original_document)
        reopened = docx.Document(new_path)
        new_texts = _all_paragraph_texts(reopened)
    except Exception:
        logger.warning("LLM-customized resume failed to re-open cleanly; treating as a validation failure", exc_info=True)
        return False

    if len(new_texts) < len(original_texts):
        logger.warning("LLM-customized resume has less content than the original; treating as a validation failure")
        return False

    modified_indices: set[int] = set()
    for index, original_text, expected_new_text in modified_paragraphs:
        if original_text == expected_new_text:
            continue
        if index >= len(original_texts) or original_texts[index] != original_text:
            logger.warning("LLM customization validation: an expected original paragraph was not found")
            return False
        if expected_new_text not in new_texts:
            logger.warning("LLM customization validation: an intended change did not land as expected")
            return False
        modified_indices.add(index)

    expected_originals = [text for i, text in enumerate(original_texts) if i not in modified_indices]
    if not _is_subsequence_of_texts(expected_originals, new_texts):
        logger.warning("LLM-customized resume lost or reordered original content; treating as a validation failure")
        return False

    return True


def generate_llm_customized_resume(
    resume: Resume,
    jd_title: str | None,
    jd_text: str,
    jd_requirements: list[str],
    ai_provider: AIProvider | None,
    settings: Settings,
    dest_dir: str,
) -> tuple[str, str] | None:
    """
    The enhanced, LLM-assisted customization path: rewrites the header
    role, adds 1-2 summary lines, surfaces verified-but-underemphasized
    skills into their correct existing category, and adds 1-2 bullets to
    relevant existing experience entries - always starting fresh from
    `resume.file_path` (the original library file, never a prior
    customized copy), always saving only a NEW file, never touching the
    original. On ANY failure or doubt at any stage - Ollama/OpenAI
    unavailable, malformed response, an unverified claim, a section that
    can't be confidently located, a save/validation failure - this returns
    None so the caller falls back to the plain deterministic
    `customize_resume_file` above; it never raises and never corrupts a
    valid resume just to chase a better JD match.
    """
    if ai_provider is None or not settings.RESUME_LLM_CUSTOMIZATION_ENABLED:
        return None
    if Path(resume.filename).suffix.lower() != ".docx":
        return None  # PDF/DOC: never rewritten, to avoid corrupting the layout
    if not resume.file_path or not Path(resume.file_path).exists():
        logger.warning("LLM resume customization: source file missing for resume id=%s", getattr(resume, "id", None))
        return None

    original_stat = Path(resume.file_path).stat()

    try:
        document = docx.Document(resume.file_path)
    except Exception:
        logger.warning("LLM resume customization: source resume failed to open", exc_info=True)
        return None

    known_titles = (resume.extracted_metadata or {}).get("job_titles", []) or []
    header_paragraph = _find_header_role_paragraph(document, known_titles)
    skill_locations = _find_skill_category_locations(document)
    experience_blocks = _find_experience_block_locations(document)
    summary_bounds = _find_section_content_bounds(document.paragraphs, SUMMARY_HEADING_RE)
    summary_reference = (
        _reference_paragraph_for_section(document.paragraphs, *summary_bounds) if summary_bounds is not None else None
    )

    if header_paragraph is None and not skill_locations and not experience_blocks and summary_reference is None:
        logger.warning("LLM resume customization: no recognizable structure to work with, skipping")
        return None

    approved_skills = _candidate_metadata_skills(resume)
    approved_experience_identifiers = [b.identifier for b in experience_blocks]
    resume_structure = _build_resume_structure_summary(
        header_paragraph, skill_locations, experience_blocks, summary_reference is not None,
    )

    logger.info(
        "LLM resume customization started: resume_id=%s filename=%s jd_title=%s",
        getattr(resume, "id", None), resume.filename, jd_title,
    )

    try:
        raw_plan = ai_provider.generate_resume_customization_plan(
            resume_text=resume.extracted_text or "",
            resume_structure=resume_structure,
            jd_title=jd_title or "",
            jd_text=jd_text,
            jd_requirements=jd_requirements,
            approved_skills=sorted(approved_skills),
            approved_experience_identifiers=approved_experience_identifiers,
        )
    except Exception as exc:
        # WARNING (not INFO) so this is never silently invisible - there is
        # no logging.basicConfig anywhere in this app, so INFO-level records
        # never reach stderr by default and a real Ollama timeout/outage
        # would fall back to the deterministic path with zero visible trace.
        logger.warning(
            "LLM resume customization: provider call failed (%s: %s), falling back to deterministic",
            type(exc).__name__, exc,
        )
        return None

    plan = _validate_plan(raw_plan, header_paragraph, skill_locations, experience_blocks, approved_skills, settings)
    if plan.is_empty():
        logger.warning("LLM resume customization: nothing survived validation, falling back to deterministic")
        return None

    # Capture (paragraph, position, original_text) for every paragraph the
    # plan may change IN PLACE, before applying anything - reading the SAME
    # live objects back afterward is simpler and always exactly correct,
    # regardless of how many skills land in one paragraph. POSITION (not
    # just the text) is captured because a resume's header role very
    # commonly appears verbatim a second time elsewhere (e.g. also as an
    # Experience entry's own job title) - matching the modified paragraph
    # back up by text value alone would be ambiguous.
    # python-docx's `.paragraphs`/`cell.paragraphs` return a FRESH Paragraph
    # wrapper object on every access, even for the exact same underlying XML
    # element - so `id(paragraph)` is never stable across two separate calls
    # (e.g. the one inside `_all_paragraphs` here vs. the one that produced
    # `header_paragraph`/`location.paragraph` earlier). The wrapper's
    # underlying `._p` lxml element IS the same object every time, so
    # position lookup keys off that instead.
    position_by_id = {id(p._p): i for i, p in enumerate(_all_paragraphs(document))}
    tracked: list[tuple[Paragraph, int, str]] = []
    if plan.header_role and header_paragraph is not None:
        tracked.append((header_paragraph, position_by_id[id(header_paragraph._p)], header_paragraph.text))
    seen_ids: set[int] = set()
    for skill_addition in plan.skills:
        # A skill's target may be a plain "Label: a, b, c" paragraph OR a
        # table-cell paragraph (see _apply_customization_plan's identical
        # `paragraph or cell_paragraph` fallback) - both are mutated in
        # place, so both must be tracked here, or a validated skill landing
        # in a table-based skills section fails the post-save integrity
        # check below every time (its old text is "expected unchanged" but
        # is actually different) and the whole customization is discarded.
        p = skill_addition.location.paragraph or skill_addition.location.cell_paragraph
        if p is not None and id(p._p) not in seen_ids:
            seen_ids.add(id(p._p))
            tracked.append((p, position_by_id[id(p._p)], p.text))

    try:
        _apply_customization_plan(document, plan, header_paragraph, summary_bounds, summary_reference)
        display_filename = CUSTOMIZED_RESUME_DISPLAY_FILENAME
        # See customize_resume_file() above for why this lives in a
        # per-customization subdirectory rather than being prefixed itself -
        # the FILE is always exactly "Diwakar_Resume.docx".
        customization_dir = Path(dest_dir) / uuid.uuid4().hex[:8]
        customization_dir.mkdir(parents=True, exist_ok=True)
        new_path = customization_dir / display_filename
        document.save(str(new_path))
    except Exception:
        logger.warning("LLM resume customization: failed while editing/saving; falling back to deterministic", exc_info=True)
        return None

    # The original is only ever OPENED (never mutated) above, and only
    # `new_path` is ever saved to - verify that guarantee held in practice.
    new_stat = Path(resume.file_path).stat()
    if new_stat.st_size != original_stat.st_size or new_stat.st_mtime != original_stat.st_mtime:
        logger.error("LLM resume customization: original resume file changed unexpectedly - discarding output")
        _discard_customization_output(new_path)
        return None

    modified_paragraphs = [(position, original_text, p.text) for p, position, original_text in tracked if p.text != original_text]
    original_document = docx.Document(resume.file_path)
    if not _llm_customization_is_valid(original_document, str(new_path), modified_paragraphs):
        _discard_customization_output(new_path)
        return None

    logger.info(
        "LLM resume customization succeeded: header_changed=%s summary_points=%d skills_added=%d experience_updates=%d",
        bool(plan.header_role), len(plan.summary_points), len(plan.skills), len(plan.experience_updates),
    )
    return str(new_path), display_filename

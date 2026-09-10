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
from app.models import Resume
from app.services.resume_matcher import ResumeScore

logger = logging.getLogger("app.resume_customizer")


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

        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        display_filename = f"Customized_{resume.filename}"
        new_path = Path(dest_dir) / f"{uuid.uuid4().hex[:8]}_{display_filename}"
        document.save(str(new_path))
    except Exception:
        logger.warning("resume customization failed while editing the document; keeping the original resume", exc_info=True)
        return None

    # Independent post-save check, every time, for every email - a
    # formatting problem always reverts to attaching the plain resume.
    original_document = docx.Document(resume.file_path)
    if not _customization_is_format_safe(original_document, str(new_path)):
        try:
            Path(new_path).unlink(missing_ok=True)
        except OSError:
            pass
        return None

    return str(new_path), display_filename

"""
RecoveryLens — guidance/ingest.py
=================================
Parses full guideline documents into a retrievable corpus.

Why this exists
---------------
The original corpus was 39 excerpts I typed by hand. Precise section numbers,
but capped at what fits in one sitting — and measurably too small: across 25
realistic clinician questions the retriever refused 16 of them (64%), including
spasticity, fatigue, cognition, mouth care and telerehabilitation. NICE NG236
covers every one of those. The guideline had the answers; the corpus did not.

Hand-curation was the wrong tool for coverage. This is the right one.

One corpus, two provenance levels
---------------------------------
There used to be two files — `corpus.json` (hand-verified) and
`corpus_full.json` (auto-extracted) — and the patient-facing path was kept away
from the auto-extracted material by the fact that ingested chunks carry no
`trigger`. That worked, but the guarantee lived in an absence rather than in a
statement, and an absence is easy to break: it already produced one 500 when
`RetrievedPassage.trigger` was typed as non-null.

There is now one `corpus.json`, and the line is drawn by a field that says what
it means:

    extraction: "curated"    section number checked by a human. Eligible for the
                             patient-facing trigger cards, where a wrong citation
                             is worst.
    extraction: "automatic"  parsed from the source document. Clinician Q&A only.
                             Coverage matters more there and a trained reader can
                             spot an off-target result.

`registry.py` filters on `extraction == "curated"`, so nothing auto-extracted can
reach a carer even if it somehow acquires a trigger.

Citation precision
------------------
A second field, because "cited" is not one thing:

    citation_precision: "recommendation"   the number points at the exact
                                           recommendation quoted
    citation_precision: "section"          the number points at the section the
                                           quote sits in, which may be several
                                           pages

Only NICE and RCP support recommendation-level citation. ISA does not — see
`parse_isa`. Chunks at section precision carry a caveat saying so, and
`retrieval.py` caps how many of them can appear in one answer, so a coarse
citation can never be the whole basis of a response.

Per-source structure
--------------------
Every source numbers things differently and each needs its own parser. What they
share is the rule that matters: the number is READ FROM THE DOCUMENT, never
invented, and a chunk whose number cannot be parsed is dropped rather than
guessed at.

    NICE   1.8.2, 1.13.10           `parse_nice`
    RCP    5.7 A, 4.24 C            `parse_rcp` — section number plus the
                                    guideline's own recommendation letter
    ISA    12.0, 13.0               `parse_isa` — section only; ISA's
                                    recommendations are unnumbered bullets
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import sys

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "corpus.json"

# Chunks shorter than this are fragments — list bullets, cross-references — and
# retrieve badly. Longer than the cap, they cover too many topics to rank well.
MIN_WORDS = 8
MAX_WORDS = 120

# RCP recommendations are routinely longer than NICE's because a single lettered
# recommendation carries its own conditions as sub-bullets. 5.7 A is ~200 words
# and every one of them is load-bearing: it holds "should not be given if brain
# imaging has identified significant haemorrhage". Truncating a conditional
# clinical recommendation to fit a word budget changes what it says, which is a
# worse failure than quoting it at length. So RCP gets a higher cap, and the
# display-side limit is where brevity is enforced instead.
RCP_MAX_WORDS = 250

# "1.8.2." or "1.8.2" alone on a line begins a NICE recommendation.
_REC_NUMBER = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\.?\s*$")
# "### 1.8. Vision" — the section heading these recommendations sit under.
_HEADING = re.compile(r"^#{2,4}\s*(\d+\.\d+)\.?\s*(.+?)\s*$")
# NICE and RCP both tag recommendations with the year last reviewed.
_YEAR_TAG = re.compile(r"\*\*\[([\d,\s a-z]+)\]\*\*", re.IGNORECASE)
# Markdown links: keep the text, drop the URL.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")

# --- RCP (strokeguideline.org) ---------------------------------------------
# "### 5.7 Anticoagulation" or "### 4.23.1 Neuropathic pain". Optional heading
# markers so this works whether the extractor marked headings or not.
_RCP_SECTION = re.compile(r"^(?:#{2,4}\s*)?(\d+\.\d+(?:\.\d+)?)\s+(\D.*?)\s*$")
# The literal marker that separates background prose from recommendations. Only
# text after it is citable as a recommendation; the prose before it is
# discussion, and quoting discussion as guidance would misrepresent the source.
_RCP_RECS_MARKER = re.compile(r"^(?:#{2,4}\s*)?Recommendations\s*$", re.IGNORECASE)
# A lettered recommendation: a single capital alone on a line.
_RCP_LETTER = re.compile(r"^([A-Z])$")
# The site renders a truncated preview of each section's prose followed by the
# full text. The preview ends in this, and indexing both would duplicate content.
_RCP_TRUNCATED = "Show more"

# --- ISA (PDF) --------------------------------------------------------------
# "12.0 STROKE REHABILITATION" — ISA numbers SECTIONS but not recommendations.
_ISA_SECTION = re.compile(r"^(\d+\.\d)\s+([A-Z][A-Z\s,()/&-]{4,})\s*$")
_ISA_RECS_MARKER = re.compile(r"^Recommendations[\s:0-9]*$", re.IGNORECASE)
_ISA_BULLET = re.compile(r"^\s*[•➢o]\s+(.*)$")
# PDF extraction glues reference markers onto the end of sentences: "stroke
# survivors.27" and "trials.180". Left in, they appear inside a verbatim quote and
# read as a typo in the guideline rather than in our parser.
#
# Stripping them is more dangerous than it looks. The first version of this
# pattern was `(?<=[a-z.,;)])\d{1,3}(?=\s|$|[.,;])`, which turned
#
#     "administered to eligible patients within 4.5 hours of symptom onset"
#
# into "within 4. hours" — the 5 sat after a full stop and matched. A silently
# altered drug window is the worst possible output of a guideline parser, and it
# would have been quoted verbatim with a citation attached.
#
# So: a footnote marker is only recognised where it CANNOT be part of a number.
# The digit run must follow a letter, or punctuation that itself follows a letter.
# Chains like "risk factors.181,182" are handled as one run.
_ISA_FOOTNOTE = re.compile(
    r"(?<=[a-z])(\d{1,3}(?:,\d{1,3})*)(?=\s|$|[.,;:)])"
    r"|(?<=[a-z][.,;)])(\d{1,3}(?:,\d{1,3})*)(?=\s|$|[.,;])"
)

# Anything that looks like a clinical quantity: 4.5 hours, 180/105 mmHg, 75 mg.
# Used to VERIFY the stripper rather than to do the stripping — see `_strip_refs`.
_QUANTITY = re.compile(r"\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?")


def _strip_refs(text: str) -> str:
    """Remove PDF reference markers, and refuse to if any quantity changed.

    The regex above is careful, but "careful" is not a guarantee, and the failure
    mode is silent alteration of a number inside text that will be displayed as a
    verbatim quote. So the result is checked: every quantity present before must
    still be present after. If one went missing the strip is abandoned and the
    original is kept — a visible "survivors.27" is a cosmetic problem, whereas
    "4. hours" is a clinical one.
    """
    stripped = re.sub(r"\s+", " ", _ISA_FOOTNOTE.sub("", text)).strip()

    before = _QUANTITY.findall(text)
    after = _QUANTITY.findall(stripped)
    for quantity in before:
        # Only quantities that are not bare integers can be footnote markers;
        # a bare integer legitimately disappears when it WAS a marker.
        if ("." in quantity or "/" in quantity) and quantity not in after:
            return re.sub(r"\s+", " ", text).strip()
    return stripped


@dataclass
class Chunk:
    source_id: str
    section: str
    heading: str
    text: str
    year_tag: str = ""
    extraction: str = "automatic"
    # "recommendation" = the number points at exactly what is quoted.
    # "section" = it points at the section the quote sits in. See module docstring.
    citation_precision: str = "recommendation"
    caveat: str | None = None
    # The page this chunk came from. Falls back to the source's own url when
    # empty. RCP publishes one page per chapter, so without this every RCP
    # citation would link to strokeguideline.org's front page — and a citation
    # nobody can follow is decoration. The whole design leans on a reader being
    # able to check a quote against the published document.
    url: str = ""
    # Only used to keep ids unique where several chunks share one section number
    # (ISA). Never rendered — see the note at the end of `parse_isa`.
    ordinal: int = 0

    @property
    def id(self) -> str:
        slug = self.section.replace(".", "_").replace(" ", "")
        suffix = f"_{self.ordinal}" if self.ordinal else ""
        return f"{self.source_id}_{slug}{suffix}"

    def to_json(self) -> dict:
        out = {
            "id": self.id, "source_id": self.source_id, "section": self.section,
            "heading": self.heading, "excerpt": self.text,
            "year_tag": self.year_tag, "extraction": self.extraction,
            "citation_precision": self.citation_precision,
        }
        if self.caveat:
            out["caveat"] = self.caveat
        if self.url:
            out["url"] = self.url
        return out


def clean(line: str) -> str:
    line = _MD_LINK.sub(r"\1", line)
    line = _YEAR_TAG.sub("", line)
    line = line.replace("**", "").replace("’", "'")
    return re.sub(r"\s+", " ", line).strip()


def _dedupe(chunks: list[Chunk]) -> list[Chunk]:
    """Keep the first chunk per section number.

    A guideline never has two recommendations with the same number; duplicates
    mean the parser has mis-read something, so the later ones are the suspect
    ones.
    """
    seen: set[str] = set()
    unique: list[Chunk] = []
    for c in chunks:
        if c.section in seen:
            continue
        seen.add(c.section)
        unique.append(c)
    return unique


def parse_rcp(text: str, source_id: str) -> list[Chunk]:
    """Extract lettered recommendations from the RCP National Clinical Guideline.

    RCP structures each section as background prose, the literal word
    "Recommendations", then recommendations labelled A, B, C. So the citable
    reference is the section number plus the letter — "5.7 A" — which is the
    guideline's own scheme and deep-links correctly.

    Only text after the "Recommendations" marker is taken. The prose before it is
    discussion of the evidence, and quoting discussion as though it were a
    recommendation would misrepresent the source in the one way that matters.
    """
    chunks: list[Chunk] = []
    section = ""
    heading = ""
    in_recs = False
    letter: str | None = None
    buffer: list[str] = []
    year = ""

    def _flush() -> None:
        nonlocal letter, buffer, year
        if section and letter and buffer:
            body = clean(" ".join(buffer))
            words = len(body.split())
            if MIN_WORDS <= words <= RCP_MAX_WORDS:
                chunks.append(Chunk(
                    source_id=source_id, section=f"{section} {letter}",
                    heading=heading, text=body, year_tag=year))
        letter, buffer, year = None, [], ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _RCP_TRUNCATED in line:
            continue

        m = _RCP_SECTION.match(line)
        if m:
            _flush()
            section, heading = m.group(1), clean(m.group(2))
            in_recs = False
            continue

        if _RCP_RECS_MARKER.match(line):
            _flush()
            in_recs = True
            continue

        if not in_recs:
            continue

        m = _RCP_LETTER.match(line)
        if m:
            _flush()
            letter = m.group(1)
            continue

        if letter:
            tag = _YEAR_TAG.search(line)
            if tag:
                year = tag.group(1).strip()
            buffer.append(re.sub(r"^[-*–]\s*", "", line))

    _flush()
    return _dedupe(chunks)


# The caveat every ISA chunk carries. Stated on the chunk rather than left to the
# reader to infer, because a section number LOOKS like a precise citation.
ISA_SECTION_CAVEAT = (
    "This citation points to a section of the ISA consensus statement, not to a "
    "numbered recommendation — ISA presents its recommendations as unnumbered "
    "bullets, so no finer reference exists in the source. Verify against the "
    "section before relying on it."
)


def parse_isa(text: str, source_id: str) -> list[Chunk]:
    """Extract recommendation bullets from the ISA consensus statement PDF.

    ISA numbers its SECTIONS ("12.0 STROKE REHABILITATION") but presents
    recommendations as unnumbered bullets under an unnumbered "Recommendations"
    heading. There is therefore no recommendation-level citation available: the
    finest reference the document supports is the section, which can run to
    several pages.

    That is a real weakness and it is recorded on every chunk rather than papered
    over. `citation_precision` is "section" and `caveat` says so in words, so a
    clinician reading the answer knows the number they are being shown is coarser
    than the ones beside it from NICE and RCP.

    Bullets are grouped rather than emitted individually: a single ISA bullet is
    often a sentence fragment that only means something in the company of the
    ones around it.
    """
    chunks: list[Chunk] = []
    section = ""
    heading = ""
    in_recs = False
    buffer: list[str] = []

    def _flush() -> None:
        nonlocal buffer
        if section and buffer:
            body = _strip_refs(clean(" ".join(buffer)))
            words = len(body.split())
            if MIN_WORDS <= words <= MAX_WORDS:
                chunks.append(Chunk(
                    source_id=source_id, section=section, heading=heading,
                    text=body, citation_precision="section",
                    caveat=ISA_SECTION_CAVEAT))
        buffer = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        m = _ISA_SECTION.match(line)
        if m:
            _flush()
            section = m.group(1)
            heading = clean(m.group(2).title())
            in_recs = False
            continue

        if _ISA_RECS_MARKER.match(line):
            _flush()
            in_recs = True
            continue

        if not in_recs or not section:
            continue

        m = _ISA_BULLET.match(line)
        if m:
            # A new bullet ends the previous group only once the group is big
            # enough to stand alone; ISA bullets wrap across several PDF lines.
            if len(" ".join(buffer).split()) >= MAX_WORDS // 2:
                _flush()
            buffer.append(m.group(1).strip())
        elif buffer:
            # Continuation of the bullet above — PDF line wrapping.
            buffer.append(line)

    _flush()

    # Several chunks legitimately share one section number here — that is the
    # whole point of section-level precision — so `_dedupe` must not run: it
    # would keep the first group per section and silently discard the rest.
    # Ids still have to be unique, so they get an ordinal WITHIN the section.
    # The ordinal is part of the id only. It never appears in `section`, because
    # inventing "12.0 (3)" would imply the source has a numbering it does not.
    per_section: dict[str, int] = {}
    for c in chunks:
        per_section[c.section] = per_section.get(c.section, 0) + 1
        c.ordinal = per_section[c.section]
    return chunks


def parse(text: str, source_id: str) -> list[Chunk]:
    """Extract numbered recommendations from a NICE guideline document."""
    chunks: list[Chunk] = []
    heading = ""
    current_number: str | None = None
    buffer: list[str] = []
    year = ""

    def _flush() -> None:
        nonlocal current_number, buffer, year
        if current_number and buffer:
            body = clean(" ".join(buffer))
            words = len(body.split())
            # Drop fragments and over-long spans rather than indexing something
            # that will retrieve badly and cite imprecisely.
            if MIN_WORDS <= words <= MAX_WORDS:
                chunks.append(Chunk(source_id=source_id, section=current_number,
                                    heading=heading, text=body, year_tag=year))
        current_number, buffer, year = None, [], ""

    for raw in text.splitlines():
        line = raw.rstrip()

        m = _HEADING.match(line)
        if m:
            _flush()
            heading = clean(m.group(2))
            continue

        m = _REC_NUMBER.match(line.strip())
        if m:
            _flush()
            current_number = m.group(1)
            continue

        if not line.strip():
            # A blank line ends a recommendation only if we have collected text.
            if buffer:
                _flush()
            continue

        if current_number:
            tag = _YEAR_TAG.search(line)
            if tag:
                year = tag.group(1).strip()
            # Bullets belong to the recommendation above them.
            buffer.append(re.sub(r"^[-*–]\s*", "", line.strip()))

    _flush()

    # A guideline never has two recommendations with the same number; if the
    # parser produced duplicates it has mis-read something, so keep the first.
    seen: set[str] = set()
    unique = []
    for c in chunks:
        if c.section in seen:
            continue
        seen.add(c.section)
        unique.append(c)
    return unique


@dataclass
class IngestReport:
    source_id: str
    chunks: int
    sections: list[str] = field(default_factory=list)
    sample: str = ""
    # How many of these can only cite a section, not a recommendation. Reported
    # per source so the coarse fraction of the corpus is a number, not a vibe.
    coarse: int = 0


PARSERS = {"nice": parse, "rcp": parse_rcp, "isa": parse_isa}


def ingest(documents: dict[str, list[tuple[str, str]]]
           ) -> tuple[list[dict], list[IngestReport]]:
    """documents: {source_id: [(url, text), ...]}. Returns (chunks, reports).

    Each (url, text) pair is parsed separately rather than concatenated, so every
    chunk knows the page it came from and can carry a working deep link. RCP
    publishes one page per chapter; concatenating first would have left all of
    them pointing at the site's front page.

    The parser is chosen from SOURCES, defaulting to the NICE one so a source
    added without a declared parser fails visibly (zero chunks) rather than being
    silently parsed by the wrong rules.
    """
    all_chunks: list[dict] = []
    reports: list[IngestReport] = []

    for source_id, parts in documents.items():
        parser_name = SOURCES.get(source_id, {}).get("parser", "nice")
        parsed: list[Chunk] = []
        for url, text in parts:
            for chunk in PARSERS[parser_name](text, source_id):
                chunk.url = url
                parsed.append(chunk)

        parsed = _dedupe_across_pages(parsed, parser_name)
        all_chunks.extend(c.to_json() for c in parsed)
        reports.append(IngestReport(
            source_id=source_id, chunks=len(parsed),
            sections=sorted({c.section.split()[0].rsplit(".", 1)[0]
                             for c in parsed}),
            sample=parsed[0].text[:100] if parsed else "",
            coarse=sum(1 for c in parsed
                       if c.citation_precision == "section"),
        ))

    return all_chunks, reports


def _dedupe_across_pages(chunks: list[Chunk], parser_name: str) -> list[Chunk]:
    """Drop repeats of the same section number across pages of one source.

    RCP repeats a few sections between chapters — "Remotely delivered therapy and
    telerehabilitation" appears as both 2.9 and 4.5 — and the site's navigation
    can echo a heading from a neighbouring chapter. Within a single page
    `_dedupe` already ran; this catches the cross-page case.

    Skipped for ISA, where several chunks share one section by design.
    """
    if parser_name == "isa":
        return chunks
    return _dedupe(chunks)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
# NICE's own pages are client-rendered and return an empty shell to a plain HTTP
# fetch. The NCBI Bookshelf mirrors carry the same text as static HTML, which is
# why they are used here. CG76 is fetchable directly from nice.org.uk.
SOURCES: dict[str, dict] = {
    "nice_ng236": {
        "urls": ["https://www.ncbi.nlm.nih.gov/books/NBK598564/"],
        "parser": "nice",
    },
    "nice_ng128": {
        "urls": ["https://www.ncbi.nlm.nih.gov/books/NBK542436/"],
        "parser": "nice",
    },
    "nice_cg76": {
        "urls": ["https://www.nice.org.uk/guidance/cg76/chapter/Recommendations"],
        "parser": "nice",
    },
    # RCP publishes one page per chapter, each with its own numbering range.
    # Chapter 1 (guideline development) and chapter 6 (implementation) are
    # omitted: they describe methodology and service structure, and contain no
    # recommendation that bears on an individual patient after discharge.
    "ncgs_2023": {
        "urls": [
            f"https://www.strokeguideline.org/chapter/{slug}/"
            for slug in (
                "organisation-of-stroke-services",                    # 2.x
                "acute-care",                                        # 3.x
                "rehabilitation-and-recovery-principles-of-rehabilitation",  # 4.0-4.6
                "activity-and-participation",                        # 4.7-4.15
                "motor-recovery-and-physical-effects-of-stroke",     # 4.16-4.26
                "psychological-effects-of-stroke",                   # 4.27-4.41
                "communication-and-language",                        # 4.42-4.45
                "sensory-effects-of-stroke",                         # 4.46-4.48
                "long-term-management-and-secondary-prevention",     # 5.x
            )
        ],
        "parser": "rcp",
    },
    # A PDF, so it needs a text extractor the HTML sources do not. Skipped with a
    # message rather than failing the run if pypdf is absent — the other four
    # sources are worth more than this one and must not be held hostage to it.
    #
    # stroke-india.org returns 403 to a programmatic request for this file: the
    # host serves it to a browser and refuses a client it does not recognise.
    # Rather than dress the request up as something it is not, ingestion looks for
    # the file on disk FIRST (see LOCAL_DIR). Download it once by hand and the
    # ingest becomes reproducible and offline, which is better than a hotlink
    # either way — a URL that works today is not a source you control.
    "isa_2024": {
        "urls": ["https://stroke-india.org/wp-content/uploads/2024/07/stroke.pdf"],
        "parser": "isa",
        "local": "isa_2024.pdf",
    },
}

# Documents downloaded by hand live here. Gitignored: these are third-party
# publications and committing them would be redistribution, which is exactly what
# the licensing posture avoids.
LOCAL_DIR = HERE / "sources"



class _Extractor(HTMLParser):
    """HTML to the structured text `parse()` expects.

    Headings become '### ...' and block elements become line breaks; everything
    else is dropped. Deliberately not BeautifulSoup — this needs one shape of
    document, and a dependency-free parser is one less thing to install on a
    512MB target.
    """

    _SKIP = {"script", "style", "nav", "header", "footer", "aside"}
    _BLOCK = {"p", "li", "div", "br", "tr", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0
        self._heading = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._heading = True
            self.out.append("\n### ")
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._heading = False
            self.out.append("\n")
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.out.append(text if self._heading else f" {text}")

    def text(self) -> str:
        joined = "".join(self.out)
        return re.sub(r"\n{3,}", "\n\n", joined)


def html_to_text(html: str) -> str:
    parser = _Extractor()
    parser.feed(html)
    return parser.text()


def pdf_to_text(data: bytes) -> str:
    """Extract text from a PDF, one page per block.

    Only used for the ISA statement. `pypdf` is pure Python and small; it is
    imported here rather than at module level so a missing install skips one
    source instead of breaking the ingestion of the other four.
    """
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def fetch(url: str, timeout: float = 60.0) -> str:
    """Download a guideline document. Returns text ready for the parsers."""
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "RecoveryLens/0.1 (research prototype)"})
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        return pdf_to_text(resp.content)

    body = resp.text
    return html_to_text(body) if "<" in body[:2000] else body


CORPUS_NOTE = [
    "RecoveryLens guidance corpus — ONE file, two provenance levels.",
    "",
    "  triggers[*].entries   HAND-VERIFIED. extraction defaults to 'curated'.",
    "                        Section numbers checked by a human. These are the",
    "                        only passages the patient-facing cards can reach.",
    "",
    "  chunks                AUTO-EXTRACTED. extraction: 'automatic'.",
    "                        Section numbers are parsed from the source document",
    "                        and never invented, but the extraction itself has",
    "                        had no human check. Clinician Q&A only.",
    "",
    "The line between them is the `extraction` field, and registry.py enforces",
    "it. It used to be enforced by the absence of a `trigger` on ingested chunks,",
    "which worked until something needed trigger to be non-null and returned a",
    "500 instead. A guarantee that lives in a missing value is not a guarantee.",
    "",
    "`citation_precision` says what the section number points AT:",
    "  'recommendation'  the exact recommendation quoted (NICE, RCP)",
    "  'section'         the section it sits in, possibly several pages (ISA,",
    "                    which numbers sections but not recommendations)",
    "",
    "EDIT `triggers` BY HAND. Do not edit `chunks` — they are regenerated by",
    "    python -m guidance.ingest",
    "which rewrites `chunks` and leaves `triggers` untouched.",
]


def write(chunks: list[dict], path: Path = OUTPUT) -> None:
    """Replace the `chunks` block, preserving hand-verified `triggers`.

    Read-modify-write rather than a plain overwrite, because this file now holds
    both the generated chunks and the 39 hand-verified entries. Clobbering it
    would silently destroy work that cannot be regenerated — the whole reason the
    two used to live in separate files. The merge is the price of one file, and
    it is cheap; losing the curated corpus to a `python -m guidance.ingest` typo
    would not be.
    """
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(
                f"{path.name} exists but could not be parsed ({type(exc).__name__}). "
                f"Refusing to overwrite it — it holds the hand-verified corpus. "
                f"Fix or restore the file first."
            )

    triggers = existing.get("triggers")
    if triggers is None:
        raise SystemExit(
            f"{path.name} has no `triggers` block. That block is hand-verified and "
            f"cannot be regenerated, so this refuses to write a file without it. "
            f"Restore it from git before running ingestion."
        )

    path.write_text(json.dumps({
        "_note": CORPUS_NOTE,
        "triggers": triggers,
        "chunks": chunks,
    }, indent=1, ensure_ascii=False))


def main() -> int:
    from .embeddings import _load_env_for_cli
    _load_env_for_cli()          # see config.py — CLIs must load .env themselves

    documents: dict[str, list[tuple[str, str]]] = {}
    failures: list[str] = []

    for source_id, spec in SOURCES.items():
        parts: list[tuple[str, str]] = []

        # A hand-downloaded copy wins over the network. Reproducible, offline, and
        # immune to a host that decides it does not like our client today.
        local_name = spec.get("local")
        local_path = LOCAL_DIR / local_name if local_name else None
        if local_path and local_path.exists():
            try:
                print(f"reading {source_id} from {local_path.name} … ",
                      end="", flush=True)
                text = (pdf_to_text(local_path.read_bytes())
                        if local_path.suffix.lower() == ".pdf"
                        else local_path.read_text(encoding="utf-8"))
                print(f"{len(text):,} chars")
                documents[source_id] = [(spec["urls"][0], text)]
                continue
            except ImportError as exc:
                print(f"SKIPPED ({exc})")
                failures.append(f"{source_id}: {exc}")
                continue
            except Exception as exc:
                print(f"FAILED ({type(exc).__name__}: {exc})")
                failures.append(f"{source_id}: {type(exc).__name__}")
                continue

        for url in spec["urls"]:
            try:
                label = source_id if len(spec["urls"]) == 1 else \
                    f"{source_id} {url.rstrip('/').rsplit('/', 1)[-1][:34]}"
                print(f"fetching {label} … ", end="", flush=True)
                parts.append((url, fetch(url)))
                print(f"{len(parts[-1][1]):,} chars")
            except ImportError as exc:
                # A missing optional extractor skips one source; it must not cost
                # the others. ISA needs pypdf and the rest do not.
                print(f"SKIPPED ({exc})")
                failures.append(f"{source_id}: {exc}")
            except Exception as exc:
                print(f"FAILED ({type(exc).__name__}: {exc})")
                failures.append(f"{source_id}: {type(exc).__name__}")
                if local_name:
                    print(f"         → download it once and save as "
                          f"guidance/sources/{local_name}:")
                    print(f"           {url}")
        if parts:
            documents[source_id] = parts

    if not documents:
        print("\nNothing fetched. Corpus unchanged.", file=sys.stderr)
        return 1

    chunks, reports = ingest(documents)
    print()
    for r in reports:
        coarse = f", {r.coarse} section-level" if r.coarse else ""
        print(f"  {r.source_id:14s} {r.chunks:4d} chunks across "
              f"{len(r.sections)} sections{coarse}")
        if r.sample:
            print(f"                 e.g. {r.sample}…")
        if r.chunks == 0:
            print(f"                 ⚠ parsed nothing — check the page structure")

    if not chunks:
        print("\nParsed 0 chunks — the page structure has probably changed. "
              "Corpus left as it was.", file=sys.stderr)
        return 1

    # Refuse a run that lost a source which previously contributed. Without this
    # a site redesign quietly halves the corpus and the only symptom is worse
    # answers, which is indistinguishable from the retriever being bad.
    previous = _previous_counts()
    lost = [sid for sid, was in previous.items()
            if was > 0 and sum(r.chunks for r in reports if r.source_id == sid) == 0]
    if lost:
        print(f"\nRefusing to write: {', '.join(lost)} previously contributed "
              f"chunks and now contributes none. Corpus left as it was.",
              file=sys.stderr)
        return 1

    write(chunks)
    total_coarse = sum(r.coarse for r in reports)
    print(f"\nWrote {OUTPUT.name} — {len(chunks):,} auto-extracted chunks "
          f"({total_coarse} section-level), hand-verified triggers preserved.")
    print("Chunks feed clinician Q&A only. Patient-facing cards still read the "
          "hand-verified entries under `triggers`.")
    if failures:
        print(f"\nIncomplete: {'; '.join(failures)}")
    print("\nNext: python -m guidance.embeddings   (re-embed the new chunks)")
    print("Then: python -m guidance.tune_floor   (EMBED_FLOOR is stale — a "
          "bigger corpus raises every score, including out-of-scope ones)")
    return 0


def _previous_counts() -> dict[str, int]:
    """How many chunks each source contributed last time, from the corpus file."""
    if not OUTPUT.exists():
        return {}
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except Exception:
        return {}
    counts: dict[str, int] = {}
    for c in data.get("chunks", []):
        counts[c.get("source_id", "")] = counts.get(c.get("source_id", ""), 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())

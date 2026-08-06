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

Two tiers, deliberately
-----------------------
    corpus.json       hand-verified. Section numbers checked by a human. Drives
                      the patient-facing trigger cards, where a wrong citation
                      is worst.
    corpus_full.json  auto-extracted. Hundreds of chunks. Drives clinician Q&A,
                      where coverage matters more and a trained reader can spot
                      an off-target result.

Every ingested chunk is marked `extraction: "automatic"` so nothing can pass
itself off as hand-verified. The two tiers are never merged.

Section numbers
---------------
NICE numbers every recommendation (1.8.2, 1.13.10). The parser preserves those
verbatim rather than inventing them, which is what makes an auto-extracted chunk
citable at all. A chunk whose number cannot be parsed is dropped rather than
guessed at — see `_flush`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import sys

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "corpus_full.json"

# Chunks shorter than this are fragments — list bullets, cross-references — and
# retrieve badly. Longer than the cap, they cover too many topics to rank well.
MIN_WORDS = 8
MAX_WORDS = 120

# "1.8.2." or "1.8.2" alone on a line begins a recommendation.
_REC_NUMBER = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\.?\s*$")
# "### 1.8. Vision" — the section heading these recommendations sit under.
_HEADING = re.compile(r"^#{2,4}\s*(\d+\.\d+)\.?\s*(.+?)\s*$")
# NICE tags each recommendation with the year it was last reviewed.
_YEAR_TAG = re.compile(r"\*\*\[([\d,\s a-z]+)\]\*\*", re.IGNORECASE)
# Markdown links: keep the text, drop the URL.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


@dataclass
class Chunk:
    source_id: str
    section: str
    heading: str
    text: str
    year_tag: str = ""
    extraction: str = "automatic"

    @property
    def id(self) -> str:
        return f"{self.source_id}_{self.section.replace('.', '_')}"

    def to_json(self) -> dict:
        return {
            "id": self.id, "source_id": self.source_id, "section": self.section,
            "heading": self.heading, "excerpt": self.text,
            "year_tag": self.year_tag, "extraction": self.extraction,
        }


def clean(line: str) -> str:
    line = _MD_LINK.sub(r"\1", line)
    line = _YEAR_TAG.sub("", line)
    line = line.replace("**", "").replace("’", "'")
    return re.sub(r"\s+", " ", line).strip()


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


def ingest(documents: dict[str, str]) -> tuple[list[dict], list[IngestReport]]:
    """documents: {source_id: raw text}. Returns (chunks, per-source reports)."""
    all_chunks: list[dict] = []
    reports: list[IngestReport] = []

    for source_id, text in documents.items():
        parsed = parse(text, source_id)
        all_chunks.extend(c.to_json() for c in parsed)
        reports.append(IngestReport(
            source_id=source_id, chunks=len(parsed),
            sections=sorted({c.section.rsplit(".", 1)[0] for c in parsed}),
            sample=parsed[0].text[:100] if parsed else "",
        ))

    return all_chunks, reports


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
# NICE's own pages are client-rendered and return an empty shell to a plain HTTP
# fetch. The NCBI Bookshelf mirrors carry the same text as static HTML, which is
# why they are used here. CG76 is fetchable directly from nice.org.uk.
SOURCES = {
    "nice_ng236": "https://www.ncbi.nlm.nih.gov/books/NBK598564/",
    "nice_ng128": "https://www.ncbi.nlm.nih.gov/books/NBK542436/",
    "nice_cg76": "https://www.nice.org.uk/guidance/cg76/chapter/Recommendations",
}


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


def fetch(url: str, timeout: float = 30.0) -> str:
    """Download a guideline page. Returns structured text ready for `parse()`."""
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "RecoveryLens/0.1 (research prototype)"})
    resp.raise_for_status()
    body = resp.text
    return html_to_text(body) if "<" in body[:2000] else body


def write(chunks: list[dict], path: Path = OUTPUT) -> None:
    path.write_text(json.dumps({
        "_note": [
            "AUTO-EXTRACTED. Not hand-verified.",
            "",
            "Section numbers are parsed from the source document, never invented,",
            "but the extraction itself has had no human check. Used for clinician",
            "Q&A retrieval, where coverage matters and a trained reader can spot",
            "an off-target result.",
            "",
            "Patient-facing guidance comes from corpus.json, which is hand-verified.",
            "The two are never merged. Regenerate with: python -m guidance.ingest",
        ],
        "chunks": chunks,
    }, indent=1))


def main() -> int:
    from .embeddings import _load_env_for_cli
    _load_env_for_cli()          # see config.py — CLIs must load .env themselves

    documents: dict[str, str] = {}
    for source_id, url in SOURCES.items():
        try:
            print(f"fetching {source_id} … ", end="", flush=True)
            documents[source_id] = fetch(url)
            print(f"{len(documents[source_id]):,} chars")
        except Exception as exc:
            # One unreachable source must not lose the others.
            print(f"FAILED ({type(exc).__name__}: {exc})")

    if not documents:
        print("\nNothing fetched. Corpus unchanged.", file=sys.stderr)
        return 1

    chunks, reports = ingest(documents)
    print()
    for r in reports:
        print(f"  {r.source_id:14s} {r.chunks:4d} chunks across "
              f"{len(r.sections)} sections")
        if r.sample:
            print(f"                 e.g. {r.sample}…")

    if not chunks:
        print("\nParsed 0 chunks — the page structure has probably changed. "
              "Corpus left as it was.", file=sys.stderr)
        return 1

    write(chunks)
    print(f"\nWrote {OUTPUT} — {len(chunks):,} chunks")
    print("These feed clinician Q&A only. Patient-facing guidance still comes "
          "from the hand-verified corpus.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

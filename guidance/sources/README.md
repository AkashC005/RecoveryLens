# Hand-downloaded guideline documents

Some publishers serve their PDFs to a browser and refuse a programmatic request.
`stroke-india.org` returns 403 for the ISA consensus statement.

Rather than disguise the request, `guidance/ingest.py` looks here first. Download
the file once, save it with the exact name below, and ingestion becomes
reproducible and offline — which is better than a hotlink regardless, since a URL
that works today is not a source you control.

| Save as         | Download from |
|-----------------|---------------|
| `isa_2024.pdf`  | <https://stroke-india.org/wp-content/uploads/2024/07/stroke.pdf> |

The documents themselves are gitignored. They are third-party publications, and
committing them would be redistribution — the thing the licensing posture in
`docs/GUIDANCE_LICENSING.md` exists to avoid. Only this README is tracked.

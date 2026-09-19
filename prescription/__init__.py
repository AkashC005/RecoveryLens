"""Prescription capture: read the medicine table, then remind the carer.

`parse.py`      deterministic geometric parse of a PDF with a text layer —
                the preferred path, reproducible, no model involved
`transcribe.py` model-assisted read of a photograph, explicitly lower
                confidence and flagged as such wherever it surfaces

Neither path writes anything a carer sees until a clinician has confirmed it.
"""

from .parse import (  # noqa: F401
    MAX_COURSE_DAYS, Medication, Prescription, SLOTS, parse_pdf, parse_words)
from .transcribe import (  # noqa: F401
    ALLOWED_TYPES, MAX_IMAGE_BYTES, transcribe_image, transcription_enabled)

__all__ = ["Medication", "Prescription", "parse_pdf", "parse_words",
           "transcribe_image", "transcription_enabled",
           "SLOTS", "MAX_COURSE_DAYS", "ALLOWED_TYPES", "MAX_IMAGE_BYTES"]

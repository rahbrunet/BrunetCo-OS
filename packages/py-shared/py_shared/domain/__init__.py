"""Domain layer (WP 0.8): canonical Family Record schema + FamilyRecordStore abstraction.

WP 1.1 adds the Appendix A reference grammar (`references`).
"""

from py_shared.domain.records import CountryRecord, FamilyRecord
from py_shared.domain.references import (
    family_display_reference,
    family_reference,
    generate_family_reference,
    generate_matter_reference,
    matter_reference,
    next_segment,
)
from py_shared.domain.store import FamilyRecordStore, PostgresFamilyRecordStore

__all__ = [
    "CountryRecord",
    "FamilyRecord",
    "FamilyRecordStore",
    "PostgresFamilyRecordStore",
    "family_display_reference",
    "family_reference",
    "generate_family_reference",
    "generate_matter_reference",
    "matter_reference",
    "next_segment",
]

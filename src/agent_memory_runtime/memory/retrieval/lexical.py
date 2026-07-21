from __future__ import annotations

import re
import unicodedata

_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_CJK_SEQUENCE_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)


def lexical_tokens(text: str) -> set[str]:
    """Return deterministic Latin words and CJK character n-grams.

    Character bi-grams give useful Chinese/Japanese/Korean lexical recall without an
    external segmenter. Single-character sequences are retained, while longer text
    uses bi-grams and a bounded full-sequence token for exact phrase matches.
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = {item for item in _LATIN_TOKEN_RE.findall(normalized)}
    for match in _CJK_SEQUENCE_RE.finditer(normalized):
        sequence = match.group(0)
        if len(sequence) == 1:
            tokens.add(sequence)
            continue
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
        if len(sequence) <= 12:
            tokens.add(sequence)
    return tokens


def searchable_record_text(record: object) -> str:
    metadata = getattr(record, "metadata", {})
    metadata_values = " ".join(str(item) for item in metadata.values())
    return " ".join(
        [
            str(getattr(record, "memory_id", "")),
            str(getattr(record, "memory_type", "")),
            str(getattr(record, "scope", "")),
            str(getattr(record, "layer", "")),
            str(getattr(record, "subject_id", "")),
            str(getattr(record, "content", "")),
            *(str(item) for item in getattr(record, "tags", ())),
            *(str(item) for item in getattr(record, "source_event_ids", ())),
            *(str(item) for item in getattr(record, "source_memory_ids", ())),
            metadata_values,
        ]
    )

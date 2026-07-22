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

    return set(lexical_token_sequence(text))


def lexical_token_sequence(text: str) -> tuple[str, ...]:
    """Return an ordered token stream suitable for FTS5 indexing.

    Unlike ``lexical_tokens``, duplicates are retained so FTS5 can account for
    term frequency when calculating BM25.
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = list(_LATIN_TOKEN_RE.findall(normalized))
    for match in _CJK_SEQUENCE_RE.finditer(normalized):
        sequence = match.group(0)
        if len(sequence) == 1:
            tokens.append(sequence)
            continue
        tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
        if len(sequence) <= 12:
            tokens.append(sequence)
    return tuple(tokens)


def fts_document_text(record: object) -> str:
    return " ".join(lexical_token_sequence(searchable_record_text(record)))


def fts_match_query(text: str) -> str:
    """Build a safe OR query from the restricted lexical token alphabet."""

    return " OR ".join(f'"{token}"' for token in sorted(lexical_tokens(text)))


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

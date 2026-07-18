from __future__ import annotations


def context_contains(context: str, required_terms: list[str]) -> bool:
    lowered = context.casefold()
    return all(term.casefold() in lowered for term in required_terms)


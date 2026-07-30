# Retrieval

The retrieval module selects memory records for a query under identity,
session, layer, type, scope, and token constraints.

## Module Roles

- `QueryRouter`: classifies queries into lexical-heavy, vector-heavy, hybrid,
  state-aware, temporal-aware, or strict no-answer modes.
- `StoreLexicalRetriever`: uses SQLite FTS5 and structured filters for keyword
  candidates.
- `SemanticRetriever`: uses an embedding provider and vector index for semantic
  candidates.
- `QdrantVectorIndex`: default vector projection with payload filters before
  vector top-k.
- `HybridCandidateRetriever`: runs retrieval legs, applies route-specific
  weights, and fuses candidates with RRF.
- `RetrievalPipeline`: applies hard filters, scoring, deterministic rerank,
  final filters, access checks, and candidate budget selection.

## Retrieval Inputs

- `MemoryQuery`: query text and identity constraints.
- `RuntimeConfig.hybrid_retrieval`: candidate limits, timeouts, weights, and
  semantic provider controls.
- `RuntimeConfig.query_router`: route-specific retrieval weights and limits.
- `RuntimeConfig.deterministic_rerank`: state, time, entity, and no-answer
  filtering controls.

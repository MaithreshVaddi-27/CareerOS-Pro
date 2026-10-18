"""Turning stored job rows into a ranked, explained answer for a candidate.

`services.sourcing` decides which jobs exist; this package decides which of them
are worth showing and why. Filtering narrows them, `vector_store`/`embeddings`
supply semantic recall, `reranker` scores, and `model_router` writes the grounded
per-job explanation.

Nothing in this package may fetch a job from an external source — that is
`services.sourcing`'s job. Keeping the boundary here is what stops the graph from
re-acquiring the same posting on every request.
"""
"""Used interface of the pinned, untyped rank-bm25 0.2.2 dependency."""

from collections.abc import Iterable

class BM25Plus:
    def __init__(self, corpus: list[list[str]]) -> None: ...
    def get_scores(self, query: list[str]) -> Iterable[float]: ...

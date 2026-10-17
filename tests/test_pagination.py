"""Pagination bounds.

`limit`, `offset` and `top_k` are the only things standing between a request and
an unbounded database pull, so their bounds are a security control, not a nicety.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas import MatchRequest, SearchRequest


class TestSearchRequestPagination:
    def test_defaults_are_conservative(self):
        req = SearchRequest()
        assert (req.limit, req.offset) == (50, 0)

    @pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": 201}, {"limit": -1}, {"offset": -1}])
    def test_out_of_range_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            SearchRequest(**bad)

    @pytest.mark.parametrize("ok", [{"limit": 1}, {"limit": 200}, {"offset": 10_000}])
    def test_edges_are_accepted(self, ok):
        assert SearchRequest(**ok)


class TestMatchRequestPagination:
    def test_defaults_are_conservative(self):
        req = MatchRequest(query="python")
        assert (req.top_k, req.offset) == (10, 0)

    @pytest.mark.parametrize("bad", [{"top_k": 0}, {"top_k": 101}, {"top_k": -5}, {"offset": -1}])
    def test_out_of_range_is_rejected(self, bad):
        """top_k used to be unbounded: a negative value reached SQL as `LIMIT -n`
        and a huge one pulled an unbounded number of rows."""
        with pytest.raises(ValidationError):
            MatchRequest(query="python", **bad)

    def test_oversample_multiplier_stays_positive(self):
        """The candidate query multiplies top_k; every accepted value must yield a
        positive LIMIT."""
        for k in (1, 2, 100):
            assert MatchRequest(query="python", top_k=k).top_k * 10 > 0
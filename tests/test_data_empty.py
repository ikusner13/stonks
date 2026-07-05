from app.data import _is_empty
from app.schemas import Fundamentals


def test_exchange_only_fundamentals_still_count_as_empty():
    assert _is_empty(Fundamentals(exchange="PNK"))

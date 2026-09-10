from execution.fill_confirmation import confirm_fill


class _FakeOrderClient:
    def __init__(self, order_book_sequence: list[list[dict]]):
        self._sequence = order_book_sequence
        self.calls = 0

    def get_order_book(self):
        book = self._sequence[min(self.calls, len(self._sequence) - 1)]
        self.calls += 1
        return book


def _no_sleep(_seconds):
    pass


def test_immediately_complete_returns_fill_result_without_sleeping():
    client = _FakeOrderClient([[{"orderid": "1", "status": "complete", "filledshares": "50", "averageprice": "24500.5"}]])
    sleeps = []
    result = confirm_fill(client, "1", sleep_fn=sleeps.append)
    assert result.status == "COMPLETE"
    assert result.filled_quantity == 50
    assert result.average_price == 24500.5
    assert sleeps == []


def test_rejected_order_reports_reason():
    client = _FakeOrderClient([[{"orderid": "1", "status": "rejected", "text": "insufficient margin"}]])
    result = confirm_fill(client, "1", sleep_fn=_no_sleep)
    assert result.status == "REJECTED"
    assert result.rejection_reason == "insufficient margin"
    assert result.filled_quantity == 0


def test_cancelled_order_is_terminal():
    client = _FakeOrderClient([[{"orderid": "1", "status": "cancelled"}]])
    result = confirm_fill(client, "1", sleep_fn=_no_sleep)
    assert result.status == "CANCELLED"


def test_polls_until_order_reaches_terminal_status():
    client = _FakeOrderClient([
        [{"orderid": "1", "status": "open"}],
        [{"orderid": "1", "status": "open"}],
        [{"orderid": "1", "status": "complete", "filledshares": "50", "averageprice": "100.0"}],
    ])
    sleeps = []
    result = confirm_fill(client, "1", poll_interval_seconds=0.01, max_wait_seconds=10, sleep_fn=sleeps.append)
    assert result.status == "COMPLETE"
    assert len(sleeps) == 2  # slept twice before the third (terminal) poll


def test_order_not_found_at_all_eventually_returns_pending_not_an_error():
    client = _FakeOrderClient([[]])
    result = confirm_fill(client, "does-not-exist", poll_interval_seconds=0.01, max_wait_seconds=0.03, sleep_fn=_no_sleep)
    assert result.status == "PENDING"
    assert result.average_price is None


def test_never_terminal_within_wait_budget_returns_pending():
    client = _FakeOrderClient([[{"orderid": "1", "status": "open"}]])
    result = confirm_fill(client, "1", poll_interval_seconds=0.01, max_wait_seconds=0.03, sleep_fn=_no_sleep)
    assert result.status == "PENDING"

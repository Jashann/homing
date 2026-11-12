import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_paypal import FakePayPal  # noqa: E402
from homing.paypal import PayPalClient  # noqa: E402
from homing.settlement import Settlement  # noqa: E402


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def client(fake):
    c = PayPalClient("test-id", "test-secret", transport=fake.transport())
    yield c
    c.close()


@pytest.fixture
def settlement(client, tmp_path):
    s = Settlement(client, tmp_path / "settlement.sqlite", holder_email="holder@example.com")
    yield s
    s.close()

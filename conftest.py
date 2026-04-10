from typing import TYPE_CHECKING

import pytest
from zeal import zeal_context

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def use_zeal(request: pytest.FixtureRequest) -> Generator[None]:
    """Enable Zeal N+1 detection context for each pytest test.

    Use @pytest.mark.no_zeal for tests that intentionally exercise import paths
    where Zeal's strict get() heuristics are too noisy.

    Yields:
        None: Control back to pytest for test execution.
    """
    if request.node.get_closest_marker("no_zeal") is not None:
        yield
        return

    with zeal_context():
        yield

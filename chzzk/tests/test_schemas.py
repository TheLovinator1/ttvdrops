import json
from pathlib import Path

import pydantic
import pytest

from chzzk.schemas import ChzzkApiResponseV2

TESTS_DIR = Path(__file__).parent


@pytest.mark.parametrize(
    ("fname", "data_model"),
    [
        ("v2_905.json", ChzzkApiResponseV2),
    ],
)
def test_chzzk_schema_strict(fname: str, data_model: type) -> None:
    """Test that the schema strictly validates the given JSON file against the provided Pydantic model."""
    with Path(TESTS_DIR / fname).open(encoding="utf-8") as f:
        data = json.load(f)
    # Should not raise
    data_model.model_validate(data)


@pytest.mark.parametrize(
    ("fname", "data_model"),
    [
        ("v1_905.json", ChzzkApiResponseV2),
    ],
)
def test_chzzk_schema_cross_fail(fname: str, data_model: type) -> None:
    """Test that the schema fails when validating the wrong JSON file/model combination."""
    with Path(TESTS_DIR / fname).open(encoding="utf-8") as f:
        data = json.load(f)
    with pytest.raises(pydantic.ValidationError):
        data_model.model_validate(data)

import pytest

from update_core.errors import VersionError
from update_core.versioning import is_newer, normalize_version, parse_version


def test_versions_are_numeric() -> None:
    assert parse_version("v1.2.10") == (1, 2, 10)
    assert is_newer("1.2.10", "1.2.9")
    assert normalize_version("v01.02.003") == "1.2.3"


@pytest.mark.parametrize("value", ["1.2", "1.2.3.4", "1.2.x", "", "-1.2.3"])
def test_invalid_versions_are_rejected(value: str) -> None:
    with pytest.raises(VersionError):
        parse_version(value)

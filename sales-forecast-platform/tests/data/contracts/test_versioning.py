import pytest

from src.data.contracts.versioning import (
    ContractVersion,
    VersionChange,
    classify_version_change,
)


def test_contract_version_parses_legacy_two_part_format():
    version = ContractVersion.parse("1.0")

    assert version.major == 1
    assert version.minor == 0
    assert version.patch == 0
    assert version.normalized() == "1.0.0"


def test_contract_version_parses_full_semver_format():
    version = ContractVersion.parse("2.3.4")

    assert version.normalized() == "2.3.4"


def test_contract_version_rejects_invalid_format():
    with pytest.raises(ValueError):
        ContractVersion.parse("v1")


def test_version_change_classification():
    assert classify_version_change(
        ContractVersion.parse("1.0.0"),
        ContractVersion.parse("1.0.1"),
    ) == VersionChange.PATCH

    assert classify_version_change(
        ContractVersion.parse("1.0.0"),
        ContractVersion.parse("1.1.0"),
    ) == VersionChange.MINOR

    assert classify_version_change(
        ContractVersion.parse("1.1.0"),
        ContractVersion.parse("2.0.0"),
    ) == VersionChange.MAJOR

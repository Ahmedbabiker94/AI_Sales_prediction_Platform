from dataclasses import dataclass
from enum import Enum
import re


class VersionChange(str, Enum):
    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"


@dataclass(frozen=True, order=True)
class ContractVersion:
    major: int
    minor: int
    patch: int = 0

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in (self.major, self.minor, self.patch)
        ):
            raise ValueError(
                "Version components must be non-negative integers."
            )

    @classmethod
    def parse(cls, value: str) -> "ContractVersion":
        if not isinstance(value, str):
            raise ValueError("Version must be a string.")

        pattern = r"(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?"

        match = re.fullmatch(pattern, value)

        if match is None:
            raise ValueError(f"Invalid contract version: {value!r}")

        major, minor, patch = match.groups()

        return cls(
            major=int(major),
            minor=int(minor),
            patch=int(patch) if patch is not None else 0,
        )

    def normalized(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def classify_version_change(
    old: ContractVersion,
    new: ContractVersion,
) -> VersionChange:
    if new < old:
        raise ValueError(
            f"Version downgrade is not allowed: "
            f"{old.normalized()} -> {new.normalized()}"
        )

    if new.major > old.major:
        return VersionChange.MAJOR

    if new.minor > old.minor:
        return VersionChange.MINOR

    if new.patch > old.patch:
        return VersionChange.PATCH

    return VersionChange.NONE
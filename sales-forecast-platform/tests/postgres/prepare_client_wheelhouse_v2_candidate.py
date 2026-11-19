"""Create an independent pytest 9.0.3 remediation candidate from verified v1.

Opt-in PyPI download of exactly one new wheel. The source is strictly
read-only. Destination MUST be absent and created privately; no Docker,
PostgreSQL, runtime harness, or accepted Dockerfile changes. A failure keeps
INCOMPLETE_DO_NOT_USE. This does NOT approve the new dependencies or image.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

if __package__:
    from .prepare_client_wheelhouse_candidate import (
        TOP_LEVEL, inspect_wheels, candidate_lock, ORIGINAL_WHEEL, ORIGINAL_HASH,
    )
    from .verify_client_wheelhouse_candidate import verify_candidate_wheelhouse
else:
    from prepare_client_wheelhouse_candidate import (
        TOP_LEVEL, inspect_wheels, candidate_lock, ORIGINAL_WHEEL, ORIGINAL_HASH,
    )
    from verify_client_wheelhouse_candidate import verify_candidate_wheelhouse

PATCHED_PYTEST = "9.0.3"
OLD_PYTEST = "8.4.2"
V2_TOP_LEVEL = tuple(
    "pytest==" + PATCHED_PYTEST if part == "pytest==" + OLD_PYTEST else part
    for part in TOP_LEVEL
)


def prepare_v2(source, destination, *, downloader=subprocess.run):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source == destination or destination.exists() or destination.is_symlink():
        raise ValueError("destination must be an absent, independent path")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source must be an existing nonsymlink directory")
    # BEFORE any destination mutation, verify complete source integrity.
    verify_candidate_wheelhouse(source)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("destination parent must exist and not be a symlink")
    destination.mkdir(mode=0o700, exist_ok=False)
    sentinel = destination / "INCOMPLETE_DO_NOT_USE"
    sentinel.write_text("New candidate not yet verified; do not use.\n", encoding="utf-8")
    try:
        manifest = json.loads((source / "candidate-manifest.json").read_text(encoding="utf-8"))
        for row in manifest["wheels"]:
            if row["name"] != "pytest":
                shutil.copyfile(source / row["filename"], destination / row["filename"])
        cmd = [sys.executable, "-m", "pip", "download",
               "--index-url", "https://pypi.org/simple",
               "--only-binary=:all:", "--no-deps", "--no-cache-dir",
               "--disable-pip-version-check", "--dest", str(destination),
               "pytest==" + PATCHED_PYTEST]
        downloader(cmd, check=True, timeout=300)
        # Exclude only the sentinel, which is never a wheel.
        files = sorted(p for p in destination.iterdir() if p.name != sentinel.name)
        if len(files) != 16 or any(not f.is_file() or f.is_symlink()
                                    or f.suffix != ".whl" for f in files):
            raise ValueError("v2 must contain exactly 16 regular wheel files")
        # Wheel inspector requires a wheels-only directory; sentinel is
        # removed only once the download and file-count checks succeed.
        sentinel.unlink()
        entries = inspect_wheels(destination)
        actual = {x["name"]: x for x in entries}
        if len(entries) != 16 or actual.get("pytest", {}).get("version") != PATCHED_PYTEST:
            raise ValueError("patched pytest wheel absent or wrong version")
        expected = {x["name"]: x for x in manifest["wheels"] if x["name"] != "pytest"}
        for name, old in expected.items():
            if actual.get(name) != old:
                raise ValueError("retained wheel changed")
        if (actual["psycopg2-binary"]["filename"] != ORIGINAL_WHEEL
                or actual["psycopg2-binary"]["sha256"] != ORIGINAL_HASH):
            raise ValueError("approved psycopg2 wheel changed")
        for spec in V2_TOP_LEVEL:
            name, version = spec.split("==", 1)
            if actual.get(name, {}).get("version") != version:
                raise ValueError("v2 top-level requirement mismatch")
        (destination / "candidate-requirements.lock").write_text(
            candidate_lock(entries), encoding="utf-8")
        (destination / "candidate-manifest.json").write_text(
            json.dumps({"status": "CANDIDATE_ONLY_NOT_APPROVED",
                        "python": "3.11", "platform": "linux/amd64",
                        "wheels": entries}, indent=2), encoding="utf-8")
    except BaseException:
        sentinel.write_text("Candidate v2 failed; do not use.\n", encoding="utf-8")
        raise
    return {"status": "CANDIDATE_ONLY_NOT_APPROVED",
            "wheel_count": 16,
            "pytest_version": PATCHED_PYTEST,
            "destination": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--new-directory", type=Path, required=True)
    parser.add_argument("--allow-pypi-download", action="store_true")
    args = parser.parse_args()
    if not args.allow_pypi_download:
        parser.error("explicit PyPI download authorization required")
    if sys.version_info[:2] != (3, 11) or sys.platform != "linux":
        parser.error("native Linux Python 3.11 required")
    print(json.dumps(prepare_v2(args.source, args.new_directory), indent=2))


if __name__ == "__main__":
    main()

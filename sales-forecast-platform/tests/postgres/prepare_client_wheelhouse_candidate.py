"""Build ONLY a proposed offline wheelhouse/lock in a NEW directory.

Does not alter the accepted tests/postgres/requirements.lock or Dockerfile.
Run in a trusted Python 3.11 Linux/amd64 staging environment with explicitly
authorized network access to PyPI. This is an artifact-preparation step, not
a PostgreSQL run or supply-chain approval. No containers are launched.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import zipfile
from email.parser import Parser

# Top-level versions match the existing focused CI; transitives MUST be
# resolved and independently reviewed, never assumed present in a base image.
TOP_LEVEL = (
    "pytest==8.4.2", "pydantic==2.13.3", "pandas==2.3.3",
    "numpy==2.4.4", "psycopg2-binary==2.9.12",
)
ORIGINAL_WHEEL = "psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
ORIGINAL_HASH = "e4e184b1fb6072bf05388aa41c697e1b2d01b3473f107e7ec44f186a32cfd0b8"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9_.+!-]+$")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_wheels(directory):
    """Return full, unique, hashed distribution inventory; reject ambiguity."""
    if not directory.is_dir():
        raise ValueError("candidate wheelhouse missing")
    wheels = sorted(directory.iterdir())
    if not wheels or any(p.is_symlink() or not p.is_file() or p.suffix != ".whl" for p in wheels):
        raise ValueError("only regular .whl files are allowed")
    entries = []
    seen = set()
    for wheel in wheels:
        if not wheel.name.endswith(".whl"):
            raise ValueError("wheel filename missing")
        with zipfile.ZipFile(wheel) as archive:
            metadata_paths = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(metadata_paths) != 1:
                raise ValueError("ambiguous wheel metadata")
            meta = Parser().parsestr(archive.read(metadata_paths[0]).decode("utf-8"))
            name, version = meta.get("Name"), meta.get("Version")
            if not name or not version or not NAME_RE.fullmatch(name) or not VERSION_RE.fullmatch(version):
                raise ValueError("invalid wheel metadata")
            key = re.sub(r"[-_.]+", "-", name).lower()
            if key in seen:
                raise ValueError("duplicate distribution")
            seen.add(key)
            # Reject wheel metadata that contradicts its filename's
            # distribution/version prefix (PEP 427 normalization).
            parts = wheel.name[:-4].split("-")
            if len(parts) < 5 or re.sub(r"[-_.]+", "-", parts[0]).lower() != key or parts[1] != version.replace("-", "_"):
                raise ValueError("wheel filename/metadata mismatch")
            entries.append({"name": key, "version": version,
                            "filename": wheel.name, "sha256": sha256(wheel)})
    return entries


def validate_inventory(entries):
    actual = {x["name"]: x for x in entries}
    required = dict(part.split("==", 1) for part in TOP_LEVEL)
    for name, version in required.items():
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key not in actual or actual[key]["version"] != version:
            raise ValueError("top-level requirement unresolved")
    selected = actual.get("psycopg2-binary")
    if selected["filename"] != ORIGINAL_WHEEL or selected["sha256"] != ORIGINAL_HASH:
        raise ValueError("psycopg2 original approved wheel differs")
    return True


def candidate_lock(entries):
    return "".join(f'{x["name"]}=={x["version"]} --hash=sha256:{x["sha256"]}\\n'
                   for x in sorted(entries, key=lambda row: row["name"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-directory", type=Path, required=True)
    parser.add_argument("--verified-psycopg-wheel", type=Path, required=True)
    parser.add_argument("--allow-pypi-download", action="store_true")
    args = parser.parse_args()
    if not args.allow_pypi_download:
        parser.error("explicit permission to download candidate wheels from PyPI required")
    if (sys.version_info[:2] != (3, 11) or sys.platform != "linux"
            or platform.machine().lower() not in ("x86_64", "amd64")):
        parser.error("run ONLY on native Linux/amd64 CPython 3.11 staging")
    destination = args.new_directory.absolute()
    if destination.exists() or destination.is_symlink():
        parser.error("new-directory must not already exist")
    source = args.verified_psycopg_wheel.resolve(strict=True)
    if source.is_symlink() or source.name != ORIGINAL_WHEEL or sha256(source) != ORIGINAL_HASH:
        parser.error("verified original psycopg wheel required")
    # Stage in a fresh exclusive directory, never overwrite existing evidence.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    import shutil
    shutil.copyfile(source, destination / ORIGINAL_WHEEL)
    packages = [p for p in TOP_LEVEL if not p.startswith("psycopg2-binary==")]
    cmd = [sys.executable, "-m", "pip", "download", "--index-url",
           "https://pypi.org/simple", "--only-binary=:all:", "--no-cache-dir",
           "--disable-pip-version-check", "--dest", str(destination), *packages]
    try:
        subprocess.run(cmd, check=True, timeout=300)
        entries = inspect_wheels(destination)
        validate_inventory(entries)
        # A candidate lock lists transitive versions/hashes, but review must
        # STILL verify exact dependency closure by offline pip installation.
        (destination / "candidate-requirements.lock").write_text(candidate_lock(entries), encoding="utf-8")
        (destination / "candidate-manifest.json").write_text(json.dumps(
            {"status": "CANDIDATE_ONLY_NOT_APPROVED", "python": "3.11",
             "platform": "linux/amd64", "wheels": entries}, indent=2), encoding="utf-8")
    except BaseException:
        # Preserve incomplete evidence; DO NOT claim lock validity.
        (destination / "INCOMPLETE_DO_NOT_USE").write_text(
            "Wheel acquisition/inspection failed. Do not use this directory.\\n", encoding="utf-8")
        raise
    print("CANDIDATE_ONLY: wheel hashes recorded; requires offline closure and independent review")


if __name__ == "__main__":
    main()

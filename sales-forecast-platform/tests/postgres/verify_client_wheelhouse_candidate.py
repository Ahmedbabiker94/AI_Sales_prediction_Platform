"""Read-only F-03 candidate wheelhouse/lock integrity gate.

Compares the complete manifest, hash-locked requirements, and wheel bytes.
Does not install packages, access PyPI, build images or approve rebaseline.
The optional independent offline pip resolution/build tests are separate.
"""
import hashlib
import json
from pathlib import Path
import re

# Support both package imports and direct script execution from tests/postgres.
if __package__:
    from .prepare_client_wheelhouse_candidate import (
        ORIGINAL_HASH, ORIGINAL_WHEEL, TOP_LEVEL,
    )
else:
    from prepare_client_wheelhouse_candidate import (
        ORIGINAL_HASH, ORIGINAL_WHEEL, TOP_LEVEL,
    )

LOCK_LINE = re.compile(r"([a-z0-9][a-z0-9.-]*)==([A-Za-z0-9_.+!-]+) --hash=sha256:([0-9a-f]{64})\Z")


def verify_candidate_wheelhouse(folder, *, top_level=TOP_LEVEL):
    """Reject any missing, unexpected or altered wheel, manifest or lock."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("wheelhouse directory invalid")
    expected_names = {"candidate-manifest.json", "candidate-requirements.lock"}
    manifest_file = folder / "candidate-manifest.json"
    lock_file = folder / "candidate-requirements.lock"
    if (manifest_file.is_symlink() or lock_file.is_symlink()
            or not manifest_file.is_file() or not lock_file.is_file()):
        raise ValueError("candidate metadata absent or symlinked")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"status", "python", "platform", "wheels"}:
        raise ValueError("invalid manifest fields")
    if (manifest["status"] != "CANDIDATE_ONLY_NOT_APPROVED"
            or manifest["python"] != "3.11" or manifest["platform"] != "linux/amd64"):
        raise ValueError("unexpected manifest target")
    entries = manifest["wheels"]
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("candidate must contain exactly 16 wheels")
    manifest_rows = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "version", "filename", "sha256"}:
            raise ValueError("invalid manifest wheel record")
        name, version, filename, digest = (entry[k] for k in ("name", "version", "filename", "sha256"))
        if not all(isinstance(x, str) for x in (name, version, filename, digest)):
            raise ValueError("invalid manifest record types")
        if (name in manifest_rows or not filename.endswith(".whl")
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("duplicate or invalid wheel record")
        manifest_rows[name] = (version, filename, digest)
        expected_names.add(filename)
    actual_names = {p.name for p in folder.iterdir()}
    if actual_names != expected_names or any(p.is_symlink() for p in folder.iterdir()):
        raise ValueError("wheelhouse contains missing, extra or symlinked files")
    rows = {}
    for line in lock_file.read_text(encoding="utf-8").splitlines():
        match = LOCK_LINE.fullmatch(line)
        if not match or match[1] in rows:
            raise ValueError("invalid or duplicate lock entry")
        rows[match[1]] = (match[2], match[3])
    if set(rows) != set(manifest_rows):
        raise ValueError("manifest and lock disagree")
    for name, (version, filename, digest) in manifest_rows.items():
        if rows[name] != (version, digest):
            raise ValueError("manifest and lock values disagree")
        p = folder / filename
        if not p.is_file() or p.is_symlink():
            raise ValueError("invalid wheel file")
        h = hashlib.sha256()
        with p.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            raise ValueError("wheel SHA-256 mismatch")
    # Re-inspect wheel metadata and normalized distribution names, not just
    # hashes supplied in the unapproved candidate manifest.
    actual_wheels = inspect_wheels_only(folder, expected_names - {"candidate-manifest.json", "candidate-requirements.lock"})
    if {e["name"]: (e["version"], e["filename"], e["sha256"]) for e in actual_wheels} != manifest_rows:
        raise ValueError("wheel metadata and candidate manifest disagree")
    required = dict(item.split("==", 1) for item in top_level)
    if any(name not in manifest_rows or manifest_rows[name][0] != version
           for name, version in required.items()):
        raise ValueError("top-level version mismatch")
    psycopg = manifest_rows["psycopg2-binary"]
    if psycopg[1:] != (ORIGINAL_WHEEL, ORIGINAL_HASH):
        raise ValueError("approved driver wheel differs")
    return True


def inspect_wheels_only(folder, wheel_names):
    """Reuse the original strict wheel parser without copying files."""
    # Original inspector expects a directory containing only wheels; check
    # exact filename inventory and then validate metadata in individual ZIPs.
    import zipfile
    from email.parser import Parser
    normalize = lambda n: re.sub(r"[-_.]+", "-", n).lower()
    result = []
    for filename in sorted(wheel_names):
        path = folder / filename
        parts = filename.removesuffix(".whl").split("-")
        if len(parts) < 5 or not filename.endswith(".whl"):
            raise ValueError("invalid wheel filename")
        with zipfile.ZipFile(path) as archive:
            metas = [p for p in archive.namelist() if p.endswith(".dist-info/METADATA")]
            if len(metas) != 1:
                raise ValueError("ambiguous wheel metadata")
            meta = Parser().parsestr(archive.read(metas[0]).decode("utf-8"))
            name, version = meta.get("Name"), meta.get("Version")
            if (not name or not version or normalize(parts[0]) != normalize(name)
                    or parts[1] != version.replace("-", "_")):
                raise ValueError("wheel metadata and filename mismatch")
        result.append({"name": normalize(name), "version": version,
                       "filename": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result

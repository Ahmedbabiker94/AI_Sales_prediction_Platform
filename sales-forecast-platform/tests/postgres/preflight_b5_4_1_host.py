"""Read-only host preflight for the approved disposable R7 B5-4-1 PostgreSQL tests.

Run from a trusted Linux/amd64 environment (WSL2 with Docker Desktop integration
is possible). Does NOT pull/build images, run containers, create volumes,
modify files, contact PostgreSQL, or access production credentials.

Exit code 0 only when the *local* prerequisites are present. This is NOT a
database integration test and grants NO authorization to run the harness.
"""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys

POSTGRES = "sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f"
BASE = "sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2"
WHEEL = "psycopg2_binary-2.9.12-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
WHEEL_HASH = "e4e184b1fb6072bf05388aa41c697e1b2d01b3473f107e7ec44f186a32cfd0b8"


def docker_inspect(image_id):
    try:
        result = subprocess.run(
            ["docker", "--host=unix:///var/run/docker.sock", "image", "inspect",
             image_id, "--format", "{{.Id}} {{.Os}} {{.Architecture}}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == (
        image_id + " linux amd64"
    )


def wheel_verified(folder):
    if folder is None:
        return False
    try:
        folder = folder.resolve(strict=True)
        if not folder.is_dir():
            return False
        file = folder / WHEEL
        if file.is_symlink() or not file.is_file():
            return False
        h = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest() == WHEEL_HASH
    except (OSError, ValueError):
        return False


def check_host(folder=None):
    results = {}
    results["Linux amd64"] = (
        sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")
    )
    socket = Path("/var/run/docker.sock")
    try:
        results["Unix Docker socket"] = stat.S_ISSOCK(socket.stat().st_mode)
    except OSError:
        results["Unix Docker socket"] = False
    results["Docker CLI"] = shutil.which("docker") is not None
    # The original harness forces the local Unix socket; no remote context or
    # production Docker daemon may be substituted.
    results["No Docker override variables"] = not any(
        os.environ.get(key) for key in (
            "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH"
        )
    )
    ready = all(results.values())
    results["Approved PostgreSQL image cached"] = (
        ready and docker_inspect(POSTGRES)
    )
    results["Approved client base image cached"] = (
        ready and docker_inspect(BASE)
    )
    results["Exact offline driver wheel verified"] = wheel_verified(folder)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True,
                        help="Existing trusted directory containing the exact pinned wheel")
    args = parser.parse_args()
    results = check_host(args.wheelhouse)
    for name, ok in results.items():
        print(("PASS" if ok else "BLOCKED") + ": " + name)
    print("Read-only preflight; NO PostgreSQL/Docker containers were started.")
    if not all(results.values()):
        print("NO-GO: retain R7-05B5-4-1 OPEN; do not run the database harness.")
        return 2
    print("Host prerequisites present. Independent review and explicit opt-in still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CR-1: pure review-only exact Docker run argv proposal.

No Docker calls. This is NOT wired to the approved run_postgres harness.
Only builds and compares complete argv vectors for the five known stages.
No arbitrary modules, mounts, environment keys or extra Docker options.
"""
from pathlib import PurePosixPath
from uuid import UUID
import re

from runtime_reference_candidate import PG_REF

_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CLIENT_TESTS = {
    "foundation": ("tests/postgres/test_forecast_evidence_postgres.py",),
    "restart": ("tests/postgres/test_forecast_evidence_restart.py",),
    "b5-4-1": (
        "tests/postgres/test_stored_forecast_accuracy_postgres.py",
        "tests/postgres/test_canonical_reporting_read.py",
        "tests/postgres/test_r7_05b5_4_1_host_reporting_requests.py",
    ),
}
_PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def _check_context(ctx):
    if type(ctx) is not dict or set(ctx) != {
        "run_id", "root", "evidence", "postgres_ref", "client_image",
        "admin_password",
    }:
        raise ValueError("incomplete or extra context")
    run_id = ctx["run_id"]
    if type(run_id) is not UUID or run_id.version != 4:
        raise ValueError("UUIDv4 ownership identity required")
    if ctx["postgres_ref"] != PG_REF or type(ctx["postgres_ref"]) is not str:
        raise ValueError("unverified PostgreSQL OCI reference")
    client = ctx["client_image"]
    if type(client) is not str or not _SHA.fullmatch(client):
        raise ValueError("unverified client image identity")
    for key in ("root", "evidence"):
        value = ctx[key]
        if type(value) is not str or not value.startswith("/"):
            raise ValueError("absolute Linux-only path required")
        path = PurePosixPath(value)
        if str(path) != value or ".." in path.parts or value == "/" or "\\x00" in value:
            raise ValueError("noncanonical or unsafe path")
    root = PurePosixPath(ctx["root"])
    if root.name != "sales-forecast-platform" or str(root).startswith("/mnt/c/"):
        raise ValueError("Linux-native project root required")
    evidence = PurePosixPath(ctx["evidence"])
    if evidence == root or root in evidence.parents:
        raise ValueError("credentials must be outside source tree")
    pwd = ctx["admin_password"]
    if type(pwd) is not str or not re.fullmatch(r"test-only-[0-9a-f]{40}", pwd):
        raise ValueError("disposable test credential required")
    return run_id


def build_review_argv(ctx):
    """Return five canonical *proposed* argv vectors and never execute them."""
    run_id = _check_context(ctx)
    prefix = "r7-05b3-" + run_id.hex
    label = "r7_05b3_run=" + str(run_id)
    data, socket = prefix + "-data", prefix + "-socket"
    database = "r7_05b3_" + run_id.hex
    root = ctx["root"]
    envfile = ctx["evidence"] + "/test-client.env"
    prox = [v for key in _PROXIES for v in ("-e", key + "=")]
    isolation = ["--network=none", "--read-only", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges", "--cpus=1",
                 "--pids-limit=64"]
    common = ["--pull=never", "--platform=linux/amd64"]

    provision = [
        "docker", "run", "--name", prefix + "-provision",
        "--label", label, *common,
        "--network=none", "--read-only", "--user=0:0",
        "--cap-drop=ALL", "--cap-add=CHOWN", "--security-opt=no-new-privileges",
        "--cpus=1", "--memory=64m", "--memory-swap=64m", "--pids-limit=32",
        *prox, "--mount", "type=volume,src=" + data + ",dst=/var/lib/postgresql/data",
        "--entrypoint", "chown", ctx["postgres_ref"],
        "999:999", "/var/lib/postgresql/data",
    ]
    server = [
        "docker", "run", "-d", "--name", prefix + "-server", "--label", label,
        *common, *isolation, "--user=999:999",
        "--memory=512m", "--memory-swap=512m",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=32m",
        "--mount", "type=volume,src=" + data + ",dst=/var/lib/postgresql/data",
        "--mount", "type=volume,src=" + socket + ",dst=/var/run/postgresql",
        *prox,
        "-e", "PGDATA=/var/lib/postgresql/data/pgdata",
        "-e", "POSTGRES_DB=" + database,
        "-e", "POSTGRES_USER=r7_test_owner",
        "-e", "POSTGRES_PASSWORD=" + ctx["admin_password"],
        "-e", "POSTGRES_INITDB_ARGS=--auth-local=scram-sha-256 --auth-host=scram-sha-256",
        ctx["postgres_ref"],
        "-c", "listen_addresses=", "-c", "shared_buffers=32MB",
        "-c", "max_connections=20", "-c", "fsync=on",
        "-c", "synchronous_commit=on", "-c", "full_page_writes=on",
    ]

    clients = {}
    for stage, modules in _CLIENT_TESTS.items():
        clients[stage] = [
            "docker", "run", "--name", prefix + "-" + stage,
            "--label", label, *common, *isolation,
            "--user=1000:1000", "--memory=1g", "--memory-swap=1g",
            "--ulimit", "cpu=180:180",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=256m",
            "--mount", "type=volume,src=" + socket + ",dst=/run/r7pg,readonly",
            "--mount", "type=bind,src=" + root + "/src,dst=/code/src,readonly",
            "--mount", "type=bind,src=" + root + "/tests,dst=/code/tests,readonly",
            "--workdir=/code", "--env-file", envfile, *prox,
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/code",
            "-e", "OMP_NUM_THREADS=1", "-e", "OPENBLAS_NUM_THREADS=1",
            "-e", "HOME=/tmp", "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "--entrypoint", "python", ctx["client_image"],
            "-m", "pytest", "-q", "--tb=short", "--maxfail=1",
            "-p", "no:cacheprovider", *modules,
        ]
    return {"provision": provision, "server": server, **clients}


def validate_review_argv(ctx, proposed):
    """Only exact five known stage vectors qualify for further review."""
    expected = build_review_argv(ctx)
    if type(proposed) is not dict or set(proposed) != set(expected):
        raise ValueError("incomplete or unexpected stage set")
    for stage, required in expected.items():
        argv = proposed[stage]
        if type(argv) not in (list, tuple) or list(argv) != required:
            raise ValueError("unauthorized Docker arguments at " + stage)
    return True

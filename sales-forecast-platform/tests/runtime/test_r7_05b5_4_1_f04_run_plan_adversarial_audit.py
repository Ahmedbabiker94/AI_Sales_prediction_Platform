"""F-04 independent adversarial audit of the *candidate* argv validator.

These regressions now REQUIRE rejecting previously admitted adversarial
synthetic argument vectors. Passing is still NOT real runner certification.
No subprocess, Docker, PostgreSQL, network or model is ever invoked.
"""
import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    checker = importlib.import_module("runtime_run_plan_candidate")
    pg_ref = importlib.import_module("runtime_reference_candidate").PG_REF
    client_id = "sha256:" + "a" * 64

    def cmd(name, image, user, extra=()):
        return ["docker", "run", "--name", "r7-05b3-" + "a" * 32 + "-" + name,
                "--label", "r7_05b3_run=" + "a" * 32,
                "--pull=never", "--network=none", "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                user, *extra, image]

    plan = {
        "run_hex": "a" * 32,
        "provision": cmd("provision", pg_ref, "--user=0:0", ("--cap-add=CHOWN",)),
        "server": cmd("server", pg_ref, "--user=999:999"),
        "clients": [cmd("foundation", client_id, "--user=1000:1000"),
                    cmd("restart", client_id, "--user=1000:1000"),
                    cmd("b5-4-1", client_id, "--user=1000:1000")],
    }
    return checker, pg_ref, client_id, plan


@pytest.mark.parametrize("unsafe", [
    ("--network", "bridge"),
    ("--privileged=true",),
    ("--cap-add=SYS_ADMIN",),
    ("--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock"),
    ("--user=0:0",),
])
def test_candidate_rejects_conflicting_flags(candidate, unsafe):
    """A malicious extra argv vector MUST be rejected before any execution."""
    checker, pg_ref, client_id, source = candidate
    plan = copy.deepcopy(source)
    # Insert while preserving every expected safe flag, exploiting the
    # presence-only validation. No Docker invocation occurs.
    plan["server"][-1:-1] = list(unsafe)
    with pytest.raises(ValueError):
        checker.validate_candidate_run_plan(plan, pg_ref, client_id)


@pytest.mark.parametrize("site", ["provision", "server", "client"])
def test_candidate_rejects_nonmatching_ownership_markers(candidate, site):
    checker, pg_ref, client_id, source = candidate
    plan = copy.deepcopy(source)
    argv = plan["clients"][0] if site == "client" else plan[site]
    argv[argv.index("--name") + 1] = "r7-05b3-" + "f" * 32 + "-foreign"
    argv[argv.index("--label") + 1] = "r7_05b3_run=unrelated"
    assert checker.validate_candidate_run_plan(plan, pg_ref, client_id) is True


def test_candidate_rejects_image_as_nonterminal_argument(candidate):
    checker, pg_ref, client_id, source = candidate
    plan = copy.deepcopy(source)
    # It checks one occurrence, but not its position relative to Docker flags.
    plan["server"].append("--unexpected-after-image")
    assert checker.validate_candidate_run_plan(plan, pg_ref, client_id) is True

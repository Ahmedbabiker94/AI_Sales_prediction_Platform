"""Additional candidate-only exact run-plan edge regressions, offline only."""
import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def secure_plan(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    checker = importlib.import_module("runtime_run_plan_candidate")
    pg = importlib.import_module("runtime_reference_candidate").PG_REF
    client = "sha256:" + "a" * 64
    run_hex = "a" * 32

    def command(stage, image, user, provision=False):
        return ["docker", "run", "--name", f"r7-05b3-{run_hex}-{stage}",
                "--label", f"r7_05b3_run={run_hex}",
                "--pull=never", "--network=none", "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                f"--user={user}", *(["--cap-add=CHOWN"] if provision else []),
                image]

    plan = {
        "run_hex": run_hex,
        "provision": command("provision", pg, "0:0", True),
        "server": command("server", pg, "999:999"),
        "clients": [
            command("foundation", client, "1000:1000"),
            command("restart", client, "1000:1000"),
            command("b5-4-1", client, "1000:1000"),
        ],
    }
    assert checker.validate_candidate_run_plan(plan, pg, client) is True
    return checker, pg, client, plan


@pytest.mark.parametrize("bad", ["not-hex", "A" * 32, "b" * 31, "", None])
def test_invalid_run_identity_rejected(secure_plan, bad):
    checker, pg, client, base = secure_plan
    plan = copy.deepcopy(base)
    plan["run_hex"] = bad
    with pytest.raises(ValueError, match="ownership"):
        checker.validate_candidate_run_plan(plan, pg, client)


@pytest.mark.parametrize("stage", ["provision", "server", "clients"])
def test_uuid_mismatch_at_any_stage_rejected(secure_plan, stage):
    checker, pg, client, base = secure_plan
    plan = copy.deepcopy(base)
    argv = plan["clients"][0] if stage == "clients" else plan[stage]
    argv[argv.index("--name") + 1] = argv[argv.index("--name") + 1].replace("a" * 32, "b" * 32)
    with pytest.raises(ValueError, match="forbidden"):
        checker.validate_candidate_run_plan(plan, pg, client)


def test_swapped_client_stages_rejected(secure_plan):
    checker, pg, client, base = secure_plan
    plan = copy.deepcopy(base)
    plan["clients"][0], plan["clients"][1] = plan["clients"][1], plan["clients"][0]
    with pytest.raises(ValueError, match="forbidden"):
        checker.validate_candidate_run_plan(plan, pg, client)


def test_missing_required_client_stage_rejected(secure_plan):
    checker, pg, client, base = secure_plan
    plan = copy.deepcopy(base)
    plan["clients"].pop()
    with pytest.raises(ValueError, match="incomplete client"):
        checker.validate_candidate_run_plan(plan, pg, client)


@pytest.mark.parametrize("extra", [
    ["--network", "bridge"],
    ["--user=0:0"],
    ["--cap-add=NET_ADMIN"],
    ["--mount", "type=bind,src=/home,dst=/home"],
    ["--env-file", "/tmp/production.env"],
    ["--entrypoint", "sh"],
    ["--pull=always"],
])
def test_unknown_or_duplicate_options_fail_closed(secure_plan, extra):
    checker, pg, client, base = secure_plan
    plan = copy.deepcopy(base)
    plan["clients"][-1][-1:-1] = extra
    with pytest.raises(ValueError):
        checker.validate_candidate_run_plan(plan, pg, client)

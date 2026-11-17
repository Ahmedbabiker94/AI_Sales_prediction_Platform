"""F-04 negative, offline-only contract tests for proposed run args.

Does not run Docker or update the accepted runner. A passing validation of a
fabricated plan cannot prove that actual run_postgres.py uses that plan.
"""
import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def contract(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("runtime_run_plan_candidate")


@pytest.fixture
def plan(contract):
    from runtime_reference_candidate import PG_REF
    client = "sha256:" + "a" * 64

    def cmd(name, image, user, extra=()):
        return ["docker", "run", "--name", "r7-05b3-synthetic-" + name,
                "--label", "r7_05b3_run=synthetic",
                "--pull=never", "--network=none", "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                user, *extra, image]

    return PG_REF, client, {
        "provision": cmd("provision", PG_REF, "--user=0:0", ["--cap-add=CHOWN"]),
        "server": cmd("server", PG_REF, "--user=999:999"),
        "clients": [cmd("foundation", client, "--user=1000:1000"),
                    cmd("restart", client, "--user=1000:1000"),
                    cmd("b5-4-1", client, "--user=1000:1000")],
    }


def test_complete_candidate_run_plan_is_accepted(contract, plan):
    pg, client, args = plan
    assert contract.validate_candidate_run_plan(args, pg, client) is True


@pytest.mark.parametrize("site", ["provision", "server", "clients"])
def test_missing_explicit_no_pull_rejected_at_every_run_site(contract, plan, site):
    pg, client, args = copy.deepcopy(plan)
    cmd = args["clients"][0] if site == "clients" else args[site]
    cmd.remove("--pull=never")
    with pytest.raises(ValueError, match="required isolation"):
        contract.validate_candidate_run_plan(args, pg, client)


@pytest.mark.parametrize("site", ["provision", "server", "clients"])
def test_mutable_or_wrong_image_rejected_at_every_run_site(contract, plan, site):
    pg, client, args = copy.deepcopy(plan)
    cmd = args["clients"][1] if site == "clients" else args[site]
    cmd[-1] = "postgres:16.15" if site != "clients" else "python:3.11"
    with pytest.raises(ValueError, match="wrong image"):
        contract.validate_candidate_run_plan(args, pg, client)


@pytest.mark.parametrize("bad", ["--pull=always", "--pull=missing", "--privileged",
                                  "--publish=5432:5432", "--network=host"])
def test_forbidden_option_rejected(contract, plan, bad):
    pg, client, args = copy.deepcopy(plan)
    args["server"].insert(-1, bad)
    with pytest.raises(ValueError, match="forbidden"):
        contract.validate_candidate_run_plan(args, pg, client)


@pytest.mark.parametrize("option", ["--network=none", "--read-only",
                                    "--cap-drop=ALL", "--security-opt=no-new-privileges"])
def test_client_isolation_cannot_be_removed(contract, plan, option):
    pg, client, args = copy.deepcopy(plan)
    args["clients"][-1].remove(option)
    with pytest.raises(ValueError, match="required isolation"):
        contract.validate_candidate_run_plan(args, pg, client)


@pytest.mark.parametrize("site,option", [
    ("provision", "--cap-add=CHOWN"),
    ("provision", "--user=0:0"),
    ("server", "--user=999:999"),
    ("clients", "--user=1000:1000"),
])
def test_required_user_and_scoped_provisioning_options(contract, plan, site, option):
    pg, client, args = copy.deepcopy(plan)
    cmd = args["clients"][0] if site == "clients" else args[site]
    cmd.remove(option)
    with pytest.raises(ValueError):
        contract.validate_candidate_run_plan(args, pg, client)


@pytest.mark.parametrize("bad_client", [
    "python:3.11", "sha256:bad", "sha256:" + "A" * 64, None,
])
def test_unverified_client_image_id_rejected(contract, plan, bad_client):
    pg, _, args = plan
    with pytest.raises(ValueError, match="client image identity"):
        contract.validate_candidate_run_plan(args, pg, bad_client)


def test_missing_client_or_postgres_site_rejected(contract, plan):
    pg, client, args = copy.deepcopy(plan)
    del args["server"]
    with pytest.raises(ValueError, match="incomplete"):
        contract.validate_candidate_run_plan(args, pg, client)


def test_unknown_postgres_index_rejected(contract, plan):
    _, client, args = plan
    with pytest.raises(ValueError, match="not verified"):
        contract.validate_candidate_run_plan(
            args, "docker.io/library/postgres@sha256:" + "b" * 64, client)

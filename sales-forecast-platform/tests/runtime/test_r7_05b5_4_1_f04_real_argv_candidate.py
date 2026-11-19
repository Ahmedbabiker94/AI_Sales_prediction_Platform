"""CR-1 pure candidate argv regressions; no Docker or PostgreSQL execution."""
import copy
import importlib
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def case(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    m = importlib.import_module("real_run_argv_candidate")
    ref = importlib.import_module("runtime_reference_candidate").PG_REF
    ctx = {
        "run_id": uuid4(),
        "root": "/tmp/review-checkout/sales-forecast-platform",
        "evidence": "/tmp/review-evidence",
        "postgres_ref": ref,
        "client_image": "sha256:" + "a" * 64,
        "admin_password": "test-only-" + "b" * 40,
    }
    return m, ctx


def test_exact_five_review_argv_baseline(case):
    m, ctx = case
    plan = m.build_review_argv(ctx)
    assert set(plan) == {"provision", "server", "foundation", "restart", "b5-4-1"}
    assert m.validate_review_argv(ctx, plan) is True
    assert all(argv.count("--pull=never") == 1 for argv in plan.values())
    assert all(argv.count("--platform=linux/amd64") == 1 for argv in plan.values())
    assert all("r7_05b3_run=" + str(ctx["run_id"]) in argv for argv in plan.values())
    assert all("r7-05b3-" + ctx["run_id"].hex in " ".join(argv) for argv in plan.values())
    assert all("--network=none" in argv for argv in plan.values())
    assert plan["server"].count(ctx["admin_password"]) == 0  # only inside env pair
    assert "POSTGRES_PASSWORD=" + ctx["admin_password"] in plan["server"]
    assert "--cap-add=CHOWN" in plan["provision"]
    assert not any("--cap-add=CHOWN" in plan[s] for s in
                   ("server", "foundation", "restart", "b5-4-1"))


@pytest.mark.parametrize("stage", ["provision", "server", "foundation", "restart", "b5-4-1"])
@pytest.mark.parametrize("change", [
    "omit_pull", "host_network", "privileged", "uid_root",
    "foreign_label", "extra_docker_socket_mount", "mutable_image",
])
def test_every_stage_rejects_unsafe_argv_mutation(case, stage, change):
    m, ctx = case
    plan = m.build_review_argv(ctx)
    argv = plan[stage]
    if change == "omit_pull":
        argv.remove("--pull=never")
    elif change == "host_network":
        argv.insert(4, "--network=host")
    elif change == "privileged":
        argv.insert(4, "--privileged=true")
    elif change == "uid_root":
        argv.insert(4, "--user=0:0")
    elif change == "foreign_label":
        argv[argv.index("--label") + 1] = "r7_05b3_run=" + str(uuid4())
    elif change == "extra_docker_socket_mount":
        argv[4:4] = ["--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock"]
    else:
        image = ctx["client_image"] if stage not in ("provision", "server") else ctx["postgres_ref"]
        argv[argv.index(image)] = "postgres:16.15" if stage in ("provision", "server") else "python:3.11"
    with pytest.raises(ValueError, match="unauthorized"):
        m.validate_review_argv(ctx, plan)


@pytest.mark.parametrize("stage", ["foundation", "restart", "b5-4-1"])
def test_unexpected_test_selector_rejected(case, stage):
    m, ctx = case
    plan = m.build_review_argv(ctx)
    plan[stage].append("tests/postgres/foreign_test.py")
    with pytest.raises(ValueError, match="unauthorized"):
        m.validate_review_argv(ctx, plan)


@pytest.mark.parametrize("stage", ["foundation", "restart", "b5-4-1"])
def test_writeable_source_or_host_bind_rejected(case, stage):
    m, ctx = case
    plan = m.build_review_argv(ctx)
    argv = plan[stage]
    idx = next(i for i, token in enumerate(argv) if token.startswith(
        "type=bind,src=" + ctx["root"] + "/src"))
    argv[idx] = argv[idx].replace(",readonly", "")
    with pytest.raises(ValueError, match="unauthorized"):
        m.validate_review_argv(ctx, plan)


@pytest.mark.parametrize("bad", [
    "postgres:16.15",
    "sha256:" + "a" * 64,
    "docker.io/library/postgres@sha256:" + "0" * 64,
])
def test_context_rejects_unverified_postgres_ref(case, bad):
    m, ctx = case
    ctx["postgres_ref"] = bad
    with pytest.raises(ValueError, match="PostgreSQL"):
        m.build_review_argv(ctx)


@pytest.mark.parametrize("bad", ["python:3.11", "sha256:wrong", None])
def test_context_rejects_bad_client_id(case, bad):
    m, ctx = case
    ctx["client_image"] = bad
    with pytest.raises(ValueError, match="client image"):
        m.build_review_argv(ctx)


@pytest.mark.parametrize("bad", [
    "/mnt/c/Users/me/sales-forecast-platform",
    "/tmp/other-project",
    "/tmp/../tmp/sales-forecast-platform",
])
def test_context_rejects_noncanonical_or_windows_worktree(case, bad):
    m, ctx = case
    ctx["root"] = bad
    with pytest.raises(ValueError):
        m.build_review_argv(ctx)


def test_context_rejects_evidence_inside_project(case):
    m, ctx = case
    ctx["evidence"] = ctx["root"] + "/evidence"
    with pytest.raises(ValueError, match="outside source"):
        m.build_review_argv(ctx)


def test_context_rejects_untrusted_password(case):
    m, ctx = case
    ctx["admin_password"] = "production-secret"
    with pytest.raises(ValueError, match="disposable"):
        m.build_review_argv(ctx)


def test_foreign_stage_or_missing_stage_rejected(case):
    m, ctx = case
    for mutate in ("extra", "missing"):
        plan = m.build_review_argv(ctx)
        if mutate == "extra":
            plan["foreign"] = ["docker", "run"]
        else:
            plan.pop("restart")
        with pytest.raises(ValueError, match="stage set"):
            m.validate_review_argv(ctx, plan)

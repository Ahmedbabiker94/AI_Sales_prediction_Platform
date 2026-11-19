"""CR-1 source-parity checks independent of candidate's self-validator.

Offline Python/AST checks; never call Docker, PostgreSQL or real runner.run().
The tests compare reviewed invariant anchors in the original source with a
candidate plan, but do not establish that the accepted runner is wired.
"""
import ast
import importlib
from pathlib import Path
from uuid import UUID

import pytest


PROJECT = Path(__file__).resolve().parents[2]
POSTGRES_DIR = PROJECT / "tests" / "postgres"

FOUNDATION = "tests/postgres/test_forecast_evidence_postgres.py"
RESTART = "tests/postgres/test_forecast_evidence_restart.py"
B5 = (
    "tests/postgres/test_stored_forecast_accuracy_postgres.py",
    "tests/postgres/test_canonical_reporting_read.py",
    "tests/postgres/test_r7_05b5_4_1_host_reporting_requests.py",
)
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy")


@pytest.fixture
def review(monkeypatch):
    monkeypatch.syspath_prepend(str(POSTGRES_DIR))
    module = importlib.import_module("real_run_argv_candidate")
    refs = importlib.import_module("runtime_reference_candidate")
    run_id = UUID("123e4567-e89b-42d3-a456-426614174000")
    ctx = {
        "run_id": run_id,
        "root": "/tmp/independent-source/sales-forecast-platform",
        "evidence": "/tmp/independent-evidence",
        "postgres_ref": refs.PG_REF,
        "client_image": "sha256:" + "f" * 64,
        "admin_password": "test-only-" + "d" * 40,
    }
    return ctx, module.build_review_argv(ctx)


@pytest.fixture
def source_trees():
    return {
        name: ast.parse((POSTGRES_DIR / name).read_text(encoding="utf-8"))
        for name in ("run_postgres.py", "run_b5_4_1_postgres.py")
    }


def _source_string_constants(tree):
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and type(node.value) is str}


def _source_tuple_assignment(tree, target):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == target for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("missing original fixed tuple " + target)


def test_original_selectors_are_independently_pinned(source_trees):
    original = _source_string_constants(source_trees["run_postgres.py"])
    assert FOUNDATION in original
    assert RESTART in original
    assert _source_tuple_assignment(
        source_trees["run_b5_4_1_postgres.py"], "TESTS"
    ) == B5


@pytest.mark.parametrize("stage,selectors", [
    ("foundation", (FOUNDATION,)),
    ("restart", (RESTART,)),
    ("b5-4-1", B5),
])
def test_client_post_image_command_is_exact_and_source_derived(review, stage, selectors):
    ctx, plan = review
    argv = plan[stage]
    image = ctx["client_image"]
    assert argv.count(image) == 1
    at = argv.index(image)
    assert "--pull=never" in argv[:at]
    assert "--platform=linux/amd64" in argv[:at]
    assert argv[at+1:] == [
        "-m", "pytest", "-q", "--tb=short", "--maxfail=1",
        "-p", "no:cacheprovider", *selectors,
    ]
    assert not any(token.startswith("--mount") or token == "--env-file"
                   for token in argv[at+1:])


@pytest.mark.parametrize("stage", [
    "provision", "server", "foundation", "restart", "b5-4-1",
])
def test_image_boundary_and_no_pull_options_are_pre_image(review, stage):
    ctx, plan = review
    argv = plan[stage]
    image = (ctx["postgres_ref"] if stage in ("provision", "server")
             else ctx["client_image"])
    assert argv.count(image) == 1
    idx = argv.index(image)
    assert all(option in argv[:idx] for option in (
        "--pull=never", "--platform=linux/amd64", "--network=none",
        "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ))
    assert not any(arg == "--pull=always" or arg == "--network=host"
                   or arg.startswith("--publish") for arg in argv)


@pytest.mark.parametrize("stage", [
    "provision", "server", "foundation", "restart", "b5-4-1",
])
def test_uuid_name_and_hyphenated_label_follow_original_convention(review, stage):
    ctx, plan = review
    argv = plan[stage]
    assert argv[argv.index("--name") + 1] == (
        "r7-05b3-" + ctx["run_id"].hex + "-" + stage)
    assert argv[argv.index("--label") + 1] == (
        "r7_05b3_run=" + str(ctx["run_id"]))


@pytest.mark.parametrize("stage", ["foundation", "restart", "b5-4-1"])
def test_client_mounts_follow_exact_original_readonly_boundaries(review, stage):
    ctx, plan = review
    argv = plan[stage]
    mounts = [argv[i+1] for i, arg in enumerate(argv[:-1]) if arg == "--mount"]
    socket = "r7-05b3-" + ctx["run_id"].hex + "-socket"
    assert mounts == [
        "type=volume,src=" + socket + ",dst=/run/r7pg,readonly",
        "type=bind,src=" + ctx["root"] + "/src,dst=/code/src,readonly",
        "type=bind,src=" + ctx["root"] + "/tests,dst=/code/tests,readonly",
    ]
    assert all("/var/run/docker.sock" not in mount for mount in mounts)


def test_provision_root_exception_is_command_and_volume_scoped(review):
    ctx, plan = review
    argv = plan["provision"]
    idx = argv.index(ctx["postgres_ref"])
    assert "--user=0:0" in argv[:idx]
    assert "--cap-add=CHOWN" in argv[:idx]
    assert argv[idx+1:] == ["999:999", "/var/lib/postgresql/data"]
    assert "--entrypoint" in argv[:idx]
    assert argv[argv.index("--entrypoint") + 1] == "chown"
    mounts = [argv[i+1] for i, arg in enumerate(argv[:idx-1]) if arg == "--mount"]
    assert mounts == [
        "type=volume,src=r7-05b3-" + ctx["run_id"].hex +
        "-data,dst=/var/lib/postgresql/data"
    ]


def test_server_socket_only_and_no_tcp_listening(review):
    ctx, plan = review
    argv = plan["server"]
    idx = argv.index(ctx["postgres_ref"])
    assert "--user=999:999" in argv[:idx]
    assert argv[idx+1:][:2] == ["-c", "listen_addresses="]
    mounts = [argv[i+1] for i, arg in enumerate(argv[:idx-1]) if arg == "--mount"]
    assert mounts == [
        "type=volume,src=r7-05b3-" + ctx["run_id"].hex +
        "-data,dst=/var/lib/postgresql/data",
        "type=volume,src=r7-05b3-" + ctx["run_id"].hex +
        "-socket,dst=/var/run/postgresql",
    ]


def test_proxy_env_clearing_has_same_keys_in_original_and_candidate(
        source_trees, review):
    ctx, plan = review
    original = _source_tuple_assignment(source_trees["run_postgres.py"], "PROXIES")
    assert original == PROXY_KEYS
    for argv in plan.values():
        env_pairs = [
            argv[i+1] for i, value in enumerate(argv[:-1])
            if value == "-e"
        ]
        for key in PROXY_KEYS:
            assert key + "=" in env_pairs


def test_review_candidate_does_not_call_accepted_runner(source_trees):
    # This test documents absence of wiring, not readiness.
    import_text = (POSTGRES_DIR / "real_run_argv_candidate.py").read_text()
    assert "subprocess" not in import_text
    assert "docker.run(" not in import_text
    assert "import run_postgres" not in import_text

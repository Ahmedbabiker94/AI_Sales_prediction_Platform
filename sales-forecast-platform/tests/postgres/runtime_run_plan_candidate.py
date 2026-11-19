"""Strict synthetic F-04 no-pull run-plan contract, candidate-only.

Deliberately accepts *only* five canonical mock argv vectors, with no
additional flags, mounts, environments, commands, or post-image arguments.
This is not a parser or builder for the real PostgreSQL harness: real argv
requires a separately reviewed allowlist and explicit controlled rebaseline.
No Docker, registry, or operating-system commands are executed.
"""
import re

from runtime_reference_candidate import postgres_run_references

_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RUN_HEX = re.compile(r"[0-9a-f]{32}\Z")
_CLIENT_STAGES = ("foundation", "restart", "b5-4-1")


def _expected(name, run_hex, image, user, *, provision=False):
    args = [
        "docker", "run",
        "--name", "r7-05b3-" + run_hex + "-" + name,
        "--label", "r7_05b3_run=" + run_hex,
        "--pull=never", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=" + user,
    ]
    if provision:
        args.append("--cap-add=CHOWN")
    return args + [image]


def _exact(argv, required, image):
    if type(argv) not in (tuple, list) or any(type(x) is not str for x in argv):
        raise ValueError("invalid Docker argv")
    if list(argv[:2]) != ["docker", "run"]:
        raise ValueError("wrong image or command")
    if len(argv) == 0 or argv[-1] != image or argv.count(image) != 1:
        raise ValueError("wrong image or image placement")
    for flag in ("--pull=never", "--network=none", "--read-only",
                 "--cap-drop=ALL", "--security-opt=no-new-privileges"):
        if argv.count(flag) != 1:
            raise ValueError("required isolation option missing or duplicated")
    # Do not accept even a seemingly benign additional option: validating the
    # real run's mounts, env, resources and entrypoints is a SEPARATE review.
    if list(argv) != required:
        raise ValueError("forbidden, reordered, or unbound Docker option")
    return True


def validate_candidate_run_plan(plan, verified_postgres_ref, client_image_id):
    """Fail closed on any deviation from the five synthetic canonical stages."""
    postgres = postgres_run_references(verified_postgres_ref)
    if type(client_image_id) is not str or not _SHA.fullmatch(client_image_id):
        raise ValueError("client image identity unverified")
    if type(plan) is not dict or set(plan) != {
        "run_hex", "provision", "server", "clients"
    }:
        raise ValueError("incomplete run plan")
    run_hex = plan["run_hex"]
    if type(run_hex) is not str or not _RUN_HEX.fullmatch(run_hex):
        raise ValueError("invalid ownership run_hex")
    clients = plan["clients"]
    if type(clients) not in (tuple, list) or len(clients) != len(_CLIENT_STAGES):
        raise ValueError("incomplete client stages")

    _exact(plan["provision"], _expected(
        "provision", run_hex, postgres["provision"], "0:0", provision=True
    ), postgres["provision"])
    _exact(plan["server"], _expected(
        "server", run_hex, postgres["server"], "999:999"
    ), postgres["server"])
    for name, argv in zip(_CLIENT_STAGES, clients):
        _exact(argv, _expected(name, run_hex, client_image_id, "1000:1000"),
               client_image_id)
    return True

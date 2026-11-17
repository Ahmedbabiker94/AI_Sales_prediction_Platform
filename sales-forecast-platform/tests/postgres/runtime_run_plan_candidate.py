"""Pure F-04 proposal for no-pull test runner argv validation.

No invocation of Docker, registry or accepted runner; not implementation approval.
"""
import re
from runtime_reference_candidate import postgres_run_references

_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


def validate_candidate_run_plan(plan, verified_postgres_ref, client_image_id):
    """Validate proposal flags for both PG sites and every client run."""
    postgres = postgres_run_references(verified_postgres_ref)
    if type(client_image_id) is not str or not _SHA.fullmatch(client_image_id):
        raise ValueError("client image identity unverified")
    if type(plan) is not dict or set(plan) != {"provision", "server", "clients"}:
        raise ValueError("incomplete run plan")
    clients = plan["clients"]
    if type(clients) not in (list, tuple) or not clients:
        raise ValueError("client stages missing")

    def check(argv, expected):
        if type(argv) not in (list, tuple) or any(type(s) is not str for s in argv):
            raise ValueError("invalid Docker argv")
        if list(argv[:2]) != ["docker", "run"] or argv.count(expected) != 1:
            raise ValueError("wrong image or command")
        for option in ("--pull=never", "--network=none", "--read-only",
                       "--cap-drop=ALL", "--security-opt=no-new-privileges"):
            if argv.count(option) != 1:
                raise ValueError("required isolation option missing or duplicated")
        if any(s in ("--privileged", "--publish", "-p", "-P", "--network=host")
               or s.startswith(("--publish=", "--network=host", "--pull="))
               and s != "--pull=never" for s in argv):
            raise ValueError("forbidden Docker option")
        if argv.count("--name") != 1 or argv.count("--label") != 1:
            raise ValueError("ownership markers required")
        for key, prefix in (("--name", "r7-05b3-"), ("--label", "r7_05b3_run=")):
            i = argv.index(key)
            if i + 1 == len(argv) or not argv[i + 1].startswith(prefix):
                raise ValueError("ownership marker missing")
    check(plan["provision"], postgres["provision"])
    check(plan["server"], postgres["server"])
    if "--user=0:0" not in plan["provision"] or "--cap-add=CHOWN" not in plan["provision"]:
        raise ValueError("provisioning exception not scoped")
    if "--user=999:999" not in plan["server"]:
        raise ValueError("PostgreSQL must run nonroot")
    for argv in clients:
        check(argv, client_image_id)
        if "--user=1000:1000" not in argv:
            raise ValueError("test client must run nonroot")
    return True

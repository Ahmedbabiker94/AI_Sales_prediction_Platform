"""Independent static parity of protected runner and review-only fork.

AST structural checks deliberately ignore indentation of the outer scope and
compare the exact original Docker/command call expression trees. This does NOT
approve the protected runner, Docker behavior, or security of the fork.
"""
import ast
from collections import Counter
from pathlib import Path


BASE = Path(__file__).resolve().parents[1] / "postgres"
ACCEPTED = BASE / "run_postgres.py"
FORK = BASE / "run_postgres_security_patch_candidate.py"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree, name):
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls(tree, name):
    return Counter(ast.dump(node, include_attributes=False)
                   for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name)
                   and node.func.id == name)


def test_all_original_run_command_calls_remain_ast_identical():
    a = _function(_tree(ACCEPTED), "run")
    b = _function(_tree(FORK), "run")
    assert _calls(a, "command") == _calls(b, "command")


def test_runner_operational_helper_bodies_unmodified():
    a, b = _tree(ACCEPTED), _tree(FORK)
    for fn in ("inspect_image", "remove_owned", "build_client",
               "remove_transient_credential_file", "verified_clean_source"):
        assert ast.dump(_function(a, fn), include_attributes=False) == ast.dump(
            _function(b, fn), include_attributes=False)


def test_trusted_resource_deletion_calls_exactly_preserved_in_run():
    a = _function(_tree(ACCEPTED), "run")
    b = _function(_tree(FORK), "run")
    assert _calls(a, "remove_owned") == _calls(b, "remove_owned")
    assert _calls(a, "remove_transient_credential_file") == _calls(
        b, "remove_transient_credential_file")


def test_finalizer_is_candidate_only_and_no_acceptance_module_imports_it():
    a = _tree(ACCEPTED)
    b = _tree(FORK)
    assert _calls(a, "finalize_manifest_candidate") == Counter()
    assert sum(_calls(b, "finalize_manifest_candidate").values()) == 1
    imported = [node.module for node in a.body if isinstance(node, ast.ImportFrom)]
    assert "redaction_finalization_candidate" not in imported


def test_review_fork_has_no_cli_entrypoint():
    b = _tree(FORK)
    assert not any(isinstance(node, ast.If)
                   and isinstance(node.test, ast.Compare)
                   and any(isinstance(value, ast.Constant)
                           and value.value == "__main__"
                           for value in node.test.comparators)
                   for node in b.body)


def test_accepted_image_pins_and_fixed_test_selectors_preserved():
    a, b = _tree(ACCEPTED), _tree(FORK)
    for symbol in ("BASE", "POSTGRES", "WHEEL", "WHEEL_SHA256"):
        def value(tree):
            return next(ast.literal_eval(n.value) for n in tree.body
                        if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == symbol
                                for t in n.targets))
        assert value(a) == value(b)
    def selected_literals(tree):
        return Counter(n.value for n in ast.walk(_function(tree, "run"))
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and n.value.startswith("tests/postgres/"))
    assert selected_literals(a) == selected_literals(b)

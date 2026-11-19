"""Offline mock-only PyPI and OSV verification tests. Never use network."""
import importlib
import json
from pathlib import Path
import pytest


@pytest.fixture
def setup_case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    m = importlib.import_module("check_external_client_artifacts_candidate")
    monkeypatch.setattr(m, "verify_candidate_wheelhouse", lambda _: True)
    row = {"name": "demo", "version": "1.0", "filename": "demo-1.0-py3-none-any.whl",
           "sha256": "a" * 64}
    (tmp_path / "candidate-manifest.json").write_text(json.dumps({"wheels": [row]}))
    def requester(url, payload=None, *, wrong_hash=False, yanked=False, vulns=None):
        if url.startswith("https://pypi.org/"):
            assert payload is None and "/demo/1.0/json" in url
            return {"info": {"version": "1.0"}, "urls": [{
                "filename": row["filename"], "packagetype": "bdist_wheel",
                "digests": {"sha256": "b"*64 if wrong_hash else row["sha256"]},
                "yanked": yanked}]}
        assert url == "https://api.osv.dev/v1/querybatch"
        assert payload == {"queries": [{"version": "1.0", "package": {
            "ecosystem": "PyPI", "name": "demo"}}]}
        return {"results": [{"vulns": [] if vulns is None else vulns}]}
    return m, tmp_path, requester


def test_exact_pypi_digest_and_empty_osv_are_candidate_only(setup_case):
    m, folder, requester = setup_case
    r = m.check_external_candidate(folder, requester)
    assert r["pypi_exact_file_hashes_match"]
    assert r["no_osv_known_findings_at_query_time"]
    assert r["status"] == "CANDIDATE_ONLY_NOT_APPROVED"


def test_mismatched_pypi_hash_blocks(setup_case):
    m, folder, requester = setup_case
    r = m.check_external_candidate(folder, lambda url, payload=None:
                                   requester(url, payload, wrong_hash=True))
    assert not r["pypi_exact_file_hashes_match"]


def test_yanked_wheel_blocks(setup_case):
    m, folder, requester = setup_case
    r = m.check_external_candidate(folder, lambda url, payload=None:
                                   requester(url, payload, yanked=True))
    assert not r["pypi_exact_file_hashes_match"]


def test_osv_finding_blocks(setup_case):
    m, folder, requester = setup_case
    r = m.check_external_candidate(folder, lambda url, payload=None:
                                   requester(url, payload, vulns=[{"id": "OSV-TEST-1"}]))
    assert r["results"][0]["osv_vulnerability_ids"] == ["OSV-TEST-1"]
    assert not r["no_osv_known_findings_at_query_time"]


def test_osv_missing_response_fails_closed(setup_case):
    m, folder, requester = setup_case
    def broken(url, payload=None):
        if url.startswith("https://api.osv.dev/"):
            return {"results": []}
        return requester(url, payload)
    with pytest.raises(ValueError, match="incomplete"):
        m.check_external_candidate(folder, broken)


def test_no_external_query_when_local_hashes_fail(setup_case, monkeypatch):
    m, folder, _ = setup_case
    monkeypatch.setattr(m, "verify_candidate_wheelhouse",
                        lambda _: (_ for _ in ()).throw(ValueError("tampered wheel")))
    with pytest.raises(ValueError, match="tampered"):
        m.check_external_candidate(folder, lambda *_: (_ for _ in ()).throw(
            AssertionError("must not make network request")))


def test_explicit_v2_top_level_reaches_integrity_gate(setup_case, monkeypatch):
    m, folder, requester = setup_case
    observed = []
    def gate(location, *, top_level):
        observed.append((location, top_level))
        return True
    monkeypatch.setattr(m, "verify_candidate_wheelhouse", gate)
    pins = ("pytest==9.0.3",)
    report = m.check_external_candidate(folder, requester, top_level=pins)
    assert report["pypi_exact_file_hashes_match"]
    assert observed == [(folder, pins)]

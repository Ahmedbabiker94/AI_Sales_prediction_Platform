"""F-03 opt-in, read-only external artifact and vulnerability metadata checks.

Run only with explicit --allow-network. Compare the candidate wheelhouse's
verified filenames and SHA-256 with official release-specific PyPI JSON, and
query exact PyPI versions through OSV. No package download, pip install,
Docker invocation or modification of accepted harness. No approval implied.
On lookup errors, partial results, yanked files or OSV findings, fail closed.
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

if __package__:
    from .verify_client_wheelhouse_candidate import verify_candidate_wheelhouse
else:
    from verify_client_wheelhouse_candidate import verify_candidate_wheelhouse


def request_json(url, payload=None):
    headers = {"Accept": "application/json", "User-Agent": "B5-4-1-candidate-review/1.0"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=None if payload is None else json.dumps(payload).encode("utf-8"),
                  headers=headers, method="GET" if payload is None else "POST")
    # The caller must explicitly enable outbound network. Bounded responses.
    with urlopen(req, timeout=20) as response:
        if response.status != 200:
            raise ValueError("external metadata lookup did not return HTTP 200")
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("external metadata response too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("invalid metadata response")
    return result


def check_external_candidate(folder, request=request_json):
    folder = Path(folder)
    verify_candidate_wheelhouse(folder)
    rows = json.loads((folder / "candidate-manifest.json").read_text(encoding="utf-8"))["wheels"]
    results = []
    all_pypi_match = True
    for row in sorted(rows, key=lambda x: x["name"]):
        url = ("https://pypi.org/pypi/" + quote(row["name"], safe="") + "/"
               + quote(row["version"], safe="") + "/json")
        data = request(url)
        files = data.get("urls")
        if not isinstance(files, list):
            raise ValueError("missing PyPI release file inventory")
        matching = [f for f in files if isinstance(f, dict)
                    and f.get("filename") == row["filename"]]
        # Exact single file, digest, wheel type, release version and non-yanked.
        ok = (len(matching) == 1 and data.get("info", {}).get("version") == row["version"]
              and matching[0].get("packagetype") == "bdist_wheel"
              and matching[0].get("digests", {}).get("sha256") == row["sha256"]
              and matching[0].get("yanked") is False)
        results.append({"name": row["name"], "version": row["version"],
                        "filename": row["filename"], "pypi_filename_sha256_match": bool(ok)})
        all_pypi_match &= bool(ok)
    payload = {"queries": [{"version": r["version"], "package": {
        "ecosystem": "PyPI", "name": r["name"]}} for r in results]}
    osv = request("https://api.osv.dev/v1/querybatch", payload)
    responses = osv.get("results")
    if not isinstance(responses, list) or len(responses) != len(results):
        raise ValueError("OSV returned missing or incomplete results")
    all_osv_clear = True
    for row, response in zip(results, responses):
        if not isinstance(response, dict):
            raise ValueError("invalid OSV response")
        vulns = response.get("vulns", [])
        if not isinstance(vulns, list) or "next_page_token" in response:
            raise ValueError("OSV response incomplete; fail closed")
        ids = []
        for item in vulns:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("invalid OSV vulnerability record")
            ids.append(item["id"])
        row["osv_vulnerability_ids"] = ids
        all_osv_clear &= not ids
    return {"status": "CANDIDATE_ONLY_NOT_APPROVED",
            "pypi_exact_file_hashes_match": all_pypi_match,
            "no_osv_known_findings_at_query_time": all_osv_clear,
            "results": results,
            "review_note": "Known advisories only; no proof of absence of vulnerabilities, no license or provenance attestation approval."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("explicit external PyPI/OSV metadata access required")
    try:
        report = check_external_candidate(args.wheelhouse)
    except Exception as error:
        print("BLOCKED: external candidate check unavailable or invalid: " + type(error).__name__,
              file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    if not report["pypi_exact_file_hashes_match"] or not report["no_osv_known_findings_at_query_time"]:
        print("NO-GO: hash mismatch, yanked release or known OSV vulnerability", file=sys.stderr)
        return 2
    print("Candidate external checks completed; independent review still mandatory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

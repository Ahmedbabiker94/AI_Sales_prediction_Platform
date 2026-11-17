"""Read-only F-03 supply-chain and license inventory for approved-input CANDIDATE wheels.

This extracts wheel-declared information only. Metadata and URLs are NOT a
trusted attestation of package origin, license acceptability, vulnerability
status, or PyPI upload identity. No network, installation, Docker, or writes.
"""
import argparse
from email.parser import Parser
import json
from pathlib import Path
import zipfile

if __package__:
    from .verify_client_wheelhouse_candidate import verify_candidate_wheelhouse
else:
    from verify_client_wheelhouse_candidate import verify_candidate_wheelhouse


def inventory_candidate_supply_chain(folder, *, candidate_version="v1"):
    folder = Path(folder)
    # Reject any missing, extra or hash-mismatched inputs first.
    if candidate_version == "v1":
        verify_candidate_wheelhouse(folder)
    elif candidate_version == "v2":
        if __package__:
            from .prepare_client_wheelhouse_v2_candidate import V2_TOP_LEVEL
        else:
            from prepare_client_wheelhouse_v2_candidate import V2_TOP_LEVEL
        verify_candidate_wheelhouse(folder, top_level=V2_TOP_LEVEL)
    else:
        raise ValueError("unsupported candidate version")
    manifest = json.loads((folder / "candidate-manifest.json").read_text(encoding="utf-8"))
    rows = []
    for entry in sorted(manifest["wheels"], key=lambda x: x["name"]):
        with zipfile.ZipFile(folder / entry["filename"]) as z:
            paths = [p for p in z.namelist() if p.endswith(".dist-info/METADATA")]
            if len(paths) != 1:
                raise ValueError("ambiguous package metadata")
            meta = Parser().parsestr(z.read(paths[0]).decode("utf-8"))
            license_files = sorted(p for p in z.namelist()
                                   if ".dist-info/licenses/" in p
                                   and not p.endswith("/"))
        declared_expression = meta.get("License-Expression")
        legacy_license = meta.get("License")
        classifiers = meta.get_all("Classifier", [])
        license_classifiers = [c for c in classifiers if c.startswith("License ::")]
        license_file_headers = meta.get_all("License-File", [])
        metadata_present = bool(declared_expression or legacy_license or license_classifiers
                                or license_files or license_file_headers)
        project_urls = meta.get_all("Project-URL", [])
        rows.append({
            "name": entry["name"],
            "version": entry["version"],
            "filename": entry["filename"],
            "sha256": entry["sha256"],
            "license_expression": declared_expression,
            "legacy_license": legacy_license,
            "license_classifiers": license_classifiers,
            "license_file_headers": license_file_headers,
            "embedded_license_files": license_files,
            "declared_home_page": meta.get("Home-page"),
            "declared_project_urls": project_urls,
            "license_information_present": metadata_present,
            "needs_license_manual_review": True,
            "pypi_artifact_provenance_verified": False,
            "vulnerability_review_completed": False,
        })
    return {
        "status": "REVIEW_REQUIRED_NOT_APPROVED",
        "wheels_verified": len(rows),
        "license_information_missing": sum(not r["license_information_present"] for r in rows),
        "origin_attestations_verified": 0,
        "vulnerability_reviews_completed": 0,
        "packages": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--candidate-version", choices=("v1", "v2"), default="v1")
    args = parser.parse_args()
    result = inventory_candidate_supply_chain(args.wheelhouse, candidate_version=args.candidate_version)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

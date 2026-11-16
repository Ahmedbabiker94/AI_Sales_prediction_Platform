"""Read-only F-03 candidate dependency closure from wheel metadata.

Only base requirements (extra='') active on CPython 3.11/Linux amd64 are
evaluated. Extra feature sets and platform ABI/license security are separate
review gates. Never uses installed site-packages to supply missing packages.
"""
import json
from email.parser import Parser
from pathlib import Path
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

if __package__:
    from .verify_client_wheelhouse_candidate import verify_candidate_wheelhouse
else:
    from verify_client_wheelhouse_candidate import verify_candidate_wheelhouse


def verify_candidate_dependency_closure(folder):
    """Reject unresolved or incompatible required wheel metadata dependencies."""
    folder = Path(folder)
    verify_candidate_wheelhouse(folder)
    entries = json.loads((folder / "candidate-manifest.json").read_text(encoding="utf-8"))["wheels"]
    installed = {canonicalize_name(x["name"]): x["version"] for x in entries}
    env = default_environment()
    env.update({
        "python_version": "3.11",
        "python_full_version": "3.11.18",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "extra": "",
    })
    checked = 0
    active = 0
    for entry in entries:
        wheel = folder / entry["filename"]
        with zipfile.ZipFile(wheel) as archive:
            metadata_paths = [x for x in archive.namelist() if x.endswith(".dist-info/METADATA")]
            if len(metadata_paths) != 1:
                raise ValueError("ambiguous wheel metadata")
            metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode("utf-8"))
        python_spec = metadata.get("Requires-Python")
        if python_spec:
            try:
                if not SpecifierSet(python_spec).contains("3.11.18", prereleases=True):
                    raise ValueError("unsupported Python runtime in wheel metadata")
            except ValueError as exc:
                raise ValueError("invalid Requires-Python marker") from exc
        for raw in metadata.get_all("Requires-Dist", []):
            checked += 1
            try:
                requirement = Requirement(raw)
                if requirement.marker and not requirement.marker.evaluate(environment=env):
                    continue
            except Exception as exc:
                raise ValueError("invalid wheel dependency marker") from exc
            active += 1
            name = canonicalize_name(requirement.name)
            version = installed.get(name)
            if version is None or not requirement.specifier.contains(version, prereleases=True):
                raise ValueError("missing or incompatible required wheel dependency")
    return {"wheels": len(entries), "requires_dist_checked": checked,
            "active_base_dependencies_satisfied": active}

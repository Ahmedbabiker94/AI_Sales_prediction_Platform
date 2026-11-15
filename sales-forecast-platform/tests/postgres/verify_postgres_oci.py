"""Read-only, pinned OCI identity inspection for the B5-4-1 PostgreSQL image.

Docker 29's containerd store may report the index digest as .Id instead of
the image-config digest. Keep these distinct. This checker never modifies the
original disposable Postgres harness, pulls images or launches containers.

The two buildx imagetools calls fetch only immutable OCI metadata from the
registry. A network outage MUST fail closed. This verification is not proof
that the original harness can use a config-digest-only local image lookup.
"""
import json
import subprocess

INDEX = "sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938"
MANIFEST = "sha256:cf5d353f55240ecc2f811827a15c7d1f2d562f08019b49dca12b5a59f4ab5962"
CONFIG = "sha256:6a35283e53fbe883da6a6ffbce8d3999dc66076cc5c4e2f1d21bdbd9e26bf86f"
REGISTRY_IMAGE = "docker.io/library/postgres"
OCI_INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
                   "application/vnd.docker.distribution.manifest.list.v2+json")
OCI_MANIFEST_TYPES = ("application/vnd.oci.image.manifest.v1+json",
                      "application/vnd.docker.distribution.manifest.v2+json")


def _output(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def verified_postgres_oci():
    """Return True ONLY for the locally present pinned linux/amd64 artifact.

    Registry metadata is bound to hard-coded digest references (never tags).
    No image data is downloaded. Cannot attest to hostile Docker hosts.
    """
    ref = REGISTRY_IMAGE + "@" + INDEX
    present = _output(["docker", "--host=unix:///var/run/docker.sock",
                       "image", "inspect", "--platform", "linux/amd64",
                       ref, "--format", "{{.Id}} {{.Os}} {{.Architecture}}"])
    if present is None:
        return False
    identity = present.strip().split()
    if len(identity) != 3 or identity[1:] != ["linux", "amd64"]:
        return False
    # A classic Docker store may use CONFIG; containerd uses INDEX or
    # the selected MANIFEST. No other local image ID is accepted.
    if identity[0] not in (INDEX, MANIFEST, CONFIG):
        return False

    index_raw = _output(["docker", "buildx", "imagetools", "inspect", ref, "--raw"])
    manifest_raw = _output(["docker", "buildx", "imagetools", "inspect",
                            REGISTRY_IMAGE + "@" + MANIFEST, "--raw"])
    if index_raw is None or manifest_raw is None:
        return False
    try:
        index = json.loads(index_raw)
        manifest = json.loads(manifest_raw)
        if index.get("mediaType") not in OCI_INDEX_TYPES:
            return False
        platforms = [m for m in index.get("manifests", [])
                     if m.get("platform", {}).get("os") == "linux"
                     and m.get("platform", {}).get("architecture") == "amd64"]
        if len(platforms) != 1:
            return False
        entry = platforms[0]
        if (entry.get("digest") != MANIFEST
                or entry.get("mediaType") not in OCI_MANIFEST_TYPES):
            return False
        return (manifest.get("mediaType") in OCI_MANIFEST_TYPES
                and manifest.get("config", {}).get("digest") == CONFIG
                and manifest.get("config", {}).get("mediaType") in
                ("application/vnd.oci.image.config.v1+json",
                 "application/vnd.docker.container.image.v1+json"))
    except (TypeError, ValueError, AttributeError):
        return False


if __name__ == "__main__":
    passed = verified_postgres_oci()
    print(("PASS" if passed else "BLOCKED")
          + ": pinned OCI index, linux/amd64 manifest and config identity")
    print("Read-only identity verification; NOT a PostgreSQL integration run.")
    raise SystemExit(0 if passed else 2)

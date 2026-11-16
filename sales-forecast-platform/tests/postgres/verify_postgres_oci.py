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
CLIENT_REGISTRY_IMAGE = "docker.io/library/python"
CLIENT_INDEX = "sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39"
CLIENT_MANIFEST = "sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944"
CLIENT_CONFIG = "sha256:cf2dad23925ed51a00735f57606b2998aedf4705a1634cb9ae1053b5c145d5ba"
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


def verified_pinned_oci(registry_image, index_digest, manifest_digest, config_digest):
    """Return True ONLY for the locally present pinned linux/amd64 artifact.

    Registry metadata is bound to hard-coded digest references (never tags).
    No image data is downloaded. Cannot attest to hostile Docker hosts.
    """
    ref = registry_image + "@" + index_digest
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
    if identity[0] not in (index_digest, manifest_digest, config_digest):
        return False

    index_raw = _output(["docker", "buildx", "imagetools", "inspect", ref, "--raw"])
    manifest_raw = _output(["docker", "buildx", "imagetools", "inspect",
                            registry_image + "@" + manifest_digest, "--raw"])
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
        if (entry.get("digest") != manifest_digest
                or entry.get("mediaType") not in OCI_MANIFEST_TYPES):
            return False
        return (manifest.get("mediaType") in OCI_MANIFEST_TYPES
                and manifest.get("config", {}).get("digest") == config_digest
                and manifest.get("config", {}).get("mediaType") in
                ("application/vnd.oci.image.config.v1+json",
                 "application/vnd.docker.container.image.v1+json"))
    except (TypeError, ValueError, AttributeError):
        return False


def verified_postgres_oci():
    """Legacy public entry point; read-only, fail-closed PostgreSQL check."""
    return verified_pinned_oci(REGISTRY_IMAGE, INDEX, MANIFEST, CONFIG)


def verified_client_candidate_oci():
    """Read-only candidate only; NOT a rebaseline approval."""
    return verified_pinned_oci(CLIENT_REGISTRY_IMAGE, CLIENT_INDEX,
                               CLIENT_MANIFEST, CLIENT_CONFIG)


if __name__ == "__main__":
    passed = verified_postgres_oci()
    print(("PASS" if passed else "BLOCKED")
          + ": pinned OCI index, linux/amd64 manifest and config identity")
    print("Read-only identity verification; NOT a PostgreSQL integration run.")
    raise SystemExit(0 if passed else 2)

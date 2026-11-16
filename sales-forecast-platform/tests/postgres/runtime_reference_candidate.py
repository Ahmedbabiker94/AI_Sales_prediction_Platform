"""F-02 candidate-only pure runtime reference and build identity contracts.

Does not run Docker, access a registry, mutate the accepted runner, or
approve an image rebaseline. An external verifier must approve the pinned
PostgreSQL repository-index reference before calling this module.
"""
import re

from verify_postgres_oci import INDEX, REGISTRY_IMAGE

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
PG_REF = REGISTRY_IMAGE + "@" + INDEX


def postgres_run_references(verified_ref):
    """Both disposable PostgreSQL container stages must use the same pin.

    Keep image selection separate from actual Docker argv and permissions.
    """
    if type(verified_ref) is not str or verified_ref != PG_REF:
        raise ValueError("PostgreSQL runtime reference not verified")
    return {"provision": verified_ref, "server": verified_ref}


def local_build_identity(iidfile_text, image_inspect):
    """Validate syntax and local architecture of one build result.

    This is a structural check, NOT sufficient provenance or production
    authorization. Docker's local ID can be index/config/manifest dependent.
    """
    if type(iidfile_text) is not str or not _SHA256.fullmatch(iidfile_text.strip()):
        raise ValueError("invalid built client iidfile digest")
    iid = iidfile_text.strip()
    if not isinstance(image_inspect, dict):
        raise ValueError("invalid built client inspect data")
    local_id = image_inspect.get("Id")
    if type(local_id) is not str or not _SHA256.fullmatch(local_id):
        raise ValueError("invalid local image ID")
    if local_id != iid:
        raise ValueError("built client image identity mismatch")
    if image_inspect.get("Os") != "linux" or image_inspect.get("Architecture") != "amd64":
        raise ValueError("built client image platform mismatch")
    return iid

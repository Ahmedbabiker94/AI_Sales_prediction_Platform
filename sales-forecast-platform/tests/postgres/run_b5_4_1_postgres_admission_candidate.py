"""F04-R2-04 R3: offline-only proposed B5-4-1 admission entrypoint.

The current operational run_b5_4_1_postgres.py still imports the protected
frozen runner and is NOT modified/rewired. This module is a candidate-only
prototype for a later owner-authorized wrapper rebaseline. NO CLI entrypoint.
It cannot execute Docker/PG unless an external test explicitly replaces all
of the candidate runner's unconditional OFFLINE_REVIEW_ONLY guards with fakes.

A bare run-manifest.json is NOT an admission API. A trusted synchronous caller
receives only a controller-validated immutable in-process evidence snapshot.
"""
from run_manifest_admission_controller_candidate import invoke_candidate_and_admit


def run_reporting_stage_with_bound_admission_candidate(
        *, allow, wheelhouse, evidence, reporting_stage):
    """Proposed trusted B5-4-1 caller: controller owns runner + admission.

    The reporting_stage is an owner-controlled function; it must not be
    selected by an untrusted caller, archive, or dynamic input.
    """
    if not callable(reporting_stage):
        raise TypeError('trusted reporting stage callback required')
    return invoke_candidate_and_admit(
        allow=allow, wheelhouse=wheelhouse, evidence=evidence,
        _host_stage=reporting_stage)

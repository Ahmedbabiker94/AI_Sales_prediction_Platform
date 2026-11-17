"""F-04 candidate-only exception-safe scope model for protected run() review.

Pure Python model; NO Docker, signal API, filesystem, or real runner integration.
The functions supplied by tests model installation and restoration. Accepted
run_postgres.py remains unchanged pending separate controlled-rebaseline approval.
"""
from contextvars import ContextVar
from contextlib import contextmanager

REVIEW_LIFECYCLE = ContextVar("r7_f04_review_lifecycle", default=None)
REVIEW_SECRETS = ContextVar("r7_f04_review_secrets", default=())


@contextmanager
def protected_scope_candidate(record, *, install_handler, restore_handler,
                              make_secrets):
    """Bound mock lifecycle with exception-safe early and late cleanup.

    The real runner's handler installation, cleanup ledger, and SIGTERM calls
    must be independently reviewed. This model does not execute or implement
    them. Restore failures propagate but cannot prevent token resets.
    """
    if type(record) is not dict:
        raise ValueError("invalid lifecycle record")
    lifecycle_token = REVIEW_LIFECYCLE.set(record)
    secrets_token = None
    installed = False
    try:
        # The real version needs a trustworthy saved SIGTERM handler and
        # must distinguish attempted vs successful handler replacement.
        install_handler()
        installed = True
        generated = make_secrets()
        if type(generated) is not tuple or not generated or any(
            type(secret) is not str or not secret for secret in generated
        ):
            raise ValueError("invalid generated disposable secrets")
        secrets_token = REVIEW_SECRETS.set(generated)
        yield generated
    finally:
        try:
            if installed:
                restore_handler()
        finally:
            try:
                if secrets_token is not None:
                    REVIEW_SECRETS.reset(secrets_token)
            finally:
                REVIEW_LIFECYCLE.reset(lifecycle_token)

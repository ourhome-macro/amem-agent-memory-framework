"""Terminal workflow outcomes understood by the durable job ledger."""


class JobReconciliationRequired(RuntimeError):
    """An uncertain side effect must be resolved before any replay."""


class JobPermanentFailure(RuntimeError):
    """A terminal agent failure must not consume repeated queue deliveries."""

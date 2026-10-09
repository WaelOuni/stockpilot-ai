class OrderTransactionFailedError(Exception):
    """The transaction did not commit."""


class OrderOutcomeUnknownError(Exception):
    """The commit outcome could not be reconciled safely."""

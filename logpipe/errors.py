class RejectError(Exception):
    """Raised when a single input line cannot be loaded.

    `reason` is a stable, machine-readable code (see docs/data_dictionary.md)
    that is stored with the rejected record.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

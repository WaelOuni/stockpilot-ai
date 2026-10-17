"""Safe resource failures from model adapters, without provider details."""


class ModelInputLimitError(RuntimeError):
    pass


class ModelUnavailableError(RuntimeError):
    pass


class ModelTimeoutError(RuntimeError):
    pass

"""Safe resource failures from model adapters, without provider details."""


class ModelInputLimitError(RuntimeError):
    code = "input_limit"


class ModelUnavailableError(RuntimeError):
    code = "dependency_unavailable"


class ModelTimeoutError(RuntimeError):
    def __init__(self, code="transport_timeout"):
        self.code = code if code in {"connect_timeout", "read_timeout", "write_timeout", "pool_timeout"} else "transport_timeout"
        super().__init__("Model transport timeout.")


class ModelProviderError(ModelUnavailableError):
    def __init__(self, status):
        self.status = status
        self.code = f"provider_http_{status}" if status in (500, 503) else "provider_http_error"
        super().__init__("Model HTTP error.")


class ModelTransportError(ModelUnavailableError):
    code = "transport_error"


class ModelResponseDecodeError(ModelUnavailableError):
    code = "response_decode_error"

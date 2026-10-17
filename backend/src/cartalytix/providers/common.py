"""Types shared by store adapters and the provider-neutral sync engine."""

from typing import Any


class ProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderPayload(dict[str, Any]):
    """Decoded data with the original JSON entity for lossless source archiving."""

    def __init__(self, payload: dict[str, Any], raw: bytes) -> None:
        super().__init__(payload)
        self.raw = raw

"""Bounded, read-only JSON-RPC transport. Never include endpoint secrets in errors."""

import json
from http.client import HTTPException
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class IngestionError(Exception):
    """Collection could not safely advance the canonical checkpoint."""


class RpcSource(Protocol):
    def call(self, method: str, params: list) -> object:
        """Return a JSON-RPC result or raise IngestionError."""


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise IngestionError("duplicate RPC JSON field")
        result[key] = value
    return result


class Rpc:
    def __init__(self, endpoint: str) -> None:
        try:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or any(
                    ord(character) <= 32 or ord(character) == 127
                    for character in endpoint
                )
            ):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise IngestionError("invalid RPC endpoint") from None
        self.endpoint = endpoint

    def call(self, method: str, params: list) -> object:
        if method not in {
            "eth_chainId",
            "eth_getBlockByNumber",
            "eth_getTransactionReceipt",
        }:
            raise IngestionError("unsupported RPC method")
        request = Request(
            self.endpoint,
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = response.read(64 * 1024 * 1024 + 1)
            if len(payload) > 64 * 1024 * 1024:
                raise IngestionError("RPC response too large")
            result = json.loads(payload, object_pairs_hook=unique_object)
        except (URLError, OSError, ValueError, HTTPException):
            raise IngestionError("RPC transport or JSON failure") from None
        if (
            not isinstance(result, dict)
            or result.get("jsonrpc") != "2.0"
            or type(result.get("id")) is not int
            or result["id"] != 1
            or "error" in result
            or "result" not in result
        ):
            raise IngestionError("invalid or unsuccessful RPC response")
        return result["result"]

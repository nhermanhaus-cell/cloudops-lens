from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import requests

from cloudops_lens.capacity import (
    CapacityUnavailable,
    fetch_capacity_snapshot,
    normalize_capacity_payloads,
    offering_key,
)

REGIONS = {
    "data": [
        {"name": "us-east-1", "description": "Washington, D.C."},
        {"name": "us-west-1", "description": "California"},
    ]
}
INSTANCE_TYPES = {
    "data": {
        "gpu_1x_a100": {
            "instance_type": {
                "name": "gpu_1x_a100",
                "gpu_description": "NVIDIA A100 40 GB",
                "price_cents_per_hour": 129,
                "specs": {"gpus": 1, "vcpus": 30, "memory_gib": 200, "storage_gib": 512},
            },
            "regions_with_capacity_available": [{"name": "us-east-1"}],
        }
    }
}


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return next(self.responses)


class TimeoutSession:
    def get(self, *args, **kwargs):
        raise requests.Timeout("detail that must stay behind the sanitized exception")


def test_capacity_expands_every_offering_region_pair() -> None:
    result = normalize_capacity_payloads(
        REGIONS,
        INSTANCE_TYPES,
        datetime(2026, 8, 23, tzinfo=UTC),
    )
    assert result["offerings"][0]["offering_key"] == "nvidia-a100-40-gb-1x"
    assert len(result["availability"]) == 2
    assert {(row["region_name"], row["available"]) for row in result["availability"]} == {
        ("us-east-1", True),
        ("us-west-1", False),
    }


def test_capacity_uses_bearer_auth_without_persisting_key() -> None:
    key = "test-secret-that-must-not-leak"
    session = FakeSession([FakeResponse(REGIONS), FakeResponse(INSTANCE_TYPES)])
    result = fetch_capacity_snapshot(key, session=session, sleep_fn=lambda _: None)
    assert session.requests[0]["headers"]["Authorization"] == f"Bearer {key}"
    assert key not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "credentials"), (403, "credentials"), (429, "rate limit"), (500, "invalid")],
)
def test_capacity_http_failures_are_sanitized(status: int, message: str) -> None:
    key = "never-print-me"
    session = FakeSession([FakeResponse({}, status)])
    with pytest.raises(CapacityUnavailable, match=message) as error:
        fetch_capacity_snapshot(key, session=session, sleep_fn=lambda _: None)
    assert key not in str(error.value)


def test_capacity_missing_key_and_malformed_or_empty_payloads_are_nonsecret_errors() -> None:
    with pytest.raises(CapacityUnavailable, match="not configured"):
        fetch_capacity_snapshot(None, sleep_fn=lambda _: None)
    with pytest.raises(CapacityUnavailable, match="unexpected regions"):
        normalize_capacity_payloads({}, INSTANCE_TYPES)
    with pytest.raises(CapacityUnavailable, match="no regions"):
        normalize_capacity_payloads({"data": []}, INSTANCE_TYPES)


def test_capacity_timeout_is_a_nonfatal_sanitized_error() -> None:
    with pytest.raises(CapacityUnavailable, match="unreachable") as error:
        fetch_capacity_snapshot(
            "timeout-key-must-not-leak", session=TimeoutSession(), sleep_fn=lambda _: None
        )
    assert "timeout-key-must-not-leak" not in str(error.value)


def test_offering_normalization_is_stable() -> None:
    assert offering_key("NVIDIA H100 SXM 80 GB", 8) == "nvidia-h100-sxm-80-gb-8x"

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
    assert {
        (row["region_name"], row["availability_status"])
        for row in result["availability"]
    } == {
        ("us-east-1", "reported_available"),
        ("us-west-1", "not_reported_available"),
    }
    assert result["normalization_summary"]["reported_available_assignments"] == 1
    assert result["normalization_summary"]["comparison_rows_created"] == 2


def test_empty_positive_list_is_valid_and_never_claims_explicit_unavailability() -> None:
    instance_types = json.loads(json.dumps(INSTANCE_TYPES))
    instance_types["data"]["gpu_1x_a100"]["regions_with_capacity_available"] = []
    result = normalize_capacity_payloads(REGIONS, instance_types)
    assert all(not row["reported_available"] for row in result["availability"])
    assert {row["availability_status"] for row in result["availability"]} == {
        "not_reported_available"
    }


def test_availability_only_region_is_preserved_and_reconciled() -> None:
    instance_types = json.loads(json.dumps(INSTANCE_TYPES))
    instance_types["data"]["gpu_1x_a100"]["regions_with_capacity_available"].append(
        {"name": "us-north-1", "description": "API-only region"}
    )
    result = normalize_capacity_payloads(REGIONS, instance_types)
    positive_regions = {
        row["region_name"] for row in result["availability"] if row["reported_available"]
    }
    assert positive_regions == {"us-east-1", "us-north-1"}
    assert result["normalization_summary"]["availability_only_regions"] == ["us-north-1"]
    assert result["normalization_summary"]["comparison_rows_created"] == 3
    api_only = next(region for region in result["regions"] if region["name"] == "us-north-1")
    assert api_only["availability_only"] is True
    assert api_only["reported_by_regions_endpoint"] is False


def test_duplicate_source_identifiers_fail_safely() -> None:
    duplicate_regions = {
        "data": [REGIONS["data"][0], REGIONS["data"][0]],
    }
    with pytest.raises(CapacityUnavailable, match="duplicate region"):
        normalize_capacity_payloads(duplicate_regions, INSTANCE_TYPES)

    duplicate_instance_types = {
        "data": [
            INSTANCE_TYPES["data"]["gpu_1x_a100"],
            INSTANCE_TYPES["data"]["gpu_1x_a100"],
        ]
    }
    with pytest.raises(CapacityUnavailable, match="duplicate instance-type"):
        normalize_capacity_payloads(REGIONS, duplicate_instance_types)


def test_malformed_availability_region_is_disclosed_without_hiding_valid_rows() -> None:
    instance_types = json.loads(json.dumps(INSTANCE_TYPES))
    instance_types["data"]["gpu_1x_a100"]["regions_with_capacity_available"].extend(
        [None, {}, {"name": "us-west-1"}]
    )
    result = normalize_capacity_payloads(REGIONS, instance_types)
    assert result["normalization_summary"]["malformed_availability_region_records"] == 2
    assert result["normalization_summary"]["reported_available_assignments"] == 2


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


def test_source_instance_types_can_share_an_analytical_offering_key() -> None:
    duplicate_shape = {
        "data": {
            **INSTANCE_TYPES["data"],
            "gpu_1x_a100_alt": {
                "instance_type": {
                    "name": "gpu_1x_a100_alt",
                    "gpu_description": "NVIDIA A100 40 GB",
                    "price_cents_per_hour": 149,
                    "specs": {
                        "gpus": 1,
                        "vcpus": 32,
                        "memory_gib": 240,
                        "storage_gib": 1024,
                    },
                },
                "regions_with_capacity_available": [{"name": "us-west-1"}],
            },
        }
    }
    result = normalize_capacity_payloads(REGIONS, duplicate_shape)
    assert len(result["offerings"]) == 2
    assert {row["source_instance_type"] for row in result["offerings"]} == {
        "gpu_1x_a100",
        "gpu_1x_a100_alt",
    }
    assert len({row["offering_key"] for row in result["offerings"]}) == 1


def test_non_gpu_and_incomplete_rows_do_not_hide_valid_gpu_capacity() -> None:
    mixed_catalog = {
        "data": {
            **INSTANCE_TYPES["data"],
            "cpu_general": {
                "instance_type": {
                    "name": "cpu_general",
                    "description": "General purpose CPU",
                    "specs": {"gpus": 0, "vcpus": 16, "memory_gib": 64},
                },
                "regions_with_capacity_available": [{"name": "us-east-1"}],
            },
            "incomplete": {"instance_type": None},
        }
    }
    result = normalize_capacity_payloads(REGIONS, mixed_catalog)
    assert len(result["offerings"]) == 1
    summary = result["normalization_summary"]
    assert summary["skipped_non_gpu_instance_types"] == 1
    assert summary["skipped_invalid_instance_types"] == 1

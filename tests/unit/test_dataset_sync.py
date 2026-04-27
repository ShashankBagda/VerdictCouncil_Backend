"""Unit tests for tests/eval/dataset_sync.py — Sprint 3 3.D1.1.

Hermetic: every LangSmith call is replaced with an in-memory fake so we
can assert idempotency, payload shape, and error paths without network.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.eval import dataset_sync

# ---------------------------------------------------------------------------
# In-memory fake LangSmith client
# ---------------------------------------------------------------------------


class _FakeDataset:
    def __init__(self, id_: str = "ds-1"):
        self.id = id_


class _FakeExample:
    def __init__(self, golden_id: str | None):
        self.metadata = {"golden_id": golden_id} if golden_id else {}


class _FakeClient:
    """Stand-in for ``langsmith.Client`` with just the calls we use."""

    def __init__(
        self,
        *,
        dataset: _FakeDataset | None = None,
        existing: list[_FakeExample] | None = None,
    ):
        self._dataset = dataset
        self._existing = existing or []
        self.create_examples_calls: list[dict] = []
        self.create_dataset_calls: list[dict] = []

    def read_dataset(self, *, dataset_name: str | None = None, **_):
        if self._dataset is None:
            raise LookupError(f"dataset {dataset_name!r} not found")
        return self._dataset

    def create_dataset(self, dataset_name: str, *, description: str | None = None, **_):
        ds = _FakeDataset(id_="ds-new")
        self._dataset = ds
        self.create_dataset_calls.append({"dataset_name": dataset_name, "description": description})
        return ds

    def list_examples(self, *, dataset_id=None, **_):
        yield from self._existing

    def create_examples(self, *, dataset_id, examples, **_):
        self.create_examples_calls.append({"dataset_id": dataset_id, "examples": list(examples)})
        return SimpleNamespace(count=len(examples))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fixture(golden_id: str, domain: str = "small_claims") -> dict:
    return {
        "metadata": {
            "id": golden_id,
            "domain": domain,
            "author": "tests",
            "date": "2026-04-25",
        },
        "inputs": {"case_id": f"case-{golden_id}", "domain": domain, "parties": []},
        "expected": {"intake": {"domain": domain}, "research": {}},
    }


# ---------------------------------------------------------------------------
# fixture_to_example
# ---------------------------------------------------------------------------


def test_fixture_to_example_carries_golden_id_in_metadata():
    fx = _fixture("sc-1")
    out = dataset_sync.fixture_to_example(fx)
    assert out["metadata"]["golden_id"] == "sc-1"
    assert out["metadata"]["domain"] == "small_claims"
    assert out["inputs"] == fx["inputs"]
    assert out["outputs"] == fx["expected"]


# ---------------------------------------------------------------------------
# sync — happy path
# ---------------------------------------------------------------------------


def test_sync_creates_examples_when_dataset_missing():
    client = _FakeClient(dataset=None, existing=[])
    fixtures = [_fixture("sc-1"), _fixture("tr-1", domain="traffic_violation")]

    report = dataset_sync.sync(client, fixtures)

    assert client.create_dataset_calls == [
        {
            "dataset_name": dataset_sync.DATASET_NAME,
            "description": dataset_sync.DATASET_DESCRIPTION,
        }
    ]
    assert len(client.create_examples_calls) == 1
    sent = client.create_examples_calls[0]["examples"]
    assert {e["metadata"]["golden_id"] for e in sent} == {"sc-1", "tr-1"}
    assert report["created"] == ["sc-1", "tr-1"]
    assert report["skipped"] == []


def test_sync_creates_examples_when_dataset_exists_but_empty():
    client = _FakeClient(dataset=_FakeDataset("ds-9"), existing=[])
    fixtures = [_fixture("sc-1")]

    report = dataset_sync.sync(client, fixtures)

    assert client.create_dataset_calls == []  # dataset already exists
    assert report["created"] == ["sc-1"]
    assert report["dataset_id"] == "ds-9"


# ---------------------------------------------------------------------------
# sync — idempotency
# ---------------------------------------------------------------------------


def test_sync_skips_already_present_golden_ids():
    client = _FakeClient(
        dataset=_FakeDataset("ds-7"),
        existing=[_FakeExample("sc-1"), _FakeExample("sc-2")],
    )
    fixtures = [_fixture("sc-1"), _fixture("sc-2"), _fixture("sc-3")]

    report = dataset_sync.sync(client, fixtures)

    assert report["skipped"] == ["sc-1", "sc-2"]
    assert report["created"] == ["sc-3"]
    sent = client.create_examples_calls[0]["examples"]
    assert {e["metadata"]["golden_id"] for e in sent} == {"sc-3"}


def test_sync_does_nothing_when_all_present():
    client = _FakeClient(
        dataset=_FakeDataset("ds-7"),
        existing=[_FakeExample("sc-1")],
    )
    fixtures = [_fixture("sc-1")]

    report = dataset_sync.sync(client, fixtures)

    assert report["created"] == []
    assert report["skipped"] == ["sc-1"]
    assert client.create_examples_calls == []


# ---------------------------------------------------------------------------
# sync — dry-run
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_writes_when_dataset_missing():
    client = _FakeClient(dataset=None, existing=[])
    report = dataset_sync.sync(client, [_fixture("sc-1")], dry_run=True)

    assert client.create_dataset_calls == []
    assert client.create_examples_calls == []
    assert report["created"] == ["sc-1"]
    assert report["dataset_id"] == "<dry-run>"


def test_dry_run_makes_no_writes_when_dataset_exists():
    client = _FakeClient(dataset=_FakeDataset("ds-1"), existing=[])
    report = dataset_sync.sync(client, [_fixture("sc-1")], dry_run=True)
    assert client.create_examples_calls == []
    assert report["created"] == ["sc-1"]


# ---------------------------------------------------------------------------
# load_fixtures — file system contract
# ---------------------------------------------------------------------------


def test_load_fixtures_reads_every_json(tmp_path: Path):
    (tmp_path / "a.json").write_text(json.dumps(_fixture("a")))
    (tmp_path / "b.json").write_text(json.dumps(_fixture("b")))
    (tmp_path / "skip.txt").write_text("ignore me")

    out = dataset_sync.load_fixtures(tmp_path)
    ids = sorted(f["metadata"]["id"] for f in out)
    assert ids == ["a", "b"]


def test_load_fixtures_raises_when_directory_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        dataset_sync.load_fixtures(tmp_path / "does-not-exist")

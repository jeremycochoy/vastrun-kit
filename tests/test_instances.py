from __future__ import annotations

import pytest

from vastrun_kit import errors, instances


def test_list_instances_calls_show_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    payload = [{"id": 1}, {"id": 2}]

    def fake(args, **kwargs):  # noqa: ANN001
        seen.append(args)
        return payload

    monkeypatch.setattr(instances.vastai_cli, "run_vastai_raw", fake)
    assert instances.list_instances() == payload
    assert seen == [["show", "instances"]]


def test_list_instances_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instances.vastai_cli, "run_vastai_raw", lambda args, **kw: [])
    assert instances.list_instances() == []


def test_find_instance_returns_match(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}, {"id": 3, "label": "c"}]
    monkeypatch.setattr(instances.vastai_cli, "run_vastai_raw", lambda args, **kw: rows)
    assert instances.find_instance(2) == {"id": 2, "label": "b"}


def test_find_instance_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(instances.vastai_cli, "run_vastai_raw", lambda args, **kw: rows)
    assert instances.find_instance(999) is None


def test_find_instance_propagates_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(args, **kwargs):  # noqa: ANN001
        raise errors.VastaiCliError("CLI exploded")

    monkeypatch.setattr(instances.vastai_cli, "run_vastai_raw", boom)
    with pytest.raises(errors.VastaiCliError, match="CLI exploded"):
        instances.find_instance(1)

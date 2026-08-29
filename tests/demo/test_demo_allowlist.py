"""Only tasks on the explicit demo allowlist may run — never a raw task_id.

``nav-tabs-secret-03`` is a REAL registry task but NOT on the demo allowlist
(hard, 12 steps): passing it must be rejected exactly like an unknown id.
"""

from __future__ import annotations

from tests.demo.conftest import make_demo_settings, valid_run_body


def test_registered_but_non_demo_task_is_rejected(
    tmp_path: object, make_client: object
) -> None:
    client = make_client(make_demo_settings(tmp_path))
    response = client.post("/demo/run", json=valid_run_body(task_id="nav-tabs-secret-03"))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "nav-tabs-secret-03" in detail
    assert "nav-simple-link-01" in detail  # points the caller at the allowlist


def test_completely_unknown_task_is_rejected(
    tmp_path: object, make_client: object
) -> None:
    client = make_client(make_demo_settings(tmp_path))
    response = client.post("/demo/run", json=valid_run_body(task_id="totally-bogus-task"))
    assert response.status_code == 400


def test_rejection_happens_before_any_run(
    tmp_path: object, make_client: object
) -> None:
    """The stubbed runner raises AssertionError if it is ever invoked."""
    client = make_client(make_demo_settings(tmp_path))
    response = client.post("/demo/run", json=valid_run_body(task_id="distractor-forms-03"))
    assert response.status_code == 400

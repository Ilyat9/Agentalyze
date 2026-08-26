"""POST /regression-check: same gate semantics as the CLI, HTTP-native status."""

from __future__ import annotations

from agentalyze.orchestration.suite_runner import save_suite_run
from agentalyze.regression.storage import set_baseline
from tests.api.conftest import make_fake_run_result


def _persist_run(settings, suite_run_id: str) -> str:  # type: ignore[no-untyped-def]
    result = make_fake_run_result(suite_run_id, settings)
    save_suite_run(result, settings.results_dir)
    return suite_run_id


class TestRegressionCheckEndpoint:
    def test_valid_comparison_is_200_with_gate_in_body(
        self, client, auth_headers, service_settings  # type: ignore[no-untyped-def]
    ) -> None:
        test_client, key = client
        baseline_id = _persist_run(service_settings, "suite-base-1")
        new_id = _persist_run(service_settings, "suite-new-1")
        set_baseline(service_settings.results_dir, baseline_id)

        response = test_client.post(
            "/regression-check",
            json={"new": new_id},
            headers=auth_headers(key),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # Identical (empty-trace) runs: nothing regressed.
        assert body["regressed"] is False
        assert body["gate_failed"] is False
        assert body["baseline_suite_run_id"] == baseline_id
        assert body["new_suite_run_id"] == new_id

    def test_unknown_run_is_404(
        self, client, auth_headers, service_settings  # type: ignore[no-untyped-def]
    ) -> None:
        test_client, key = client
        baseline_id = _persist_run(service_settings, "suite-base-2")
        set_baseline(service_settings.results_dir, baseline_id)
        response = test_client.post(
            "/regression-check",
            json={"new": "missing-suite-run"},
            headers=auth_headers(key),
        )
        assert response.status_code == 404

    def test_no_baseline_is_409(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, key = client
        response = test_client.post(
            "/regression-check",
            json={"new": "whatever"},
            headers=auth_headers(key),
        )
        assert response.status_code == 409

    def test_allow_regressions_flag_round_trips(
        self, client, auth_headers, service_settings  # type: ignore[no-untyped-def]
    ) -> None:
        test_client, key = client
        baseline_id = _persist_run(service_settings, "suite-base-3")
        new_id = _persist_run(service_settings, "suite-new-3")
        set_baseline(service_settings.results_dir, baseline_id)

        body = test_client.post(
            "/regression-check",
            json={"new": new_id, "allow_regressions": True},
            headers=auth_headers(key),
        ).json()
        assert body["allow_regressions"] is True

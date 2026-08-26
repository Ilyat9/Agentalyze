"""Authentication tests: Bearer keys hashed at rest, constant-time checks."""

from __future__ import annotations

from agentalyze.api.auth import (
    generate_api_key,
    hash_api_key,
    verify_api_key_hash,
)


class TestKeyHashing:
    def test_roundtrip(self) -> None:
        key = generate_api_key()
        stored = hash_api_key(key)
        assert verify_api_key_hash(key, stored)

    def test_wrong_key_rejected(self) -> None:
        stored = hash_api_key(generate_api_key())
        assert not verify_api_key_hash("agt-wrong", stored)

    def test_hash_is_salted(self) -> None:
        key = generate_api_key()
        assert hash_api_key(key) != hash_api_key(key)

    def test_stored_format_never_contains_plaintext(self) -> None:
        key = "agt-supersecret-value"
        stored = hash_api_key(key)
        assert key not in stored
        assert stored.startswith("scrypt$")

    def test_malformed_stored_hash_rejected(self) -> None:
        assert not verify_api_key_hash("k", "not-a-hash")
        assert not verify_api_key_hash("k", "md5$aa$bb")


class TestHttpAuth:
    def test_missing_header_is_401(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, _ = client
        response = test_client.post(
            "/runs", json={"provider_names": ["fake-provider"]}
        )
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    def test_bad_key_is_401_and_generic(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, _ = client
        response = test_client.post(
            "/runs",
            json={"provider_names": ["fake-provider"]},
            headers=auth_headers("agt-definitely-not-valid"),
        )
        assert response.status_code == 401
        # Must not leak which part failed.
        assert response.json()["detail"] == "Invalid API Key" or "invalid" in (
            response.json()["detail"].lower()
        )

    def test_valid_key_accepted(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, plaintext = client
        response = test_client.get("/runs", headers=auth_headers(plaintext))
        assert response.status_code == 200

    def test_health_endpoints_need_no_auth(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _ = client
        assert test_client.get("/livez").status_code == 200

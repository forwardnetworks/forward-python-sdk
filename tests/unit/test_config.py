"""Client configuration."""

from __future__ import annotations

import httpx
import pytest

from forward_sdk.config import (
    RetryPolicy,
    build_config,
    config_from_env,
    resolve_rate_limit,
    resolve_timeout,
)
from forward_sdk.errors import ForwardConfigurationError


class TestBaseUrl:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("https://fwd.app", "https://fwd.app/api"),
            ("https://fwd.app/", "https://fwd.app/api"),
            ("https://fwd.app/api", "https://fwd.app/api"),
            ("https://fwd.app/api/", "https://fwd.app/api"),
            ("  https://fwd.app  ", "https://fwd.app/api"),
        ],
    )
    def test_api_suffix_is_added_exactly_once(self, given: str, expected: str) -> None:
        assert build_config(given).base_url == expected

    def test_empty_base_url_is_rejected(self) -> None:
        with pytest.raises(ForwardConfigurationError, match="must not be empty"):
            build_config("   ")


class TestCredentials:
    def test_both_or_neither(self) -> None:
        with pytest.raises(ForwardConfigurationError, match="together"):
            build_config("https://fwd.app", username="only-user")
        with pytest.raises(ForwardConfigurationError, match="together"):
            build_config("https://fwd.app", password="only-secret")

    def test_auth_tuple(self) -> None:
        config = build_config("https://fwd.app", username="u", password="p")
        assert config.auth == ("u", "p")
        assert build_config("https://fwd.app").auth is None


class TestRateLimit:
    @pytest.mark.parametrize("url", ["https://fwd.app", "https://acme.fwd.app", "https://FWD.APP"])
    def test_hosted_service_is_paced_by_default(self, url: str) -> None:
        assert build_config(url).rate_limit_rpm == 1800

    def test_self_hosted_is_not_paced_by_default(self) -> None:
        """Self-hosted limits are a local matter, so the SDK does not invent one."""
        assert build_config("https://forward.example.com").rate_limit_rpm is None

    def test_explicit_values_win(self) -> None:
        assert build_config("https://fwd.app", rate_limit_rpm=60).rate_limit_rpm == 60
        assert build_config("https://fwd.app", rate_limit_rpm=None).rate_limit_rpm is None

    def test_non_positive_disables(self) -> None:
        assert resolve_rate_limit(0, is_saas=True) is None
        assert resolve_rate_limit(-5, is_saas=True) is None


class TestTimeouts:
    def test_scalar_becomes_a_read_deadline(self) -> None:
        timeout = resolve_timeout(120.0)
        assert timeout.read == 120.0
        # Connecting should not inherit a long read deadline.
        assert timeout.connect == 10.0

    def test_httpx_timeout_passes_through(self) -> None:
        given = httpx.Timeout(connect=1, read=2, write=3, pool=4)
        assert resolve_timeout(given) is given


class TestRetryPolicy:
    def test_count_becomes_attempts(self) -> None:
        """`retries=3` means three retries, so four attempts."""
        assert RetryPolicy.coerce(3).max_attempts == 4
        assert RetryPolicy.coerce(0).max_attempts == 1

    def test_policy_passes_through(self) -> None:
        policy = RetryPolicy(max_attempts=9)
        assert RetryPolicy.coerce(policy) is policy

    def test_invalid_values_rejected(self) -> None:
        with pytest.raises(ForwardConfigurationError, match="at least 1"):
            RetryPolicy(max_attempts=0)
        with pytest.raises(ForwardConfigurationError, match="positive"):
            RetryPolicy(backoff_base=0)


class TestEnvironment:
    def test_reads_forward_variables(self) -> None:
        settings = config_from_env(
            {
                "FORWARD_URL": "https://forward.test",
                "FORWARD_USERNAME": "key",
                "FORWARD_PASSWORD": "secret",
                "FORWARD_NETWORK_ID": "101",
                "FORWARD_VERIFY_TLS": "false",
                "FORWARD_TIMEOUT": "45",
                "FORWARD_RETRIES": "2",
                "FORWARD_RATE_LIMIT_RPM": "600",
            }
        )
        assert settings["base_url"] == "https://forward.test"
        assert settings["username"] == "key"
        assert settings["network_id"] == "101"
        assert settings["verify"] is False
        assert settings["timeout"] == 45.0
        assert settings["rate_limit_rpm"] == 600

    def test_missing_url_explains_what_to_set(self) -> None:
        with pytest.raises(ForwardConfigurationError, match="FORWARD_URL"):
            config_from_env({})

    def test_keyword_arguments_win(self) -> None:
        settings = config_from_env(
            {"FORWARD_URL": "https://from-env", "FORWARD_USERNAME": "env-user"},
            base_url="https://explicit",
            username="explicit-user",
        )
        assert settings["base_url"] == "https://explicit"
        assert settings["username"] == "explicit-user"

    def test_blank_values_are_treated_as_unset(self) -> None:
        settings = config_from_env(
            {"FORWARD_URL": "https://forward.test", "FORWARD_USERNAME": "   "}
        )
        assert settings["username"] is None

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
    def test_verify_tls_falsey_values(self, value: str) -> None:
        settings = config_from_env({"FORWARD_URL": "https://x", "FORWARD_VERIFY_TLS": value})
        assert settings["verify"] is False


def test_user_agent_identifies_the_sdk_and_the_application() -> None:
    config = build_config("https://fwd.app", user_agent="my-app/2.0")
    agent = config.full_user_agent()
    assert agent.startswith("forward-sdk/")
    assert "python-httpx/" in agent
    assert agent.endswith("my-app/2.0")

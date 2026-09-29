"""Client configuration.

Configuration is resolved once, at client construction, into an immutable
:class:`ClientConfig`. Nothing here talks to the network: the SDK never probes a
Forward instance to discover its version, licence or capabilities, because doing
so would add a mandatory round trip and still could not answer the question
reliably. See ``docs/gating.md``.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from forward_sdk._http import normalize_base_url
from forward_sdk._version import __version__
from forward_sdk.errors import ForwardConfigurationError

__all__ = ["ClientConfig", "RetryPolicy"]

#: Forward's hosted service. Used only to pick a default request rate.
SAAS_HOST_SUFFIX = ".fwd.app"
SAAS_HOSTS = frozenset({"fwd.app"})

#: Forward counts requests per authenticated user per minute, in a sliding
#: window, and the ceiling is an org setting (``MAX_API_REQ_PER_USER_PER_MIN``)
#: whose default is 2000 and whose range is 1 to 10,000. Exceeding it does not
#: delay a request, it blocks the user for a configurable lockout, default one
#: minute and up to sixty, answered with 429 and a ``Retry-After`` carrying the
#: remaining block. So the cost of overshooting is not a slow sync but a stalled
#: one, and it is worth staying well under.
#:
#: The budget belongs to the user, not to a client or a host, so every process
#: authenticating as the same user spends from one allowance. A single client's
#: pacing cannot protect a fleet; see the ``throttle=`` hook.
SAAS_HARD_LIMIT_RPM = 2000
SAAS_DEFAULT_RPM = 1800

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 300.0
#: NQE result streams can pause between chunks while the server works.
DEFAULT_STREAM_READ_TIMEOUT = 600.0

ENV_PREFIX = "FORWARD_"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How the transport retries a failed request.

    Attributes:
        max_attempts: Total sends, including the first. ``1`` disables retries.
        backoff_base: Starting delay in seconds for exponential backoff.
        backoff_cap: Upper bound on the computed backoff, before jitter.
        max_retry_after: Upper bound on an honoured ``Retry-After``, so an
            unreasonable server value cannot hang a caller.
    """

    max_attempts: int = 4
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    max_retry_after: float = 120.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ForwardConfigurationError("max_attempts must be at least 1")
        if self.backoff_base <= 0 or self.backoff_cap <= 0:
            raise ForwardConfigurationError("backoff values must be positive")

    @classmethod
    def coerce(cls, value: RetryPolicy | int | None) -> RetryPolicy:
        """Accept a policy, a plain retry count, or ``None`` for the default."""
        if value is None:
            return cls()
        if isinstance(value, RetryPolicy):
            return value
        return cls(max_attempts=int(value) + 1)


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Resolved settings for one client.

    Attributes:
        base_url: API root, always ending in ``/api``.
        username: API token access key, or a login name.
        password: API token secret, or a password.
        verify: TLS verification: ``True`` (the system trust store), ``False``
            (disabled), or the path to a CA bundle file or directory for a
            deployment whose certificate chains to a private root.
        timeout: Per-request timeouts.
        stream_read_timeout: Read timeout for streaming responses.
        retries: Retry policy.
        rate_limit_rpm: Client-side pacing in requests per minute, or ``None``.
        network_id: Default network for calls that omit one.
        snapshot_id: Default snapshot for calls that omit one. ``None`` means
            Forward uses the network's latest processed snapshot.
        user_agent: Extra token identifying the calling application.
        cache_ttl: Seconds to reuse cached reads, or ``0`` to disable.
        snapshot_cache_ttl: How long to reuse a resolved snapshot. ``0``, the
            default, resolves every time. ``"lifetime"`` resolves once and
            keeps that answer for as long as the client lives, which is what a
            run holding one point in time wants. A number of seconds suits
            repeated independent reads, and is the wrong shape for a pinned
            run: expiring mid-run lets the next resolution return a different
            snapshot, so the run straddles two moments with nothing raising.
    """

    base_url: str
    username: str | None = None
    # Kept out of repr: a config object lands in tracebacks, logs and debug
    # dumps, and a credential must not travel with it.
    password: str | None = field(default=None, repr=False)
    verify: bool | str = True
    timeout: httpx.Timeout = field(
        default_factory=lambda: httpx.Timeout(
            connect=DEFAULT_CONNECT_TIMEOUT,
            read=DEFAULT_READ_TIMEOUT,
            write=DEFAULT_READ_TIMEOUT,
            pool=DEFAULT_CONNECT_TIMEOUT,
        )
    )
    stream_read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    rate_limit_rpm: int | None = None
    network_id: str | None = None
    snapshot_id: str | None = None
    user_agent: str | None = None
    cache_ttl: float = 60.0
    snapshot_cache_ttl: float | Literal["lifetime"] = 0.0
    proxy: str | None = None
    trust_env: bool = True

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def is_saas(self) -> bool:
        """Whether this points at Forward's hosted service."""
        host = self.host.lower()
        return host in SAAS_HOSTS or host.endswith(SAAS_HOST_SUFFIX)

    @property
    def auth(self) -> tuple[str, str] | None:
        """Basic-auth credentials, or ``None`` when unauthenticated."""
        if self.username is None or self.password is None:
            return None
        return (self.username, self.password)

    def full_user_agent(self, httpx_version: str = httpx.__version__) -> str:
        base = f"forward-sdk/{__version__} python-httpx/{httpx_version}"
        return f"{base} {self.user_agent}" if self.user_agent else base

    def with_overrides(self, **changes: Any) -> ClientConfig:
        return replace(self, **changes)


def resolve_timeout(timeout: httpx.Timeout | float | None) -> httpx.Timeout:
    """Accept a full timeout, a single read deadline, or ``None``."""
    if timeout is None:
        return httpx.Timeout(
            connect=DEFAULT_CONNECT_TIMEOUT,
            read=DEFAULT_READ_TIMEOUT,
            write=DEFAULT_READ_TIMEOUT,
            pool=DEFAULT_CONNECT_TIMEOUT,
        )
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(
        connect=min(DEFAULT_CONNECT_TIMEOUT, float(timeout)),
        read=float(timeout),
        write=float(timeout),
        pool=DEFAULT_CONNECT_TIMEOUT,
    )


def resolve_rate_limit(
    rate_limit_rpm: int | Literal["auto"] | None, *, is_saas: bool
) -> int | None:
    """Resolve the request rate, defaulting only for Forward's hosted service.

    ``"auto"`` paces requests on ``fwd.app`` below the 2000 per minute per user
    that Forward's default org setting allows, and leaves self-hosted
    deployments alone, where the limiter ships disabled and an administrator
    decides whether to turn it on and at what number.

    The 1800 is a heuristic, not a reading of the server: the limit is an org
    setting the SDK cannot query, and it is spent by every process using the
    same credentials. Treat it as headroom, not as a guarantee.
    """
    if rate_limit_rpm == "auto":
        return SAAS_DEFAULT_RPM if is_saas else None
    if rate_limit_rpm is None:
        return None
    value = int(rate_limit_rpm)
    if value <= 0:
        return None
    return value


def _env(name: str, environ: dict[str, str]) -> str | None:
    value = environ.get(ENV_PREFIX + name)
    return value.strip() if value and value.strip() else None


_FALSE = frozenset({"0", "false", "no", "off"})
_TRUE = frozenset({"1", "true", "yes", "on"})


def _env_verify(environ: dict[str, str]) -> bool | str:
    """``FORWARD_VERIFY_TLS``: a boolean, or the path to a CA bundle.

    A self-hosted deployment usually presents a certificate from a private CA.
    The fix is to trust that CA, not to turn verification off, so the variable
    also takes a path. Anything that is neither a recognised boolean nor an
    existing path is refused: reading ``/etc/fwd/ca.pem`` with a typo as "true"
    would silently verify against the wrong trust store.
    """
    raw = _env("VERIFY_TLS", environ)
    if raw is None or raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    return _ca_path(raw, source="FORWARD_VERIFY_TLS")


def _ca_path(value: str, *, source: str) -> str:
    path = os.path.expanduser(value)
    if not os.path.exists(path):
        expected = "true/false or " if source.startswith("FORWARD_") else ""
        raise ForwardConfigurationError(
            f"{source}={value!r} is not {expected}an existing CA bundle file or directory"
        )
    return path


def tls_verify(verify: bool | str | ssl.SSLContext) -> bool | ssl.SSLContext:
    """Turn a ``verify`` setting into what ``httpx`` accepts without a deprecation.

    httpx 0.28 deprecates a string ``verify``; a CA path becomes an
    :class:`ssl.SSLContext` here, once, so callers can keep passing a path.
    """
    if isinstance(verify, (bool, ssl.SSLContext)):
        return verify
    if os.path.isdir(verify):
        return ssl.create_default_context(capath=verify)
    return ssl.create_default_context(cafile=verify)


def config_from_env(environ: dict[str, str] | None = None, **overrides: Any) -> dict[str, Any]:
    """Read client settings from ``FORWARD_*`` environment variables.

    Explicit keyword arguments always win over the environment. Variable names
    match those already used by the Forward NetBox and Nautobot plugins, so an
    existing deployment's environment works unchanged.
    """
    environ = dict(os.environ if environ is None else environ)

    base_url = overrides.pop("base_url", None) or _env("URL", environ)
    if not base_url:
        raise ForwardConfigurationError(
            "no Forward URL: pass base_url or set FORWARD_URL (for example https://fwd.app)"
        )

    settings: dict[str, Any] = {
        "base_url": base_url,
        "username": _env("USERNAME", environ),
        "password": _env("PASSWORD", environ),
        "network_id": _env("NETWORK_ID", environ),
        "snapshot_id": _env("SNAPSHOT_ID", environ),
        "user_agent": _env("USER_AGENT", environ),
        "verify": _env_verify(environ),
    }

    timeout = _env("TIMEOUT", environ)
    if timeout:
        settings["timeout"] = float(timeout)
    rpm = _env("RATE_LIMIT_RPM", environ)
    if rpm:
        settings["rate_limit_rpm"] = int(rpm)
    retries = _env("RETRIES", environ)
    if retries:
        settings["retries"] = int(retries)
    snapshot_ttl = _env("SNAPSHOT_CACHE_TTL", environ)
    if snapshot_ttl:
        settings["snapshot_cache_ttl"] = (
            "lifetime" if snapshot_ttl.lower() == "lifetime" else float(snapshot_ttl)
        )

    settings.update(overrides)
    return settings


def build_config(
    base_url: str,
    *,
    username: str | None = None,
    password: str | None = None,
    verify: bool | str = True,
    timeout: httpx.Timeout | float | None = None,
    retries: RetryPolicy | int | None = None,
    rate_limit_rpm: int | Literal["auto"] | None = "auto",
    network_id: str | None = None,
    snapshot_id: str | None = None,
    user_agent: str | None = None,
    cache_ttl: float = 60.0,
    snapshot_cache_ttl: float | Literal["lifetime"] = 0.0,
    proxy: str | None = None,
    trust_env: bool = True,
    stream_read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT,
) -> ClientConfig:
    """Validate and normalize constructor arguments into a :class:`ClientConfig`."""
    try:
        normalized = normalize_base_url(base_url)
    except ValueError as exc:
        raise ForwardConfigurationError(str(exc)) from exc

    if (username is None) != (password is None):
        raise ForwardConfigurationError(
            "username and password must be provided together "
            "(use an API token's access key and secret)"
        )
    if isinstance(verify, str):
        verify = _ca_path(verify, source="verify")

    config = ClientConfig(
        base_url=normalized,
        username=username,
        password=password,
        verify=verify,
        timeout=resolve_timeout(timeout),
        stream_read_timeout=stream_read_timeout,
        retries=RetryPolicy.coerce(retries),
        rate_limit_rpm=None,
        network_id=network_id,
        snapshot_id=snapshot_id,
        user_agent=user_agent,
        cache_ttl=max(0.0, cache_ttl),
        snapshot_cache_ttl=(
            "lifetime" if snapshot_cache_ttl == "lifetime" else max(0.0, float(snapshot_cache_ttl))
        ),
        proxy=proxy,
        trust_env=trust_env,
    )
    return config.with_overrides(
        rate_limit_rpm=resolve_rate_limit(rate_limit_rpm, is_saas=config.is_saas)
    )

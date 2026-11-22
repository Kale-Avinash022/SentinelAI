"""Allowlisted provider destinations — the SSRF/egress guard (Day 016).

A chat caller must never be able to point the gateway at an arbitrary URL:
that is the classic SSRF-with-an-LLM-SDK failure, where a "base_url" knob
turns the server into an authenticated proxy for whoever can send a chat
message. So the only destinations the gateway can ever reach are fixed at
build time by this module, from configuration an operator owns:

* **Exact ``provider/model`` pairs.** Request bodies choose *among* allowlisted
  models, never *outside* them; an unknown pin is refused before any SDK call.
* **Scheme-checked base URLs.** Real vendor endpoints must be ``https``; a
  plain-``http`` endpoint is accepted only when its host is loopback, because
  the brief's optional "local model" runs on the same machine. Credentials
  embedded in a URL (``https://user:***@host``) are refused outright.
* **Configuration-only input.** The parser takes values from ``Settings`` and
  fixtures — nothing downstream re-parses a caller's text into a destination.

The functions are pure and synchronous: no socket is opened here, in step with
the rest of ``app.gateway`` where building a registry is a network-free
operation (proven by the Day 015 import-time rules).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

#: Hosts accepted for plain-``http`` local model endpoints (Ollama, vLLM, …).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})

_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class ProviderDestination:
    """One allowlisted ``(provider, model)`` pair and the endpoint it may use.

    ``base_url`` of ``None`` means "the vendor SDK's own official endpoint" —
    which is permitted without listing because it is the whole reason LiteLLM
    exists. Any *other* endpoint must be spelled out and scheme-checked.
    """

    provider: str
    model: str
    base_url: str | None = None


class DestinationNotAllowedError(ValueError):
    """Raised when a destination fails the allowlist or SSRF shape checks.

    A ``ValueError``, because at build time this is misconfiguration and the
    settings validators surface it as startup failure; the adapter converts it
    into a typed ``FORBIDDEN`` provider failure at call time.
    """


def normalize_base_url(url: str) -> str:
    """Validate and canonicalize one endpoint URL against the SSRF rules.

    Returns the lowercased ``scheme://netloc/path`` form (query/fragment
    stripped — an endpoint carrying query params is not a shape we proxy).
    """
    candidate = url.strip()
    if not candidate:
        raise DestinationNotAllowedError("Base URL must not be empty.")
    parts = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise DestinationNotAllowedError(f"Base URL scheme {scheme!r} is not http or https.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise DestinationNotAllowedError("Base URL must name a host.")
    if parts.username or parts.password:
        raise DestinationNotAllowedError("Base URLs must never embed credentials.")
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        raise DestinationNotAllowedError(
            "Plain-http endpoints are only allowed on loopback hosts (SSRF guard)."
        )
    path = parts.path.rstrip("/")
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()
    return f"{scheme}://{netloc}{path}"


def is_loopback_base_url(url: str) -> bool:
    """True when ``url`` is a valid loopback endpoint (after normalization)."""
    try:
        normalized = normalize_base_url(url)
    except DestinationNotAllowedError:
        return False
    host = urlsplit(normalized).hostname or ""
    return host in LOOPBACK_HOSTS


def parse_destination(entry: str) -> ProviderDestination:
    """Parse one ``"provider/model[@base_url]"`` allowlist entry.

    The *right-most* slash separates provider from model, because model ids
    may themselves contain slashes (``anthropic/claude-x`` under LiteLLM's
    own syntax is ``provider/model``, never ambiguous here).
    """
    text = entry.strip()
    base_url: str | None = None
    if "@" in text:
        text, _, raw_url = text.partition("@")
        base_url = normalize_base_url(raw_url)
    provider, sep, model = text.rpartition("/")
    if not sep or not provider.strip() or not model.strip():
        raise DestinationNotAllowedError(
            f"Destination {entry!r} must look like 'provider/model[@base_url]'."
        )
    return ProviderDestination(
        provider=provider.strip().lower(), model=model.strip(), base_url=base_url
    )


class DestinationAllowlist:
    """The closed set of destinations the gateway may ever contact."""

    def __init__(self, entries: tuple[ProviderDestination, ...]) -> None:
        self._entries = {(e.provider, e.model): e for e in entries}

    def allows(self, provider: str, model: str, base_url: str | None = None) -> bool:
        """True when this exact triple is allowlisted (``None`` base matches
        the vendor-official default, not an override)."""
        entry = self._entries.get((provider.lower(), model))
        if entry is None:
            return False
        return entry.base_url == base_url

    def require(self, provider: str, model: str, base_url: str | None = None) -> None:
        """Raise :class:`DestinationNotAllowedError` unless the triple is known."""
        if not self.allows(provider, model, base_url):
            # Message names the request, never the allowlist contents.
            raise DestinationNotAllowedError(
                f"Destination {provider!r} model {model!r} is not in the configured allowlist."
            )

    @property
    def size(self) -> int:
        return len(self._entries)


def build_allowlist(entries: list[str]) -> DestinationAllowlist:
    """Parse settings-style ``"provider/model[@base_url]"`` strings.

    Duplicate pairs collapse (last wins); malformed entries raise at startup,
    not at the first chat call.
    """
    parsed: list[ProviderDestination] = []
    for raw in entries:
        if not raw.strip():
            continue
        parsed.append(parse_destination(raw))
    return DestinationAllowlist(tuple(parsed))


__all__ = [
    "LOOPBACK_HOSTS",
    "DestinationAllowlist",
    "DestinationNotAllowedError",
    "ProviderDestination",
    "build_allowlist",
    "is_loopback_base_url",
    "normalize_base_url",
    "parse_destination",
]

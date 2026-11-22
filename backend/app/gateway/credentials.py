"""Provider credential resolution through a secret adapter (Day 016).

The step rule is absolute: provider keys are *never* taken from a chat
request and never stored as plaintext configuration. They live as
**references** — the name of an environment variable whose value is either a
raw key (development convenience) or a payload sealed by the Day 014
:class:`~app.configs.vault.FernetVault` (``fernet:v1:...``), so a deployment
can keep vendor keys inside the same envelope vault as prompt bodies.

The adapter boundary sees a resolved ``SecretStr`` for exactly one call and
nothing else: it is never placed on :class:`CompletionRequest`, never logged,
and never reachable from the wire. ``EnvironmentCredentialResolver`` reads
``os.environ`` lazily, so importing the gateway stays network- and
secret-free (the Day 006 rule tests still enforce).
"""

from __future__ import annotations

import os
from typing import Protocol

from pydantic import SecretStr

from app.configs.vault import FernetVault, is_sealed


class CredentialNotConfiguredError(RuntimeError):
    """A configured provider names a credential that is not available.

    Deliberately opaque — the message never guesses *why* (missing variable
    versus wrong vault key are operator diagnostics, not caller ones),
    mirroring :class:`~app.configs.vault.VaultError`.
    """


class CredentialResolver(Protocol):
    """The secret-adapter seam the provider builders depend on."""

    def resolve(self, reference: str) -> SecretStr:
        """Return the secret a reference points at, or raise.

        Contract: implementations must raise
        :class:`CredentialNotConfiguredError` — never return ``None`` and
        never echo the secret's value into the error text.
        """
        ...


class EnvironmentCredentialResolver:
    """Resolve references against the process environment via the vault.

    A reference is an environment-variable *name* (e.g. ``OPENAI_API_KEY``).
    Its value is returned as-is when it is a plain key, and unsealed through
    the shared :class:`~app.configs.vault.FernetVault` when it carries the
    ``fernet:v1:`` envelope marker — so the same configuration shape works
    for raw dev keys and vault-wrapped production secrets.
    """

    def __init__(self, *, vault: FernetVault | None = None, env: dict[str, str] | None = None):
        self._vault = vault
        self._env = env if env is not None else os.environ

    def resolve(self, reference: str) -> SecretStr:
        name = reference.strip()
        if not name:
            raise CredentialNotConfiguredError("Credential reference must not be empty.")
        value = self._env.get(name)
        if value is None or not value.strip():
            raise CredentialNotConfiguredError(
                f"Credential {name!r} is configured but not present in the environment."
            )
        if is_sealed(value):
            value = self._unseal(name, value)
        return SecretStr(value)

    def _unseal(self, reference: str, sealed: str) -> str:
        if self._vault is None:
            raise CredentialNotConfiguredError(
                f"Credential {reference!r} is vault-sealed but no vault is configured."
            )
        try:
            # VaultError (wrong key, tampering, corruption) is opaque already;
            # catching its RuntimeError base keeps this boundary future-proof.
            return self._vault.open(sealed)
        except RuntimeError as exc:
            raise CredentialNotConfiguredError(
                f"Credential {reference!r} could not be resolved."
            ) from exc


__all__ = [
    "CredentialNotConfiguredError",
    "CredentialResolver",
    "EnvironmentCredentialResolver",
]

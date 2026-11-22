"""Working LLM gateway module (Week 3).

Normalized chat contracts, the provider port, deterministic fake adapters,
the LiteLLM vendor adapter with its destination allowlist and credential
secret adapter, and the gateway service that wires them. Business logic
speaks only to the closed types in :mod:`app.gateway.contracts`; vendor
response shapes are translated once, at each adapter boundary.
"""

from __future__ import annotations

"""Provider adapter implementations (Day 016).

Subpackage for anything that speaks a vendor's language: the LiteLLM adapter
lives here so the rest of ``app.gateway`` keeps only normalized, vendor-free
types. Adapters must implement the :class:`~app.gateway.contracts.ChatProvider`
port and translate in both directions — nothing outside this package may
import an SDK.
"""

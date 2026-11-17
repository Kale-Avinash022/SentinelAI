"""Security regression tests: cross-tenant isolation as a CI invariant (Day 011).

These are *adversarial* integration tests. They connect as the real non-owner
API role ``sentinel_app`` (``NOBYPASSRLS``) against a live PostgreSQL and try to
leak one organization's data into another through the paths that break in
practice — forgotten filters, direct object references, forged
``organization_id`` bodies, nested foreign keys, missing context, and pooled
connection reuse. A pass here means the *database* refuses the leak, not that a
handler remembered to filter.
"""

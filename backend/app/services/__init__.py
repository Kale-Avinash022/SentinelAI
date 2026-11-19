"""Domain services for the Day 013 credential-lifecycle module.

Thin by design: they hold the *transactional* logic (store writes plus their
audit entries) so API route handlers stay parameter-mapping only, mirroring
how :mod:`app.auth.repository` keeps Day 012's endpoint layer honest.
"""

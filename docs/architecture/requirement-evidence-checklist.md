# SentinelAI — Requirement-to-Evidence Verification Checklist

> **Status:** Approved Baseline  
> **Document:** `docs/architecture/requirement-evidence-checklist.md`  
> **Version:** 1.0.0 (Day 001)

---

## 1. Traceability Matrix

Every requirement stated in the SentinelAI product brief is mapped to specific implementation days, verifiable acceptance criteria, and local testing methods.

| Core Requirement | Key Days | Local Test Strategy (Zero Cloud Cost) | Required Empirical Evidence |
|---|---|---|---|
| **Modular Monolith & Module Boundaries** | Days 01–07 | Clean pytest suite, no circular dependencies, modular directory layout. | Test execution logs, ADR-0001 sign-off. |
| **Multi-Tenant Isolation via PostgreSQL RLS** | Days 08–11 | Negative test suite asserting 0 rows returned or 403 Forbidden across pooled sessions without tenant context. | `backend/tests/security/test_tenant_isolation.py` run report. |
| **Authentication, JWT & Scoped API Keys** | Days 12–13 | Unit tests on password hashing (Argon2), token signing, and constant-time API key verification. | Auth unit tests passing with 100% coverage on security primitives. |
| **Deterministic LLM Gateway & Idempotency** | Days 15–21 | Integration tests with deterministic mock providers generating synthetic timeouts and billing edge cases. | Idempotency replay tests asserting no duplicate payment charge or replay. |
| **Transactional Outbox & Celery Workers** | Days 22–28 | Fault injection: disconnect Redis broker during active gateway traffic; verify outbox persists and resumes cleanly. | Chaos test log demonstrating 0 dropped events across queue crash. |
| **Latency, Error & Cost Aggregations** | Days 29–31 | Seed 1,000 synthetic gateway events; execute worker rollup; verify SQL aggregates match raw sums. | Math verification assertions in pytest. |
| **React TypeScript Control Dashboard** | Days 32–35 | Vitest component tests + local browser automated verification script. | Successful Vite production build (`npm run build`) and screenshot evidence. |
| **Risk Detection & Deduplicating Incidents** | Days 36–42 | Feed synthetic latency/error/cost anomaly fixtures into detector pipeline; verify single deduplicated incident created. | Incident generation and state machine test reports. |
| **AI Quality Evaluation & 200-Case Benchmark** | Days 43–49 | Execute evaluation benchmark against version-locked 200-case dataset; calculate Cohen's Kappa calibration score. | Calibration report with calculated agreement metrics. |
| **Fenced Recovery & A/B/C Policy Gates** | Days 50–56 | Simulated split-brain test: attempt concurrent failover executions with stale fencing token. | Fencing rejection assertion: HTTP 409 Conflict / stale token error. |
| **Reliability, Security Scanners & Chaos Matrix** | Days 57–63 | Run Bandit, Semgrep, pip-audit locally; run full provider failure matrix. | Clean scanner receipts (0 High/Critical findings) and failure matrix matrix logs. |
| **Locust Load & Scalability Benchmarks** | Days 64–70 | Run Locust headless against local Docker stack up to 200 RPS; generate latency distributions. | CSV and HTML benchmark reports detailing actual p50/p95/p99 latency. |
| **Cost-Conscious AWS Infrastructure** | Days 71–77 | Terraform syntax/plan validation + optional short-lived synthetic test under $20 budget cap. | `terraform plan` output and verified teardown receipt. |
| **Release Acceptance & Quickstart Verification** | Days 78–84 | Execute quickstart from scratch in isolated test directory; run synthetic demo. | Successful bootstrap report from clean clone in $< 5\text{ minutes}$. |
| **Optional Advanced Labs (Kafka, LangGraph, K8s)** | Days 85–90 | Run isolated Docker/kind lab experiments; document comparative findings. | Post-release ADRs comparing operational trade-offs. |


"""Unit tests validating Docker Compose specification, safety constraints, and DB bootstrap scripts."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
INIT_ROLES_SCRIPT = REPO_ROOT / "infra" / "docker" / "postgres" / "init-roles.sh"


def test_docker_compose_file_exists_and_parses() -> None:
    """Ensure docker-compose.yml exists and parses as valid YAML."""
    assert COMPOSE_FILE.is_file(), f"docker-compose.yml not found at {COMPOSE_FILE}"
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    assert isinstance(config, dict)
    assert "services" in config
    assert "volumes" in config


def test_core_profile_and_services_defined() -> None:
    """Verify postgres and redis services exist under the core profile."""
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    services = config["services"]
    assert "postgres" in services
    assert "redis" in services

    for svc_name in ["postgres", "redis"]:
        svc = services[svc_name]
        assert "profiles" in svc
        assert "core" in svc["profiles"], f"{svc_name} missing 'core' profile"


def test_ports_bound_to_localhost_only() -> None:
    """Verify ports are bound strictly to 127.0.0.1 (not 0.0.0.0 or LAN)."""
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for svc_name in ["postgres", "redis"]:
        ports = config["services"][svc_name].get("ports", [])
        assert len(ports) > 0, f"{svc_name} has no port bindings"
        for p in ports:
            assert p.startswith("127.0.0.1:"), f"{svc_name} port {p} is not restricted to 127.0.0.1"


def test_named_volumes_used_for_data_persistence() -> None:
    """Verify data persistence uses named Docker volumes rather than host bind mounts."""
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    declared_volumes = config.get("volumes", {})
    assert "postgres_data" in declared_volumes
    assert "redis_data" in declared_volumes

    # Verify services mount the named volumes
    pg_vols = config["services"]["postgres"]["volumes"]
    redis_vols = config["services"]["redis"]["volumes"]

    assert any(v.startswith("postgres_data:") for v in pg_vols), (
        "PostgreSQL must mount named volume 'postgres_data'"
    )
    assert any(v.startswith("redis_data:") for v in redis_vols), (
        "Redis must mount named volume 'redis_data'"
    )


def test_healthchecks_and_resource_limits_configured() -> None:
    """Verify healthchecks and deploy resource limits are present for container reliability."""
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for svc_name in ["postgres", "redis"]:
        svc = config["services"][svc_name]
        assert "healthcheck" in svc, f"{svc_name} missing healthcheck"
        assert "test" in svc["healthcheck"]
        assert "deploy" in svc, f"{svc_name} missing deploy configuration"
        assert "resources" in svc["deploy"]
        assert "limits" in svc["deploy"]["resources"]


def test_database_bootstrap_script_security_and_roles() -> None:
    """Verify the PostgreSQL role bootstrap script enforces least-privilege and RLS safety."""
    assert INIT_ROLES_SCRIPT.is_file(), f"Init roles script not found at {INIT_ROLES_SCRIPT}"
    content = INIT_ROLES_SCRIPT.read_text(encoding="utf-8")

    # Roles presence
    assert "sentinel_migrator" in content
    assert "sentinel_app" in content

    # Security controls: sentinel_migrator has schema ownership, but NOSUPERUSER and NOBYPASSRLS
    assert "NOSUPERUSER" in content
    assert "NOBYPASSRLS" in content

    # sentinel_app must NOT have SUPERUSER, CREATEDB, CREATEROLE, or BYPASSRLS
    assert "NOCREATEDB" in content
    assert "NOCREATEROLE" in content

    # Ownership and privilege revocation
    assert "ALTER SCHEMA public OWNER TO" in content
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in content
    assert "REVOKE CREATE ON SCHEMA public FROM" in content

    # DML grants
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO" in content

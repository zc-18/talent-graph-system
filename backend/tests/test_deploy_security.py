import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_container_build_never_copies_environment_secrets():
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text("utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text("utf-8")
    assert "COPY backend/.env" not in dockerfile
    assert "**/.env" in dockerignore
    assert "backend/*.db" in dockerignore


def test_production_launchers_force_read_only_and_external_env_file():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text("utf-8")
    service = (ROOT / "deploy" / "talent-graph.service").read_text("utf-8")
    for content in (compose, service):
        assert "APP_ENV=production" in content
        assert "READ_ONLY=1" in content
        assert "/etc/talent-graph/talent-graph.env" in content


def test_uni_app_is_the_only_mobile_project():
    frontend = ROOT / "frontend"
    package = json.loads((frontend / "package.json").read_text("utf-8"))
    dependency_names = set(package.get("dependencies", {})) | set(
        package.get("devDependencies", {}))

    assert not (frontend / "android").exists()
    assert not (frontend / "capacitor.config.ts").exists()
    assert not (frontend / ".env.android").exists()
    assert not any(name.startswith("android:") for name in package.get("scripts", {}))
    assert not any(name.startswith("@capacitor/") for name in dependency_names)
    assert "capacitor-secure-storage-plugin" not in dependency_names

    app_root = ROOT.parent / "app"
    assert (app_root / "manifest.json").is_file()
    assert (app_root / "pages.json").is_file()

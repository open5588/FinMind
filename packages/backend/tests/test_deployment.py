import os
from pathlib import Path
import subprocess
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_dockerfiles_and_compose_have_healthchecks():
    backend_dockerfile = (REPO_ROOT / "packages/backend/Dockerfile").read_text(
        encoding="utf-8"
    )
    frontend_dockerfile = (REPO_ROOT / "app/Dockerfile").read_text(encoding="utf-8")
    compose_raw = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "HEALTHCHECK" in backend_dockerfile
    assert "/health" in backend_dockerfile
    assert "HEALTHCHECK" in frontend_dockerfile

    compose = yaml.safe_load(compose_raw)
    services = compose.get("services", {})
    for svc in ("postgres", "redis", "backend", "frontend"):
        assert svc in services, f"Missing service {svc} in docker-compose.yml"
        assert "healthcheck" in services[svc], f"Missing healthcheck on {svc}"


def test_helm_chart_contains_hpa_pdb_networkpolicy_and_ingress():
    chart_yaml = yaml.safe_load(
        (REPO_ROOT / "helm/Chart.yaml").read_text(encoding="utf-8")
    )
    values_yaml = yaml.safe_load(
        (REPO_ROOT / "helm/values.yaml").read_text(encoding="utf-8")
    )
    assert chart_yaml["name"] == "finmind"
    assert values_yaml["backend"]["hpa"]["enabled"] is True
    assert values_yaml["backend"]["pdb"]["enabled"] is True
    assert values_yaml["networkPolicy"]["enabled"] is True
    assert values_yaml["ingress"]["enabled"] is True

    hpa_tpl = (REPO_ROOT / "helm/templates/hpa.yaml").read_text(encoding="utf-8")
    pdb_tpl = (REPO_ROOT / "helm/templates/pdb.yaml").read_text(encoding="utf-8")
    netpol_tpl = (REPO_ROOT / "helm/templates/networkpolicy.yaml").read_text(
        encoding="utf-8"
    )
    ingress_tpl = (REPO_ROOT / "helm/templates/ingress.yaml").read_text(
        encoding="utf-8"
    )

    assert "HorizontalPodAutoscaler" in hpa_tpl
    assert "PodDisruptionBudget" in pdb_tpl
    assert "NetworkPolicy" in netpol_tpl
    assert "kind: Ingress" in ingress_tpl
    assert "tls:" in ingress_tpl


def test_cloud_configs_and_tiltfile_present():
    tiltfile = (REPO_ROOT / "Tiltfile").read_text(encoding="utf-8")
    railway = (REPO_ROOT / "railway.toml").read_text(encoding="utf-8")
    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    fly = (REPO_ROOT / "fly.toml").read_text(encoding="utf-8")

    assert "docker_build" in tiltfile
    assert "k8s_resource" in tiltfile
    assert "healthcheckPath" in railway
    assert "services" in render and "databases" in render
    assert "/health" in fly


def test_deploy_sh_is_executable_and_passes_preflight():
    deploy_script = REPO_ROOT / "deploy.sh"
    assert deploy_script.exists()
    assert os.access(deploy_script, os.X_OK)

    result = subprocess.run(
        [str(deploy_script), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"deploy.sh --check failed: {result.stderr}"
    assert "All preflight checks passed" in result.stdout

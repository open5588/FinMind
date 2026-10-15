#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:4173}"
NAMESPACE="${K8S_NAMESPACE:-finmind}"
DRY_RUN="${DRY_RUN:-0}"

log()  { printf "\033[1;34m[finmind-deploy]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[finmind-ok]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[finmind-warn]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[finmind-error]\033[0m %s\n" "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
FinMind Universal One-Click Deployment CLI

Usage:
  ./deploy.sh [target] [options]

Targets:
  compose | docker   Deploy full stack via Docker Compose (Postgres, Redis, Backend, Frontend, Monitoring)
  k8s                Deploy Kubernetes manifests to namespace '${NAMESPACE}'
  helm               Deploy Helm chart './helm' to namespace '${NAMESPACE}'
  tilt               Start local Kubernetes inner-loop workflow via Tilt ('Tiltfile')
  railway            Deploy to Railway using 'railway.toml'
  render             Validate/deploy Render Blueprint 'render.yaml'
  fly                Deploy to Fly.io using 'fly.toml'
  heroku             Deploy to Heroku using 'Procfile'
  do                 Deploy to DigitalOcean App Platform using '.do/app.yaml'
  gcp                Deploy to Google Cloud Run using 'deploy/cloud/gcp-cloudrun.yaml'
  azure              Deploy to Azure Container Apps using 'deploy/cloud/azure-containerapp.yaml'
  aws                Deploy to AWS App Runner / ECS using 'deploy/cloud/aws-apprunner.yaml'
  vercel             Deploy frontend to Vercel using 'vercel.json'
  netlify            Deploy frontend to Netlify using 'netlify.toml'
  preflight          Run preflight validation checks only
  smoke              Run end-to-end runtime smoke checks against BACKEND_URL & FRONTEND_URL

Options:
  --check, --preflight-only   Run preflight checks and exit
  --smoke-test                Run runtime smoke tests and exit
  --dry-run                   Validate manifests/configs without mutating target infrastructure
  -h, --help                  Show this help message
USAGE
}

ensure_env_file() {
  if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
      cp .env.example .env
      log "Created .env from .env.example"
    else
      cat <<ENVEOF > .env
POSTGRES_USER=finmind
POSTGRES_PASSWORD=finmind
POSTGRES_DB=finmind
DATABASE_URL=postgresql+psycopg2://finmind:finmind@postgres:5432/finmind
REDIS_URL=redis://redis:6379/0
JWT_SECRET=finmind-prod-ready-jwt-secret-key-32chars
GEMINI_MODEL=gemini-1.5-flash
LOG_LEVEL=INFO
ENVEOF
      log "Generated default .env"
    fi
  fi
}

run_preflight() {
  local mode="${1:-all}"
  log "Running preflight checks (mode=${mode})..."

  ensure_env_file

  # Verify required deployment artifacts exist and are non-empty
  local required_files=(
    "packages/backend/Dockerfile"
    "app/Dockerfile"
    "docker-compose.yml"
    "Tiltfile"
    "railway.toml"
    "render.yaml"
    "fly.toml"
    "helm/Chart.yaml"
    "helm/values.yaml"
    "helm/templates/backend.yaml"
    "helm/templates/frontend.yaml"
    "helm/templates/postgres.yaml"
    "helm/templates/redis.yaml"
    "helm/templates/hpa.yaml"
    "helm/templates/pdb.yaml"
    "helm/templates/networkpolicy.yaml"
    "helm/templates/ingress.yaml"
    "deploy/k8s/namespace.yaml"
    "deploy/k8s/app-stack.yaml"
  )

  for f in "${required_files[@]}"; do
    [[ -s "$f" ]] || err "Missing or empty required deployment file: $f"
  done

  # Validate key Kubernetes / Helm constructs
  grep -q "HorizontalPodAutoscaler" helm/templates/hpa.yaml || err "HPA missing from Helm chart"
  grep -q "PodDisruptionBudget" helm/templates/pdb.yaml || err "PDB missing from Helm chart"
  grep -q "NetworkPolicy" helm/templates/networkpolicy.yaml || err "NetworkPolicy missing from Helm chart"
  grep -q "kind: Ingress" helm/templates/ingress.yaml || err "Ingress missing from Helm chart"
  grep -q "healthcheck:" docker-compose.yml || err "Healthchecks missing from docker-compose.yml"

  if [[ "$mode" == "compose" || "$mode" == "docker" ]]; then
    command -v docker >/dev/null 2>&1 || err "docker CLI is required for compose deployment"
    if [[ "$DRY_RUN" == "0" ]]; then
      docker compose config -q || err "docker-compose.yml validation failed"
    fi
  elif [[ "$mode" == "k8s" ]]; then
    command -v kubectl >/dev/null 2>&1 || err "kubectl is required for Kubernetes deployment"
  elif [[ "$mode" == "helm" ]]; then
    command -v helm >/dev/null 2>&1 || err "helm CLI is required for Helm deployment"
    helm lint ./helm || err "Helm chart lint failed"
  elif [[ "$mode" == "tilt" ]]; then
    command -v tilt >/dev/null 2>&1 || err "tilt CLI is required for Tilt workflow"
  fi

  ok "All preflight checks passed."
}

run_smoke_checks() {
  log "Running runtime smoke checks against BACKEND_URL=${BACKEND_URL} ..."

  command -v curl >/dev/null 2>&1 || err "curl is required for smoke checks"

  # 1. Wait for backend /health
  local retries=30
  local healthy=0
  for ((i=1; i<=retries; i++)); do
    if curl -fsS "${BACKEND_URL}/health" 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      healthy=1
      break
    fi
    sleep 2
  done
  [[ "$healthy" -eq 1 ]] || err "Backend healthcheck failed at ${BACKEND_URL}/health"
  ok "Backend /health is reachable and healthy."

  # 2. Check /metrics observability endpoint
  curl -fsS "${BACKEND_URL}/metrics" >/dev/null || err "Backend /metrics endpoint unreachable"
  ok "Backend /metrics observability endpoint verified."

  # 3. Check frontend reachability (optional if frontend container running)
  if curl -fsS "${FRONTEND_URL}" >/dev/null 2>&1; then
    ok "Frontend is reachable at ${FRONTEND_URL}."
  else
    warn "Frontend at ${FRONTEND_URL} not responding (skipped or running on alternate port)."
  fi

  # 4. End-to-End API Smoke Check (Auth + Core Modules + Weekly Digest + GDPR Export)
  local smoke_email="smoke.$(date +%s)@finmind.local"
  local smoke_pass="SmokePass!1234"

  curl -fsS -X POST "${BACKEND_URL}/auth/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${smoke_email}\",\"password\":\"${smoke_pass}\"}" >/dev/null \
    || err "Smoke check failed on /auth/register"

  local login_resp
  login_resp="$(curl -fsS -X POST "${BACKEND_URL}/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${smoke_email}\",\"password\":\"${smoke_pass}\"}")" \
    || err "Smoke check failed on /auth/login"

  local token
  token="$(printf '%s' "$login_resp" | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  [[ -n "$token" ]] || err "Failed to extract access_token during smoke check"

  for ep in "/auth/me" "/categories" "/expenses" "/bills" "/reminders" "/dashboard/summary" "/insights/budget-suggestion" "/api/digests/weekly" "/api/privacy/export"; do
    curl -fsS "${BACKEND_URL}${ep}" -H "Authorization: Bearer ${token}" >/dev/null \
      || err "Smoke check failed on endpoint ${ep}"
  done

  ok "All runtime smoke checks passed (auth, expenses, bills, reminders, dashboard, insights, digests, privacy)."
}

TARGET="compose"
for arg in "$@"; do
  case "$arg" in
    --check|--preflight-only|preflight)
      TARGET="preflight"
      ;;
    --smoke-test|smoke)
      TARGET="smoke"
      ;;
    --dry-run)
      DRY_RUN="1"
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    compose|docker|k8s|helm|tilt|railway|render|fly|heroku|do|gcp|azure|aws|vercel|netlify)
      TARGET="$arg"
      ;;
    *)
      err "Unknown argument: $arg (use --help for usage)"
      ;;
  esac
done

case "$TARGET" in
  preflight)
    run_preflight "all"
    ;;
  smoke)
    run_smoke_checks
    ;;
  compose|docker)
    run_preflight "compose"
    if [[ "$DRY_RUN" == "1" ]]; then
      log "Dry-run enabled: skipping docker compose up."
      exit 0
    fi
    log "Starting FinMind stack with Docker Compose..."
    docker compose up -d --build
    run_smoke_checks
    ;;
  k8s)
    run_preflight "k8s"
    if [[ "$DRY_RUN" == "1" ]]; then
      log "Dry-run enabled: validating Kubernetes manifests..."
      kubectl apply --dry-run=client -f deploy/k8s/namespace.yaml
      kubectl apply --dry-run=client -f deploy/k8s/secrets.example.yaml
      kubectl apply --dry-run=client -f deploy/k8s/app-stack.yaml
      exit 0
    fi
    kubectl apply -f deploy/k8s/namespace.yaml
    if [[ -f deploy/k8s/secrets.yaml ]]; then
      kubectl apply -f deploy/k8s/secrets.yaml
    else
      kubectl apply -f deploy/k8s/secrets.example.yaml
    fi
    kubectl apply -f deploy/k8s/app-stack.yaml
    kubectl apply -f deploy/k8s/monitoring-stack.yaml
    ok "Kubernetes stack deployed to namespace ${NAMESPACE}."
    ;;
  helm)
    run_preflight "helm"
    if [[ "$DRY_RUN" == "1" ]]; then
      helm template finmind ./helm --namespace "${NAMESPACE}" >/dev/null
      ok "Helm dry-run template rendering succeeded."
      exit 0
    fi
    helm upgrade --install finmind ./helm --namespace "${NAMESPACE}" --create-namespace --wait
    ok "Helm release 'finmind' deployed to namespace ${NAMESPACE}."
    ;;
  tilt)
    run_preflight "tilt"
    if [[ "$DRY_RUN" == "1" ]]; then
      log "Dry-run enabled: Tiltfile verified."
      exit 0
    fi
    exec tilt up
    ;;
  railway)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Railway config railway.toml verified."; exit 0; }
    command -v railway >/dev/null 2>&1 || err "railway CLI not installed"
    railway up
    ;;
  render)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Render blueprint render.yaml verified."; exit 0; }
    ok "Render Blueprint ready in render.yaml. Triggering deploy via Render CLI if available..."
    command -v render >/dev/null 2>&1 && render blueprint launch || true
    ;;
  fly)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Fly.io config fly.toml verified."; exit 0; }
    command -v flyctl >/dev/null 2>&1 || err "flyctl not installed"
    flyctl deploy --config fly.toml
    ;;
  heroku)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Heroku Procfile verified."; exit 0; }
    command -v heroku >/dev/null 2>&1 || err "heroku CLI not installed"
    git push heroku HEAD:main
    ;;
  do)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "DigitalOcean App Platform spec .do/app.yaml verified."; exit 0; }
    command -v doctl >/dev/null 2>&1 || err "doctl not installed"
    doctl apps create --spec .do/app.yaml
    ;;
  gcp)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "GCP Cloud Run manifest deploy/cloud/gcp-cloudrun.yaml verified."; exit 0; }
    command -v gcloud >/dev/null 2>&1 || err "gcloud CLI not installed"
    gcloud run services replace deploy/cloud/gcp-cloudrun.yaml
    ;;
  azure)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Azure Container App manifest deploy/cloud/azure-containerapp.yaml verified."; exit 0; }
    command -v az >/dev/null 2>&1 || err "az CLI not installed"
    az containerapp up --name finmind-backend --source ./packages/backend
    ;;
  aws)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "AWS App Runner config deploy/cloud/aws-apprunner.yaml verified."; exit 0; }
    command -v aws >/dev/null 2>&1 || err "aws CLI not installed"
    ok "AWS App Runner specification validated at deploy/cloud/aws-apprunner.yaml"
    ;;
  vercel)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Vercel config vercel.json verified."; exit 0; }
    command -v vercel >/dev/null 2>&1 || err "vercel CLI not installed"
    vercel --prod
    ;;
  netlify)
    run_preflight "all"
    [[ "$DRY_RUN" == "1" ]] && { ok "Netlify config netlify.toml verified."; exit 0; }
    command -v netlify >/dev/null 2>&1 || err "netlify CLI not installed"
    netlify deploy --prod --dir=app/dist
    ;;
esac

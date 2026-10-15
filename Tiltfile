# -*- mode: Python -*-
# FinMind Local Kubernetes Development Loop via Tilt

allow_k8s_contexts(['docker-desktop', 'minikube', 'kind-finmind', 'kind-kind', 'k3d-finmind', 'rancher-desktop', 'colima'])

# 1. Build Backend Image with Live Update
docker_build(
    'ghcr.io/rohitdash08/finmind-backend:latest',
    './packages/backend',
    dockerfile='./packages/backend/Dockerfile',
    live_update=[
        sync('./packages/backend/app', '/app/app'),
        sync('./packages/backend/wsgi.py', '/app/wsgi.py'),
        run('pip install -r /app/requirements.txt', trigger=['./packages/backend/requirements.txt']),
    ],
)

# 2. Build Frontend Image with Live Update
docker_build(
    'ghcr.io/rohitdash08/finmind-frontend:latest',
    './app',
    dockerfile='./app/Dockerfile',
    live_update=[
        sync('./app/src', '/app/src'),
        sync('./app/public', '/app/public'),
    ],
)

# 3. Apply Kubernetes Namespace, Secrets, Application Stack, and Observability Stack
k8s_yaml('deploy/k8s/namespace.yaml')

if os.path.exists('deploy/k8s/secrets.yaml'):
    k8s_yaml('deploy/k8s/secrets.yaml')
else:
    k8s_yaml('deploy/k8s/secrets.example.yaml')

k8s_yaml('deploy/k8s/app-stack.yaml')
k8s_yaml('deploy/k8s/monitoring-stack.yaml')

# 4. Configure Resource Dependencies & Port Forwards
k8s_resource(
    'postgres',
    port_forwards=['5432:5432'],
    labels=['data'],
)

k8s_resource(
    'redis',
    port_forwards=['6379:6379'],
    labels=['data'],
)

k8s_resource(
    'backend',
    port_forwards=['8000:8000'],
    resource_deps=['postgres', 'redis'],
    labels=['app'],
)

k8s_resource(
    'frontend',
    port_forwards=['5173:80'],
    resource_deps=['backend'],
    labels=['app'],
)

k8s_resource(
    'nginx',
    port_forwards=['8080:80'],
    resource_deps=['backend'],
    labels=['edge'],
)

k8s_resource(
    'prometheus',
    port_forwards=['9090:9090'],
    labels=['monitoring'],
)

k8s_resource(
    'grafana',
    port_forwards=['3000:3000'],
    resource_deps=['prometheus', 'loki'],
    labels=['monitoring'],
)

k8s_resource(
    'loki',
    port_forwards=['3100:3100'],
    labels=['monitoring'],
)

# 5. Local Developer Verification Tasks
local_resource(
    'smoke-check',
    cmd='./deploy.sh smoke',
    resource_deps=['backend', 'frontend'],
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=['test'],
)

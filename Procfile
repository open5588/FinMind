release: cd packages/backend && python -m flask --app wsgi:app init-db
web: cd packages/backend && gunicorn --workers=2 --threads=4 --bind 0.0.0.0:${PORT:-8000} wsgi:app

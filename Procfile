web: uv run python src/manage.py runserver ${YAMTRACK_PORT:-8000}
worker: uv run celery --workdir src -A config worker --beat --loglevel DEBUG --without-mingle --without-gossip
css: tailwindcss --input ./static/css/input.css --output ./static/css/main.css --watch --cwd ./src/

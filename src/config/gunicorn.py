import os

bind = "localhost:8001"
preload_app = True
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
timeout = 200
max_requests = 500
max_requests_jitter = 10

accesslog = "-"
errorlog = "-"

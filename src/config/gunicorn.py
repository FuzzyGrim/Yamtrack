import multiprocessing

bind = "localhost:8001"
preload_app = True
timeout = 200
max_requests = 500
max_requests_jitter = 10

# Access log - records incoming HTTP requests
accesslog = "-"
# Error log - records Gunicorn error messages
errorlog = "-"

# Workers configuration based on blog post recommendation
workers = multiprocessing.cpu_count() * 2 + 1

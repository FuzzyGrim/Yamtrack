import gc

bind = "localhost:23847"
preload_app = True
timeout = 200
max_requests = 500
max_requests_jitter = 10

accesslog = "-"
errorlog = "-"


def pre_fork(server, worker):  # noqa: ARG001
    """Freeze preloaded objects so the forked worker keeps sharing their memory."""
    gc.freeze()

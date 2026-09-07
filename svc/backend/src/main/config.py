import os
import json
import multiprocessing

bind = f"0.0.0.0:{os.environ.get('BACKEND_PORT', '80')}"

worker_class = os.environ.get("WORKER_CLASS", "gthread")
keepalive = int(os.environ.get("WORKER_KEEPALIVE", "60"))
threads = int(os.environ.get("WORKER_THREADS", "1"))
worker_connections = int(os.environ.get("WORKER_CONNECTIONS", "2"))
reuse_port = bool(json.loads(os.environ.get("WORKER_REUSE_PORT", "true")))

workers = os.environ.get("WORKERS")
if workers:
    workers = int(workers)
else:
    workers = (multiprocessing.cpu_count() * 2) + 1

accesslog = "-"
access_log_format = "{'remote_ip':'%(h)s','request_id':'%({X-Request-Id}i)s','response_code':'%(s)s'," \
                    "'request_method':'%(m)s','request_path':'%(U)s', 'request_timetaken':'%(D)s'," \
                    "'response_length':'%(B)s','pid':'%(p)s'}"
loglevel = "debug" if json.loads(os.environ.get('DEBUG', "False").lower()) else "info"

# max_requests disabled by default
max_requests = int(os.environ.get("WORKER_MAX_REQUESTS", "0"))
max_requests_jitter = int(os.environ.get("WORKER_MAX_REQUESTS_JITTER", "0"))

# preload_app = True # TODO check if this help to preload workers

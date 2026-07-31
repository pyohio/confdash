import os

bind = "0.0.0.0:8000"

# 2 workers x 4 threads rather than more processes: this workload is I/O-bound (Postgres plus
# outbound provider APIs), so threads absorb concurrency at a fraction of the memory.
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Needed for Django to see the real scheme and client IP behind a reverse proxy. Defaults to a
# private range; override when the proxy sits elsewhere.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "10.0.0.0/8")

# Access logging is owned by the app's request-logging middleware via structlog, which emits
# structured events. Gunicorn's plain-text access log would be a second, worse copy.
accesslog = None

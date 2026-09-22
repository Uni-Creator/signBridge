import os


bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))

timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
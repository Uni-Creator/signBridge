# gunicorn.conf.py
import os

worker_class = "gthread"
workers = 1
threads = 4
timeout = 120
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
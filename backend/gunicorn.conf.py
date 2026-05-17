# gunicorn.conf.py
import os

worker_class = "sync"
workers = 1
timeout = 120
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
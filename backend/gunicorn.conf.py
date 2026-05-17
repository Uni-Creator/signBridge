import os

def post_fork(server, worker):
    from gevent import monkey
    monkey.patch_all()

worker_class = "gevent"
workers = 1
timeout = 120
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
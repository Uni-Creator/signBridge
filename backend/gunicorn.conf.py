# This runs before any worker or app code
def post_fork(server, worker):
    pass

def pre_exec(server):
    pass

# Critical: patch here, before app imports
import gevent.monkey
gevent.monkey.patch_all()

worker_class = "gevent"
workers = 1
timeout = 120
bind = "0.0.0.0:10000"
def post_fork(server, worker):
    from gevent import monkey
    monkey.patch_all()


import gevent.monkey
gevent.monkey.patch_all()

worker_class = "gevent"
workers = 1
timeout = 120
bind = "0.0.0.0:10000"
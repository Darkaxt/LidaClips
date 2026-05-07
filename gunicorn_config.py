import os

bind = "0.0.0.0:5000"
workers = 1
threads = 4
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "0"))
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"

import os

bind = "0.0.0.0:8000"
workers = int(os.getenv("ERP_WEB_WORKERS", "3"))
threads = int(os.getenv("ERP_WEB_THREADS", "2"))
timeout = 90
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
access_log_format = '%({x-request-id}i)s %(m)s %(U)s %(s)s %(L)s'

#!/bin/sh
exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 2 --timeout 90 app:app

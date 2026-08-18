#!/bin/sh
exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 app:app

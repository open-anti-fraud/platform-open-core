#!/bin/bash
set -e

gunicorn -c main/config.py main.wsgi:application

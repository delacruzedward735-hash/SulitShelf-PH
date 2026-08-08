#!/bin/sh
set -eu

python -m flask --app run.py deploy-release

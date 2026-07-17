web: gunicorn --workers 2 --threads 4 --bind 0.0.0.0:$PORT run:app
release: flask --app run.py db upgrade

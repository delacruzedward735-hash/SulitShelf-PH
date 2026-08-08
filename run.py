import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    if app.config["IS_PRODUCTION"]:
        raise RuntimeError("Do not use Flask's development server in production. Start Gunicorn instead.")
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"})

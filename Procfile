web: python -m playwright install chromium 2>/dev/null || true && gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300

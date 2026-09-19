# RecoveryLens — one container, both halves.
#
# The frontend is built in a throwaway stage and only its output is copied into
# the runtime image, so Node and node_modules never ship. The API then serves
# those files itself (see api/main.py `_mount_frontend`), which means one
# service, one port, and no cross-origin configuration to get wrong.

# ---------------------------------------------------------------- build stage
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
# No VITE_API_URL: the built app calls its own origin, so the same image runs
# on any host without being rebuilt for it.
RUN npm run build

# -------------------------------------------------------------- runtime stage
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Runtime dependencies only. requirements-train.txt (catboost, xgboost,
# lightgbm) is deliberately absent: the models are already fitted and frozen,
# and catboost pins numpy<2, which the runtime cannot satisfy.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The fitted models. 27 MB, and the single thing whose absence turns a green
# build into a container that dies on startup: predictor.load() raises
# FileNotFoundError before the first request, so the deploy looks fine until the
# health check never passes.
COPY models/ ./models/

COPY api/ ./api/
COPY guidance/ ./guidance/
COPY messaging/ ./messaging/
COPY triage/ ./triage/
COPY voice/ ./voice/
COPY extraction/ ./extraction/
COPY prescription/ ./prescription/
COPY conftest.py ./
COPY --from=web /web/dist ./web/dist

# Render (and most hosts) inject PORT. Defaulted so the image also runs locally
# with a bare `docker run`.
# Fail the BUILD if anything the app loads at startup is missing, rather than
# shipping an image that dies on its first health check.
RUN python -c "\
import pathlib, sys; \
missing = [p for p in ['models/final_death_14d.pkl', 'api/artifacts/schema.json', \
                       'api/artifacts/thresholds.json', 'guidance/corpus.json', \
                       'web/dist/index.html'] if not pathlib.Path(p).exists()]; \
sys.exit('missing from the image: ' + ', '.join(missing)) if missing else print('startup files present')"

ENV PORT=8000
EXPOSE 8000

# One worker on purpose. The APScheduler job that sends check-ins lives in the
# process; two workers would mean two schedulers and two messages per check-in
# to the same family.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]

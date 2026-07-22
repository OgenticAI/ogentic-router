# ogentic-router live demo — Streamlit UI over the routing pipeline.
#
# Build context is the REPO ROOT (not demo/), so the router is installed from
# source — the demo uses main's API (router.backends, budget, ...), which the
# published 0.1.0 wheel predates. When a release ships those, switch to a pinned
# wheel. Railway: Root Directory = repo root, Dockerfile Path = demo/Dockerfile.
FROM python:3.12-slim

WORKDIR /app

# Build deps Presidio / spaCy may touch (small).
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install the router from source (+ the Shield classifier extra) and Streamlit.
# README.md is referenced by pyproject's `readme=`, so it's needed for the build.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[shield]" "streamlit>=1.36"

# Shield's Presidio pipeline loads en_core_web_lg — bundle it so there's no
# cold-start download. (en_core_web_sm is far smaller if you can trade NER
# accuracy for image size; Shield defaults to lg.)
RUN python -m spacy download en_core_web_lg

COPY demo ./demo

ENV PORT=8501
EXPOSE 8501
# Bind 0.0.0.0 (IPv4) — a Railway/HF edge proxy connects over IPv4; a "::"
# (IPv6-only) bind on -slim gives a 502 even though the app is up.
CMD ["sh", "-c", "streamlit run demo/app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]

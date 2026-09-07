FROM python:3.8-slim-bullseye AS requirements

RUN pip install --no-cache-dir poetry==1.5.1

WORKDIR /build
COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt --without-hashes -o /requirements.txt

FROM python:3.8-slim-bullseye

RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,from=requirements,source=/requirements.txt,target=/requirements.txt \
    pip install --no-cache-dir -r /requirements.txt

WORKDIR /app

ENV IS_ON_PREMISE=1
ENV WORKERS=
ENV CELERY_LOG_DIR=/var/log/celery

COPY ./src /app

CMD ["bash", "main/entrypoint.sh"]

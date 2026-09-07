# Third-Party Notices

This document lists third-party software referenced or installed when users build and deploy Platform Open Core:

1. **Infrastructure container images** referenced by `docker-compose.yml`. These images are not distributed as part of this repository and are pulled separately from Docker Hub by users during deployment.
2. **Backend build dependencies** referenced by [`svc/backend`](svc/backend). The backend container image is built by the user from the provided Dockerfile and dependency definitions; Platform Open Core does not distribute a prebuilt backend image.

These components are subject to their own license terms. Container image tags may be floating; versions below were verified on **September 8, 2026** and may change when upstream images or dependency pins are updated.

The authoritative Python dependency pins are [`svc/backend/pyproject.toml`](svc/backend/pyproject.toml) and [`svc/backend/poetry.lock`](svc/backend/poetry.lock).

---

## Summary — Infrastructure Images

| Component | Image tag | Version observed | License |
|---|---|---:|---|
| PostgreSQL | `postgres:16-alpine` | 16.15 | PostgreSQL License |
| Redis | `redis:7-alpine` | 7.4.11 | RSALv2 / SSPLv1 |
| RabbitMQ | `rabbitmq:4-management-alpine` | 4.3.5 | MPL-2.0 |
| Memcached | `memcached:1.6-alpine` | 1.6.45 | BSD-3-Clause |
| Alpine Linux / base packages | Base of `*-alpine` images | varies | Various |

Official images are available from [Docker Hub](https://hub.docker.com/) under `library/postgres`, `library/redis`, `library/rabbitmq`, and `library/memcached`.

---

## Summary — Backend Build

| Component | Source / tag | Notes | License overview |
|---|---|---|---|
| Python base image | `python:3.8-slim-bullseye` | Official Docker image; Debian 11 slim | PSF / Debian package licenses |
| `libpq5` | Debian package via `apt` in Dockerfile | PostgreSQL client shared library | PostgreSQL License |
| Python runtime packages | Installed from PyPI via Poetry export | See inventory below | Mostly MIT / BSD / Apache-2.0 |
| Copyleft / special terms | `psycopg2-binary`, `python-crontab`, `cssutils`, `certifi`, `fqdn` | See details below | LGPL / MPL-2.0 |

---

## PostgreSQL

* **Project:** PostgreSQL Database Management System
* **Image:** `postgres:16-alpine`
* **Version observed:** 16.15
* **Copyright:** Copyright (c) The PostgreSQL Global Development Group
* **License:** PostgreSQL License
* **Homepage:** https://www.postgresql.org/
* **License text:** https://www.postgresql.org/about/licence/

---

## Redis

* **Project:** Redis
* **Image:** `redis:7-alpine`
* **Version observed:** 7.4.11
* **Copyright:** Copyright (c) Redis Ltd.
* **License:** Dual licensed under the Redis Source Available License v2 (RSALv2) or Server Side Public License v1 (SSPLv1)
* **Homepage:** https://redis.io/
* **License information:** https://redis.io/legal/licenses/
* **RSALv2:** https://redis.io/legal/rsalv2-agreement/
* **SSPLv1:** https://www.mongodb.com/licensing/server-side-public-license

Redis 7.4 is distributed under the RSALv2 / SSPLv1 dual-license model. These are source-available licenses and are not OSI-approved open-source licenses.

Review the applicable license terms carefully, particularly for managed-service or service-provider use cases.

---

## RabbitMQ

* **Project:** RabbitMQ
* **Image:** `rabbitmq:4-management-alpine`
* **Version observed:** 4.3.5
* **Copyright:** Copyright (c) Broadcom, Inc. and/or its subsidiaries; historical copyrights of VMware, Pivotal, and contributors may also apply
* **License:** Mozilla Public License 2.0 (MPL-2.0)
* **Homepage:** https://www.rabbitmq.com/
* **Source:** https://github.com/rabbitmq/rabbitmq-server
* **License text:** https://www.mozilla.org/MPL/2.0/

The official RabbitMQ image also contains Erlang/OTP and other runtime components that are subject to their respective licenses. Erlang/OTP is licensed under the Apache License 2.0.

---

## Memcached

* **Project:** memcached
* **Image:** `memcached:1.6-alpine`
* **Version observed:** 1.6.45
* **Copyright:** Copyright (c) Danga Interactive, LLC and contributors
* **License:** BSD-3-Clause
* **Homepage:** https://memcached.org/
* **Source:** https://github.com/memcached/memcached
* **License text:** https://github.com/memcached/memcached/blob/master/LICENSE

---

## Alpine Linux and Base Packages

The `*-alpine` container images referenced above are based on Alpine Linux.

Alpine Linux is a Linux distribution composed of multiple software packages. Individual packages included in an Alpine-based container image are subject to their respective licenses.

The Alpine Linux version and installed package set may differ between container images and may change when floating image tags are updated.

* **Project:** Alpine Linux
* **Homepage:** https://alpinelinux.org/
* **Package repository:** https://pkgs.alpinelinux.org/

See the corresponding Docker Official Image build definitions and Alpine package metadata for the licenses applicable to individual packages.

---

## Backend Build — Base OS

* **Base image:** `python:3.8-slim-bullseye` ([Docker Hub `library/python`](https://hub.docker.com/_/python))
* **OS:** Debian 11 (bullseye) slim
* **Build definition:** [`svc/backend/Dockerfile`](svc/backend/Dockerfile)

The base image includes the CPython 3.8 runtime and Debian packages shipped by the official image. Those components are subject to the Python Software Foundation License and the licenses of the corresponding Debian packages.

### Explicitly installed Debian package

| Package | Purpose | License (upstream) |
|---|---|---|
| `libpq5` | PostgreSQL client shared library | PostgreSQL License |

Additional OS packages may be present in the slim base image. For a more complete OS-level inventory, inspect the image filesystem, the Docker Official Image build definition, and Debian package metadata (`dpkg -l`).

---

## Backend Build — Python Packages

Python dependencies are declared in Poetry and installed into the locally built backend image with `pip` from a Poetry-exported requirements list (public PyPI).

License metadata below was collected from installed package metadata using `pip-licenses` on September 8, 2026. It is provided as an inventory aid and may simplify or combine license expressions. The applicable upstream `LICENSE`, `COPYING`, or equivalent license files control; PyPI metadata is a secondary reference.

### Packages requiring special attention

| Package | Version | License / terms | Notes |
|---|---:|---|---|
| `psycopg2-binary` | 2.9.7 | LGPL-3.0-or-later with OpenSSL linking exception | Weak copyleft. Binary wheels may bundle native libraries such as `libpq` and OpenSSL, which have their own applicable license terms. |
| `python-crontab` | 3.0.0 | LGPLv3+ | Weak copyleft. |
| `cssutils` | 2.7.1 | LGPL | Weak copyleft; refer to the upstream license file for the exact terms applicable to this release. |
| `certifi` | 2023.7.22 | MPL-2.0 | File-level copyleft. |
| `fqdn` | 1.5.1 | MPL-2.0 | File-level copyleft. |

Relevant upstream license information:

* `psycopg2`: https://www.psycopg.org/docs/license.html
* `python-crontab`: https://pypi.org/project/python-crontab/3.0.0/
* `cssutils`: https://pypi.org/project/cssutils/2.7.1/
* `certifi`: https://pypi.org/project/certifi/2023.7.22/
* `fqdn`: https://pypi.org/project/fqdn/1.5.1/

`pip-licenses` reports Python package metadata but does not necessarily enumerate native libraries bundled inside binary wheels. If a user redistributes a locally built backend image, those bundled components and their licenses should also be reviewed.

### Inventory by license family

Versions match the backend image built from the current `poetry.lock`.

#### Apache Software License

`aio-pika` 9.4.3, `aiohttp` 3.10.11, `aiormq` 6.8.1, `aiosignal` 1.3.1, `arrow` 1.2.3, `async-timeout` 4.0.3, `backports.zoneinfo` 0.2.1, `faststream` 0.4.7, `frozenlist` 1.4.0, `googleapis-common-protos` 1.60.0, `grpcio` 1.57.0, `importlib-metadata` 6.8.0, `importlib-resources` 6.0.1, `msgpack` 1.0.5, `multidict` 6.0.4, `opentelemetry-api` 1.19.0, `opentelemetry-exporter-otlp` 1.19.0, `opentelemetry-exporter-otlp-proto-common` 1.19.0, `opentelemetry-exporter-otlp-proto-grpc` 1.19.0, `opentelemetry-exporter-otlp-proto-http` 1.19.0, `opentelemetry-proto` 1.19.0, `opentelemetry-sdk` 1.19.0, `opentelemetry-semantic-conventions` 0.40b0, `propcache` 0.2.0, `pyOpenSSL` 23.2.0, `python-multipart` 0.0.5, `requests` 2.31.0, `tzdata` 2023.3, `yarl` 1.15.2

#### BSD / BSD-3-Clause (including “BSD License” classifiers)

`amqp` 5.1.1, `asgiref` 3.7.2, `billiard` 3.6.4.0, `bson` 0.5.10, `celery` 5.2.4, `channels` 3.0.5, `channels-redis` 3.3.1, `click` 8.1.6, `click-plugins` 1.1.1, `daphne` 3.0.2, `Django` 3.2.20, `django-celery-beat` 2.2.1, `django-json-widget` 1.1.1, `django-timezone-field` 4.2.3, `hiredis` 2.2.3, `idna` 3.4, `jsonpointer` 2.4, `kombu` 5.3.1, `lxml` 4.9.2, `pamqp` 3.3.0, `pika` 1.3.2, `prompt-toolkit` 3.0.39, `protobuf` 4.24.0, `pyasn1` 0.5.0, `pyasn1-modules` 0.3.0, `pycparser` 2.21, `Pygments` 2.16.1, `sqlparse` 0.4.4, `starlette` 0.31.0, `uvicorn` 0.18.3, `vine` 5.0.0, `webcolors` 1.13, `wrapt` 1.15.0

#### MIT License

`aioredis` 1.3.1, `annotated-types` 0.7.0, `anyio` 3.7.1, `attrs` 23.1.0, `autobahn` 23.1.2, `Automat` 22.10.0, `backoff` 2.2.1, `beautifulsoup4` 4.12.2, `cachetools` 5.5.2, `cffi` 1.15.1, `charset-normalizer` 3.2.0, `click-didyoumean` 0.3.0, `click-repl` 0.3.0, `constantly` 15.1.0, `Deprecated` 1.2.14, `django-inlinecss` 0.3.0, `exceptiongroup` 1.1.2, `fast-depends` 2.4.12, `future` 0.18.3, `graphql-core` 3.2.3, `gunicorn` 20.1.0, `h11` 0.14.0, `hyperlink` 21.0.0, `incremental` 22.10.0, `jsonref` 0.3.0, `jsonschema` 4.4.0, `markdown-it-py` 3.0.0, `mdurl` 0.1.2, `notifiers` 1.3.3, `promise` 2.3, `pydantic` 2.9.2, `pydantic_core` 2.23.4, `PyJWT` 2.7.0, `pynliner` 0.8.0, `pyrsistent` 0.19.3, `pytz` 2023.3, `ratelimit` 2.2.1, `rfc3339-validator` 0.1.4, `rfc3986-validator` 0.1.1, `rich` 13.9.4, `service-identity` 23.1.0, `six` 1.16.0, `soupsieve` 2.4.1, `strawberry-graphql` 0.127.4, `strawberry-graphql-django` 0.4.0, `Twisted` 22.10.0, `txaio` 23.1.1, `typer` 0.13.0, `uri-template` 1.3.0, `urllib3` 1.26.16, `zipp` 3.16.2

#### Mozilla Public License 2.0 (MPL-2.0)

`certifi` 2023.7.22, `fqdn` 1.5.1

#### Python Software Foundation License

`python-memcached` 1.59, `typing_extensions` 4.8.0, `aiohappyeyeballs` 2.4.4

`aiohappyeyeballs` is licensed under the same terms as CPython (PSF-2.0). Some package metadata for this release also contains an `Other/Proprietary` classifier; the upstream project license statement identifies the applicable license as the Python Software Foundation License.

#### LGPL

`cssutils` 2.7.1, `psycopg2-binary` 2.9.7, `python-crontab` 3.0.0

#### Alternative / multiple license expressions

| Package | Version | License expression / notes |
|---|---:|---|
| `cryptography` | 41.0.3 | Apache-2.0 OR BSD-3-Clause |
| `sniffio` | 1.3.0 | MIT OR Apache-2.0 |
| `python-dateutil` | 2.8.2 | BSD-3-Clause / Apache-2.0 dual-license metadata; see upstream license for contribution-specific terms |

#### Other permissive / other OSI-approved licenses

| Package | Version | License |
|---|---:|---|
| `Pillow` | 9.0.1 | Historical Permission Notice and Disclaimer (HPND) |
| `isoduration` | 20.11.0 | ISC License (ISCL) |
| `shellingham` | 1.5.4 | ISC License (ISCL) |
| `zope.interface` | 6.0 | Zope Public License |

### Regenerating the Python inventory

From a locally built backend image:

```sh
docker compose build backend
docker compose run --rm --entrypoint sh backend \
  -c 'pip install -q pip-licenses && pip-licenses --format=markdown --order=license'
```

---

## Docker Official Images

The infrastructure images and Python base image referenced above are Docker Official Images.

Build definitions and package metadata are maintained in the following repositories:

* https://github.com/docker-library/official-images
* https://github.com/docker-library/postgres
* https://github.com/docker-library/redis
* https://github.com/docker-library/rabbitmq
* https://github.com/docker-library/memcached
* https://github.com/docker-library/python

Container images may include additional operating-system packages, libraries, runtimes, and other third-party components that are subject to their own licenses.

For a more complete inventory of software contained in an image, inspect its SBOM, image filesystem, build definition, and installed package metadata.

For Alpine APK-managed packages, installed packages can be listed with:

```sh
apk list -I
```

For Debian-based backend image packages:

```sh
dpkg -l
```

---

## Disclaimer

This notice is provided for convenience and informational purposes.

Third-party software is subject to the license terms provided by its respective copyright holders and upstream projects. In case of any conflict between this document and an applicable upstream license, the upstream license terms control.

Platform Open Core does not distribute the referenced infrastructure images or a prebuilt backend image. Users obtain infrastructure images from their respective upstream registries and build the backend image locally from the provided source and dependency definitions.

Users are responsible for ensuring compliance with all applicable third-party license terms for their deployment, modification, and any redistribution they perform.

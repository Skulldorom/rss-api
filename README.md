# rss-api

Simple API to fetch rss feeds for github releases using fresh rss (powered by FastAPI)

![alt text](example/image.png)

This allows you to easily integrate it with [https://gethomepage.dev/](https://gethomepage.dev/)
Using their [Custom API integration](https://gethomepage.dev/widgets/services/customapi/) 

## API Documentation

The API provides interactive documentation at:
- Swagger UI: `http://localhost:5000/docs`
- ReDoc: `http://localhost:5000/redoc`

## Available Endpoints

- `GET /freshrss/unread` - Fetch unread RSS items from FreshRSS
  - Optional query parameters:
    - `n` (integer, default `10`) - Number of unread items to return
    - `category` (string) - FreshRSS category label to scope unread items, e.g. `/freshrss/unread?category=Tech`

## Testing

Run the test suite with:

```bash
uv sync
uv run pytest
```

GitHub Actions runs these tests for pull requests and before publishing the Docker image from `main`.

Example of services.yaml:

```
- Updates:
            icon: github.png
            href: http://192.168.0.11:5000
            siteMonitor: http://192.168.0.11:5000/freshrss/unread
            widget:
              type: customapi
              name: Unread RSS
              url: http://192.168.0.11:5000/freshrss/unread
              display: dynamic-list
              mappings:
                name: feed
                label: display
```

# Docker

Copy the example environment file and set all three required values:

```bash
cp .env.example .env
```

`FRESHRSS_HOST` must be a URL that is reachable **from the API container**. Do
not use `localhost`: inside the container that name refers to the API container
itself, not FreshRSS. Compose validates `FRESHRSS_HOST`, `FRESHRSS_USER`, and
`FRESHRSS_PASS` before creating the container, so an unset or empty value
produces a clear configuration error instead of entering a restart loop.

Choose one of the following host configurations.

### FreshRSS in the same Compose project

When FreshRSS is a service on the same Compose network, use its service name.
For example, if the service is named `freshrss`:

```yaml
services:
  freshrss:
    image: freshrss/freshrss:latest
    # Add the FreshRSS volumes and other settings required by your deployment.

  custom-api:
    image: ghcr.io/skulldorom/rss-api:latest
    environment:
      FRESHRSS_HOST: http://freshrss
      FRESHRSS_USER: ${FRESHRSS_USER:?Set FRESHRSS_USER in .env}
      FRESHRSS_PASS: ${FRESHRSS_PASS:?Set FRESHRSS_PASS in .env}
```

Equivalently, keep the provided Compose file and set this in `.env`:

```dotenv
FRESHRSS_HOST=http://freshrss
```

### FreshRSS on the Docker Desktop host

Docker Desktop provides `host.docker.internal` for reaching a service exposed
by the host. If FreshRSS is published on host port `8020`, use:

```dotenv
FRESHRSS_HOST=http://host.docker.internal:8020
```

### FreshRSS on a Linux host

On Linux, set the host's address explicitly (replace the example address with
one reachable from Docker):

```dotenv
FRESHRSS_HOST=http://192.168.1.10:8020
```

Alternatively, map Docker's host gateway in `docker-compose.yml` and then use
the same hostname as the Docker Desktop example:

```yaml
services:
  custom-api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

```dotenv
FRESHRSS_HOST=http://host.docker.internal:8020
```

## Running with Docker Compose

You can run the API using the pre-built image from GitHub Container Registry:

```bash
docker compose up
```

This uses the image `ghcr.io/skulldorom/rss-api:latest`.

If you want to build locally, update `docker-compose.yml` to use `build: .` instead of the `image:` field.

### Health check

The container image defines a Docker `HEALTHCHECK` that calls `GET /health`.
If you want to override it in `docker-compose.yml`, you can add a service-level `healthcheck` block.

### How to update

Go to the location where you ran git clone, `cd rss-api`

```
docker compose down
git pull origin main
docker compose up -d
```

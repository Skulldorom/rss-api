# rss-api

Simple API to fetch rss feeds for github releases, deplyoed using docker

```
git clone https://github.com/Skulldorom/rss-api.git
cd rss-api
```

Create a `.env` file in the root directory and add the following variables:

```
FRESHRSS_HOST=
FRESHRSS_USER=
FRESHRSS_PASS=
```

### Build the Docker image

To test

```
docker compose up --build
```

### Final build

```
docker compose up --build -d
```

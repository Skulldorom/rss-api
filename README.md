# rss-api

Simple API to fetch rss feeds for github releases, deplyoed using docker

```
git clone https://github.com/Skulldorom/rss-api.git
cd rss-api
```

Create a `.env` file in the root directory and add the following variables:

```
# Base Url of fresh rss
FRESHRSS_HOST=http://localhost:8020
# Username
FRESHRSS_USER=
# API password 
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

### How to update

go the location where you rna git clone, `cd rss-api`

```
docker compose down
git pull origin main
docker compose up --build -d
```


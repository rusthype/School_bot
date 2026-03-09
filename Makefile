SHELL := /bin/sh

.PHONY: build up down logs shell db-shell migrate

build:
\tdocker compose build

up:
\tdocker compose up -d

down:
\tdocker compose down

logs:
\tdocker compose logs -f --tail=200

shell:
\tdocker compose exec bot /bin/sh

db-shell:
\tdocker compose exec postgres psql -U postgres -d school_bot_db

migrate:
\tdocker compose exec bot python scripts/add_delivery_columns.py

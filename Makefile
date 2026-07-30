# Local development stack. everything runs in Docker, nothing needs to be
# installed on the host except Docker itself.

COMPOSE      := docker compose -f docker/docker-compose.yml
COMPOSE_TEST := docker compose -f docker/docker-compose-test.yml

.PHONY: up down logs sh seed reset backing-up test-up test-down test migrate revision current heads

## full stack (db + redis + api) 

up:            ## Build and start the whole stack (api on :8000)
	$(COMPOSE) up --build -d
	$(COMPOSE) logs -f api

down:          ## Stop the stack (keeps the data volumes)
	$(COMPOSE) down

reset:         ## Stop the stack AND wipe the Postgres/Redis volumes
	$(COMPOSE) down -v

logs:          ## Tail the api logs
	$(COMPOSE) logs -f api

sh:            ## Shell inside the api container (skips migrations)
	$(COMPOSE) run --rm -e RUN_MIGRATIONS=false api bash

seed:          ## Insert the dev api key + pricing rows
	$(COMPOSE) exec api python -m scripts.seed_dev

backing-up:    ## Start only Postgres and Redis (run the app on the host yourself)
	$(COMPOSE) up -d db redis

## migrations (run inside the api container) 

migrate:       ## Apply all pending migrations
	$(COMPOSE) exec api alembic upgrade head

revision:      ## Autogenerate a migration: make revision m="your message"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

current:       ## Show the database's current revision
	$(COMPOSE) exec api alembic current

heads:         ## Show the latest migration file revision
	$(COMPOSE) exec api alembic heads

## tests (separate, volume-less stack; stop `up` first, ports collide) 

test-up:       ## Start the test Postgres + Redis
	$(COMPOSE_TEST) up -d

test-down:     ## Stop the test stack
	$(COMPOSE_TEST) down

test:          ## Run the suite on the host against the test stack
	pytest tests/ -v

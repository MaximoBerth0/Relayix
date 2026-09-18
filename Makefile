# Local development: Postgres + Redis in Docker, the api runs on the host via
# Poetry (`poetry install` once, then `make run`).

COMPOSE      := docker compose -f docker/docker-compose.yml
COMPOSE_TEST := docker compose -f docker/docker-compose-test.yml

.PHONY: up down logs reset run test-up test-down test migrate revision current heads

## backing services (db + redis)

up:            ## Start Postgres + Redis in the background
	$(COMPOSE) up -d

down:          ## Stop the stack (keeps the data volumes)
	$(COMPOSE) down

reset:         ## Stop the stack AND wipe the Postgres/Redis volumes
	$(COMPOSE) down -v

logs:          ## Tail the backing services' logs
	$(COMPOSE) logs -f

## app (runs on the host)

run:           ## Apply migrations, then start uvicorn with --reload
	poetry run alembic upgrade head
	poetry run uvicorn app.main:app --reload

## migrations (run on the host, against the backing Postgres)

migrate:       ## Apply all pending migrations
	poetry run alembic upgrade head

revision:      ## Autogenerate a migration: make revision m="your message"
	poetry run alembic revision --autogenerate -m "$(m)"

current:       ## Show the database's current revision
	poetry run alembic current

heads:         ## Show the latest migration file revision
	poetry run alembic heads

## tests (separate, volume-less stack; stop `up` first, ports collide) 

test-up:       ## Start the test Postgres + Redis
	$(COMPOSE_TEST) up -d

test-down:     ## Stop the test stack
	$(COMPOSE_TEST) down

test:          ## Run the suite on the host against the test stack
	pytest tests/ -v

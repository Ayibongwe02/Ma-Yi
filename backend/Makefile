# Ma-yi Live Command — common Docker targets
IMAGE      ?= mayi-command
TAG        ?= stage3
FULL_IMAGE ?= $(IMAGE):$(TAG)
PORT       ?= 8000

.PHONY: build up down logs shell health push clean ml-bootstrap

## Build the production image
build:
	docker build -t $(FULL_IMAGE) .

## Pre-train ML with ~1000 diverse closed trades (bootstrap pool)
## Usage: make ml-bootstrap
##        make ml-bootstrap TARGET=1200 BARS=900
ml-bootstrap:
	PYTHONPATH=. python -m engine.ml.bootstrap --target $(or $(TARGET),1000) --bars $(or $(BARS),900)

## Build and start (foreground)
up:
	docker compose up --build

## Build and start detached
up-d:
	docker compose up --build -d

## Stop and remove containers
down:
	docker compose down

## Follow app logs
logs:
	docker compose logs -f app

## Open a shell inside the running container
shell:
	docker compose exec app bash

## Hit the health endpoint
health:
	curl -sf http://localhost:$(PORT)/api/health && echo " OK" || echo " FAIL"

## Tag & push to a registry (set REGISTRY=your.registry/path)
push:
	@test -n "$(REGISTRY)" || (echo "Set REGISTRY=... first"; exit 1)
	docker tag $(FULL_IMAGE) $(REGISTRY)/$(FULL_IMAGE)
	docker push $(REGISTRY)/$(FULL_IMAGE)

## Remove local image
clean:
	docker rmi $(FULL_IMAGE) 2>/dev/null || true
	docker compose down --rmi local 2>/dev/null || true

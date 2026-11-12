demo:
	./scripts/demo.sh

test:
	uv run pytest -q

journeys:
	node scripts/live_journeys.mjs

.PHONY: demo test journeys

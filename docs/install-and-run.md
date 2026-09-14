# Install & Run

How to get `agentos` (the MonkeyBrain / CognitiveOS runtime) running
locally, end to end — clone, install, boot, verify, first request.

## Prerequisites

**Real, required** — `Kernel` fails fast at boot (raises `RuntimeError`)
if any of these are unreachable, per its documented contract:

- MongoDB
- Redis
- Neo4j (`bolt://...:7687`)

**Optional / degrades gracefully if absent**: NATS, InfluxDB,
Elasticsearch (audit sink), OPA, mem0.

**For a native (non-Docker) install**: Python `>=3.11`.

## Option A — Docker Compose (recommended for a first run)

Brings up every prerequisite above plus the runtime itself, with real
`depends_on: condition: service_healthy` gating (the runtime doesn't
start accepting traffic until Mongo/Redis/Neo4j report healthy):

```bash
git clone https://github.com/monkeypatched-stack/cogitive-os.git
cd cogitive-os
docker compose up agentos
```

Check it's actually up:

```bash
curl http://localhost:8031/live
curl http://localhost:8031/health
```

Tail logs / stop:

```bash
docker compose logs -f agentos
docker compose down
```

See [`deployment.md`](deployment.md) for what each Compose service is,
the Kubernetes manifest, and the Helm chart.

## Option B — native, against your own MongoDB/Redis/Neo4j

Use this if you already run Mongo/Redis/Neo4j elsewhere (a shared dev
cluster, existing local instances) and don't want Docker Compose
managing them.

```bash
git clone https://github.com/monkeypatched-stack/cogitive-os.git
cd cogitive-os
uv sync --frozen
source .venv/bin/activate
```

Point it at your services (only needed if they're not on the defaults
`localhost`/standard ports — the runtime's own config layer documents
the full variable list; the ones that matter most to get right first):

```bash
export MONGODB_URL=mongodb://localhost:27017
export REDIS_HOST=localhost
export REDIS_PORT=6379
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=<your-neo4j-password>
```

Start it:

```bash
./scripts/start_server.sh 8031
```

`start_server.sh` takes `[port] [workers] [auth_required] [log_level]`
— defaults are `8000 1 false info` if you omit them, which is why the
`8031` above matters (it's the port every other doc in this repo
assumes). `auth_required=false` is explicitly a **development-mode**
setting — every route accepts an unauthenticated `X-User-ID` header
instead of a verified Bearer token. Pass `true` (and see
[`deployment.md`](deployment.md)'s Security section) before exposing
this anywhere but your own machine.

Logs land at `/tmp/monkeybrain_server.log`; stop it with
`./scripts/stop_server.sh`.

## Verify it's actually working

```bash
curl http://localhost:8031/live
# {"status": "alive"}

curl http://localhost:8031/health
# {"status": "healthy", "checks": {"mongodb": ..., "redis": ..., "runtime": ..., "policy": ...}}
```

Seed a small demo world (actors, societies, geography — the same data
the Living World Explorer frontend renders) and run the canonical
first request:

```bash
python3 scripts/seed_world.py seed    # populate baseline actors/geography/KG
python3 scripts/seed_world.py validate  # sanity-check what was seeded
python3 scripts/seed_world.py demo    # POST /prompt as Priya Sharma: "Buy 2 liters of milk."
```

A successful `demo` run prints the real JSON response — plan, predicted
outcome, execution result, and whether the goal was achieved. That's
the actual cognitive-tick pipeline running end to end: Observe → Believe
→ Plan → Predict → Decide → Execute → Compare → Learn (see
[`architecture.md`](architecture.md#cognitive-pipeline-per-actor-tick)
for what each stage actually does).

## Next steps

- [Examples](examples.md) — real request/response pairs against a
  representative slice of the API.
- [OpenAPI spec](openapi.md) — the full spec, live or as a frozen
  snapshot.
- [Architecture](architecture.md) — how the runtime is actually built:
  layering, geography/society, the cognitive pipeline, policy/governance.
- [Troubleshooting](troubleshooting.md) — real issues hit and diagnosed
  running this exact stack.
- [Deployment](deployment.md) — Kubernetes and Helm, beyond Docker
  Compose.

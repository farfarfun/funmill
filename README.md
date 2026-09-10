# Funmill

Funmill provides one stable task API while execution is delegated to a
replaceable backend. The first backend is self-hosted
[Windmill](https://github.com/windmill-labs/windmill), pinned to `v1.808.0`.

```text
client -> Funmill /v1 -> TaskBackend -> Windmill
                                  -> another backend later
```

Funmill owns the public request and response models. Backend job IDs remain
opaque strings, and no Windmill routes or payloads are exposed to clients.

## Start

Install the project and the Windmill binary:

```bash
uv sync
uv run funmill install windmill
```

Start Windmill against an existing PostgreSQL database:

```bash
vim ~/.farfarfun/funmill/services/windmill/.env
uv run funmill start windmill
```

Set `DATABASE_URL=postgresql://windmill:password@127.0.0.1:5432/windmill` in
that file. The installer creates it with `0600` permissions and never
overwrites an existing configuration.

Open <http://localhost:8813>, log in with `admin@windmill.dev` / `changeme`,
change the password, and create an API token in the `admins` workspace. Then
start Funmill in another terminal:

```bash
FUNMILL_API_KEY='replace-me' \
WINDMILL_URL='http://127.0.0.1:8813' \
WINDMILL_WORKSPACE=admins \
WINDMILL_TOKEN='replace-me' \
uv run funmill start
```

The Funmill API port is fixed at `8812`; the active third-party UI/API port is
fixed at `8813`. OpenAPI docs are at <http://localhost:8812/docs>. All `/v1`
routes require `X-API-Key`. See the
[Windmill deployment guide](src/funmill/backends/windmill/README.md) for
PostgreSQL setup and additional workers.

Run the end-to-end task and DAG checks with:

```bash
FUNMILL_API_KEY=the-value-from-env ./scripts/smoke.sh
```

Set `FUNMILL_CALLBACK_URL` to test callbacks. The URL must be reachable from
the Windmill workers.

## API

| Operation | Route |
| --- | --- |
| Submit Python/Bash | `POST /v1/tasks` |
| Submit a DAG | `POST /v1/workflows` |
| Status | `GET /v1/tasks/{task_id}` |
| Logs | `GET /v1/tasks/{task_id}/logs` |
| Progress | `GET /v1/tasks/{task_id}/progress` |
| Result | `GET /v1/tasks/{task_id}/result` |
| Cancel | `POST /v1/tasks/{task_id}/cancel` |
| Rerun | `POST /v1/tasks/{task_id}/rerun` |

Submit one task, optionally waiting for existing task IDs:

```json
{
  "language": "python",
  "source": "def main(value: int):\n    return value * 2\n",
  "args": {"value": 21},
  "depends_on": ["EXISTING_TASK_ID"],
  "dependency_timeout_seconds": 3600,
  "retry": {"attempts": 2, "delay_seconds": 5},
  "timeout_seconds": 300,
  "callback_url": "https://example.internal/task-callback"
}
```

Submit `A -> [B, C]` as one workflow:

```json
{
  "tasks": [
    {"key": "a", "language": "python", "source": "def main(): return 1"},
    {"key": "b", "language": "python", "source": "def main(): return 2", "depends_on": ["a"]},
    {"key": "c", "language": "bash", "source": "main() { echo 3; }", "depends_on": ["a"]}
  ]
}
```

Dependencies inside a workflow are task keys. Top-level `depends_on` values are
IDs returned by earlier Funmill submissions. The Windmill backend checks those
dependencies in short jobs separated by Flow sleeps, so waiting does not hold a
worker process. Failure or cancellation of a dependency fails the waiting task.
Workflow results and callback payloads are objects keyed by every workflow task
key. Each topological layer is a synchronization barrier; tasks in the same
layer run in parallel.

Callbacks contain `task_id`, `status`, and `payload`; success and failure
delivery retry three times. Delivery is at least once, so callback receivers
must be idempotent.

## Backends

The public contract is `TaskBackend` in `src/funmill/backends/base.py`. Backend
selection uses `FUNMILL_BACKEND`; registration lives in
`src/funmill/backends/__init__.py`, following the same driver pattern as
`fundrive`. Each third-party adapter lives in its own directory, such as
`src/funmill/backends/windmill/`.

Changing the backend does not change `/v1`, but it does not migrate old jobs or
their IDs. Add a Funmill-owned ID mapping database only when jobs must remain
queryable after a live backend migration.

## Operations

```bash
funmill services
funmill install windmill
funmill start windmill
funmill start
```

Both start commands run in the foreground and stop normally with `Ctrl+C`.
Use systemd or an existing process manager for long-running deployment. Add
TLS, PostgreSQL backups, callback egress restrictions, and a secrets manager
before network exposure.

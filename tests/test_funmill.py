import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import funmill.cli as funmill_cli
from funmill.api import app, backend_dependency
from funmill.backends.base import TaskBackend
from funmill.backends.windmill import WindmillBackend
from funmill.backends.windmill import service as windmill_service
from funmill.models import (
    TaskInfo,
    TaskLogs,
    TaskProgress,
    TaskResult,
    TaskStatus,
    TaskSubmit,
    WorkflowSubmit,
)

JOB_ID = "11111111-1111-4111-8111-111111111111"
RERUN_ID = "22222222-2222-4222-8222-222222222222"


def workflow(**overrides):
    value = {
        "tasks": [
            {"key": "a", "language": "python", "source": "def main(): return 1"},
            {
                "key": "b",
                "language": "python",
                "source": "def main(): return 2",
                "depends_on": ["a"],
            },
            {
                "key": "c",
                "language": "bash",
                "source": "main() { echo 3; }",
                "depends_on": ["a"],
            },
        ]
    }
    value.update(overrides)
    return WorkflowSubmit.model_validate(value)


def test_workflow_dependency_validation_and_layers():
    layers = [[task.key for task in layer] for layer in workflow().topological_layers()]
    assert layers == [
        ["a"],
        ["b", "c"],
    ]

    with pytest.raises(ValidationError, match="unknown dependencies"):
        workflow(
            tasks=[
                {
                    "key": "a",
                    "language": "python",
                    "source": "def main(): pass",
                    "depends_on": ["missing"],
                }
            ]
        )

    with pytest.raises(ValidationError, match="dependency cycle"):
        workflow(
            tasks=[
                {
                    "key": "a",
                    "language": "python",
                    "source": "def main(): pass",
                    "depends_on": ["b"],
                },
                {
                    "key": "b",
                    "language": "python",
                    "source": "def main(): pass",
                    "depends_on": ["a"],
                },
            ]
        )


def test_windmill_translates_task_dependencies_retry_and_callback():
    captured = {}

    def handler(request: httpx.Request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, text=JOB_ID)

    client = httpx.Client(
        base_url="http://windmill/api/w/admins/",
        transport=httpx.MockTransport(handler),
    )
    backend = WindmillBackend("http://unused", "admins", "token", client=client)
    task_id = backend.submit_task(
        TaskSubmit.model_validate(
            {
                "language": "python",
                "source": "def main(value): return value",
                "args": {"value": 7},
                "depends_on": [RERUN_ID],
                "retry": {"attempts": 2, "delay_seconds": 3},
                "callback_url": "https://example.test/callback",
            }
        )
    )

    assert task_id == JOB_ID
    assert captured["path"].endswith("/jobs/run/preview_flow")
    value = captured["body"]["value"]
    assert value["modules"][0]["value"]["type"] == "whileloopflow"
    assert value["modules"][1]["retry"]["constant"] == {
        "attempts": 2,
        "seconds": 3,
    }
    assert value["modules"][-1]["id"] == "funmill_callback"
    assert value["failure_module"]["id"] == "failure"


def test_windmill_translates_dag_to_parallel_layer():
    payloads = []

    def handler(request: httpx.Request):
        payloads.append(json.loads(request.content))
        return httpx.Response(201, text=JOB_ID)

    client = httpx.Client(
        base_url="http://windmill/api/w/admins/",
        transport=httpx.MockTransport(handler),
    )
    backend = WindmillBackend("http://unused", "admins", "token", client=client)
    backend.submit_workflow(workflow())

    modules = payloads[0]["value"]["modules"]
    assert modules[0]["id"] == "a"
    assert modules[1]["value"]["type"] == "branchall"
    assert [branch["summary"] for branch in modules[1]["value"]["branches"]] == [
        "b",
        "c",
    ]
    assert modules[2]["id"] == "funmill_result"
    assert modules[2]["value"]["input_transforms"]["results"]["expr"] == (
        '({"a": results["a"], "b": results["funmill_layer_1"][0], '
        '"c": results["funmill_layer_1"][1]})'
    )


def test_windmill_reruns_preview_flow_and_normalizes_status():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": JOB_ID,
                    "success": True,
                    "canceled": False,
                    "created_at": "2026-09-10T00:00:00Z",
                    "started_at": "2026-09-10T00:00:01Z",
                    "completed_at": "2026-09-10T00:00:02Z",
                    "duration_ms": 1000,
                    "raw_flow": {"modules": []},
                    "args": {},
                },
            )
        return httpx.Response(201, text=RERUN_ID)

    client = httpx.Client(
        base_url="http://windmill/api/w/admins/",
        transport=httpx.MockTransport(handler),
    )
    backend = WindmillBackend("http://unused", "admins", "token", client=client)

    assert backend.get_task(JOB_ID).status == TaskStatus.SUCCEEDED
    assert backend.get_progress(JOB_ID).progress == 100
    assert backend.rerun(JOB_ID) == RERUN_ID
    assert json.loads(requests[-1].content) == {"value": {"modules": []}, "args": {}}


def test_windmill_service_install_and_start(monkeypatch, tmp_path):
    binary = b"windmill-test-binary"
    monkeypatch.delenv("FUNMILL_HOME", raising=False)
    assert windmill_service._target() == (
        Path.home() / ".farfarfun" / "funmill" / "services" / "windmill" / "windmill"
    )
    monkeypatch.setenv("FUNMILL_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgres://windmill:test@localhost/windmill")
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setattr(windmill_service.platform, "system", lambda: "Linux")
    monkeypatch.setattr(windmill_service.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(windmill_service, "SHA256", hashlib.sha256(binary).hexdigest())
    monkeypatch.setattr(
        windmill_service,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(binary),
    )

    executable = windmill_service.install()
    assert executable.read_bytes() == binary
    assert executable.stat().st_mode & 0o111

    called = {}
    monkeypatch.setattr(
        windmill_service.os,
        "execve",
        lambda path, argv, env: called.update(path=path, argv=argv, env=env),
    )
    windmill_service.start()
    assert called["path"] == executable
    assert called["env"]["MODE"] == "standalone"
    assert called["env"]["PORT"] == "8813"

    monkeypatch.setenv("MODE", "worker")
    monkeypatch.delenv("PORT", raising=False)
    windmill_service.start()
    assert "PORT" not in called["env"]


def test_windmill_default_url(monkeypatch):
    monkeypatch.delenv("WINDMILL_URL", raising=False)
    backend = WindmillBackend.from_env()
    try:
        assert str(backend.client.base_url) == "http://127.0.0.1:8813/api/w/admins/"
    finally:
        backend.close()


def test_funmill_cli_uses_facade_port(monkeypatch):
    called = {}
    monkeypatch.setenv("FUNMILL_PORT", "9999")
    monkeypatch.setattr(
        funmill_cli.uvicorn,
        "run",
        lambda app, **kwargs: called.update(app=app, **kwargs),
    )
    funmill_cli.main(["start"])
    assert called == {
        "app": "funmill.api:app",
        "host": "127.0.0.1",
        "port": 8812,
    }


class FakeBackend(TaskBackend):
    name = "fake"

    def submit_task(self, task):
        assert task.language == "python"
        return JOB_ID

    def submit_workflow(self, workflow):
        return JOB_ID

    def get_task(self, task_id):
        return TaskInfo(task_id=task_id, status=TaskStatus.SUCCEEDED)

    def get_progress(self, task_id):
        return TaskProgress(task_id=task_id, progress=100)

    def get_logs(self, task_id):
        return TaskLogs(task_id=task_id, logs="done")

    def get_result(self, task_id):
        return TaskResult(task_id=task_id, result={"ok": True})

    def cancel(self, task_id, reason):
        return None

    def rerun(self, task_id):
        return RERUN_ID

    def close(self):
        return None


def test_api_is_backend_neutral_and_authenticated(monkeypatch):
    monkeypatch.setenv("FUNMILL_API_KEY", "secret")
    app.dependency_overrides[backend_dependency] = FakeBackend
    try:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/v1/tasks",
                    json={"language": "python", "source": "def main(): pass"},
                ).status_code
                == 401
            )

            response = client.post(
                "/v1/tasks",
                headers={"X-API-Key": "secret"},
                json={"language": "python", "source": "def main(): pass"},
            )
            assert response.status_code == 202
            assert response.json() == {
                "task_id": JOB_ID,
                "status": "queued",
                "rerun_of": None,
            }

            response = client.post(
                f"/v1/tasks/{JOB_ID}/rerun", headers={"X-API-Key": "secret"}
            )
            assert response.json()["task_id"] == RERUN_ID
            assert response.json()["rerun_of"] == JOB_ID
    finally:
        app.dependency_overrides.clear()

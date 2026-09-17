"""健康地址与容器健康检查脚本的集成测试（真实 HTTP 端口 + 子进程）。"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from app.main import SERVICE_NAME, SERVICE_VERSION, app

HEALTHCHECK_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "healthcheck.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server_base_url() -> str:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    ready = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/healthz", timeout=1
            ) as response:
                ready = response.status == 200
        except OSError:
            ready = False
        if ready:
            break
        time.sleep(0.1)
    if not ready:
        raise RuntimeError("测试用 uvicorn 未能在 10s 内就绪")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_healthz_returns_service_ok(server_base_url):
    status, body = _get_json(f"{server_base_url}/healthz")
    assert status == 200
    assert body == {"status": "ok"}


def test_health_alias_returns_full_health_info(server_base_url):
    status, body = _get_json(f"{server_base_url}/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == SERVICE_NAME
    assert body["version"] == SERVICE_VERSION


def test_root_returns_health_info_not_404(server_base_url):
    status, body = _get_json(f"{server_base_url}/")
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == SERVICE_NAME
    assert "/healthz" in body["endpoints"]
    assert "/api/v1/adjudicate" in body["endpoints"]


def test_unknown_path_still_404(server_base_url):
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{server_base_url}/docker-health", timeout=5)
    assert exc.value.code == 404


def test_healthcheck_script_exits_zero_against_running_api(server_base_url):
    env = {**os.environ, "HEALTHCHECK_URL": f"{server_base_url}/healthz"}
    result = subprocess.run(
        [sys.executable, str(HEALTHCHECK_SCRIPT)],
        env=env, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_healthcheck_script_exits_one_when_api_unreachable():
    dead_port = _free_port()  # 取到后立即释放，该端口无服务监听
    env = {**os.environ, "HEALTHCHECK_URL": f"http://127.0.0.1:{dead_port}/healthz"}
    result = subprocess.run(
        [sys.executable, str(HEALTHCHECK_SCRIPT)],
        env=env, capture_output=True, timeout=10,
    )
    assert result.returncode == 1

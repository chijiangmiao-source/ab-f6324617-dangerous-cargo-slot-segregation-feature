#!/usr/bin/env python3
"""容器健康检查脚本（Docker HEALTHCHECK 入口）。

访问健康地址，HTTP 200 且 JSON 中 status == "ok" 时以 0 退出，
其余任何情况（连接失败、超时、非 200、状态异常）一律以 1 退出。
不依赖第三方库，python:3.13-slim 镜像内可直接运行。

环境变量：
    HEALTHCHECK_URL  健康地址，默认 http://127.0.0.1:8000/healthz
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8000/healthz")
TIMEOUT = 3.0


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT) as response:
            if response.status != 200:
                return 1
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return 1
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())

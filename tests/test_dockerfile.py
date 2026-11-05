"""Deployment contract tests for the Docker image (no daemon needed).

These read the files as text on purpose: `docker build` is not available in CI,
but the two properties that decide whether the container leaks processes and
whether it shuts down gracefully are both visible in the files.

The bug these pin: Docker starts the `HEALTHCHECK` (and every `docker exec`)
inside the container's PID namespace, with a parent that lives outside it. The
process is reparented to PID 1, and a PID 1 that never calls `wait()` leaves it
behind as a zombie, one every 30 seconds. Running the server under an init
fixes it, so an init must be present either in the image or in the compose
service, and the entrypoint must actually be the init.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"


def _instructions() -> list[str]:
    """Dockerfile instructions with comments and line continuations resolved."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    joined = text.replace("\\\n", " ")
    return [
        line.strip()
        for line in joined.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_image_installs_an_init():
    assert any(
        instruction.startswith("RUN") and "tini" in instruction
        for instruction in _instructions()
    ), "the runtime stage must install tini, or nothing reaps reparented processes"


def test_entrypoint_runs_the_server_under_the_init():
    entrypoints = [
        instruction
        for instruction in _instructions()
        if instruction.startswith("ENTRYPOINT")
    ]
    assert entrypoints, "no ENTRYPOINT"
    argv = json.loads(entrypoints[-1].split(" ", 1)[1])
    assert argv[0].endswith("tini"), (
        "the server must be exec'd by tini, not run as PID 1 itself: " + repr(argv)
    )
    # `tini -- <command>`: the separator has to be there, otherwise tini treats
    # the server's arguments as its own.
    assert argv[1] == "--", argv
    assert argv[2].endswith("tapebasis"), argv


def test_entrypoint_still_starts_the_http_server():
    cmds = [
        instruction for instruction in _instructions() if instruction.startswith("CMD")
    ]
    assert cmds, "no CMD"
    argv = json.loads(cmds[-1].split(" ", 1)[1])
    assert argv[0] == "streamable-http"
    assert "--host" in argv and argv[argv.index("--host") + 1] == "0.0.0.0"
    assert "--port" in argv and argv[argv.index("--port") + 1] == "8000"


def test_compose_also_enables_init():
    """Compose covers the case where the image is swapped for one without tini."""
    lines = COMPOSE.read_text(encoding="utf-8").splitlines()
    service = next(
        index for index, line in enumerate(lines) if line.strip() == "tapebasis:"
    )
    own = []
    for line in lines[service + 1:]:
        if line.strip() and not line.startswith(" "):
            break  # next top-level key: the service block ended
        if line.strip().startswith("init:"):
            own.append(line)
    assert own == ["    init: true"], own

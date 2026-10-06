"""Serve the operator docs as markdown for the UI's Help view.

- ``GET /api/docs`` -> ``[{"name": ..., "title": ...}]``
- ``GET /api/docs/{name}`` -> the markdown text (``text/markdown``).

Only a fixed whitelist is served: the basenames of ``<repo>/docs/*.md``
(``docs/README.md`` as ``README``), the per-setup pages ``<repo>/docs/<setup>/*.md``
as ``<setup>-<basename>`` (``docs/zed/panic.md`` as ``zed-panic``), and
``pi_camera/README.md`` as ``pi-camera`` and ``AGENTS.md`` (naming rules) as
``AGENTS``. The running setup's own pages are
also served under their bare basename (``panic`` = ``zed-panic`` on the ZED
Box), so the UI's PANIC button opens the right page. ``name`` is looked up in
that mapping, never joined into a path, so there is no traversal.
"""

from __future__ import annotations

import re
from pathlib import Path

from aiohttp import web

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Per-setup doc folders (docs/<setup>/), named like the setup's file prefix (zed-config-lab.yml)
SETUPS = ("zed", "pi")


def whitelist(repo_root: Path, setup: str | None = None) -> dict[str, Path]:
    docs_dir = repo_root / "docs"
    files: dict[str, Path] = {}
    if docs_dir.is_dir():
        for path in sorted(docs_dir.glob("*.md")):
            if path.is_file() and _NAME_RE.match(path.stem):
                files[path.stem] = path
        for name in SETUPS:
            for path in sorted((docs_dir / name).glob("*.md")):
                if path.is_file() and _NAME_RE.match(path.stem):
                    files[f"{name}-{path.stem}"] = path
                    if name == setup:
                        files.setdefault(path.stem, path)
    agents = repo_root / "AGENTS.md"
    if agents.is_file():
        files["AGENTS"] = agents
    pi_readme = repo_root / "pi_camera" / "README.md"
    if pi_readme.is_file():
        files["pi-camera"] = pi_readme
    return files


def _title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return path.stem


def setup_of(camera: object) -> str:
    """The docs setup for a vision config's ``camera:`` section: ``zed`` for the ZED SDK driver, else ``pi``."""
    driver = camera.get("driver") if isinstance(camera, dict) else None
    return "zed" if str(driver).upper() == "ZED" else "pi"


def register(
    http_app: web.Application, repo_root: Path, setup: str | None = None
) -> None:
    async def list_handler(_: web.Request) -> web.Response:
        return web.json_response(
            [
                {"name": name, "title": _title(path)}
                for name, path in whitelist(repo_root, setup).items()
            ]
        )

    async def doc_handler(request: web.Request) -> web.Response:
        path = whitelist(repo_root, setup).get(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound(text="unknown document")
        return web.Response(
            text=path.read_text(encoding="utf-8"),
            content_type="text/markdown",
            charset="utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    http_app.router.add_get("/api/docs", list_handler)
    http_app.router.add_get("/api/docs/{name}", doc_handler)

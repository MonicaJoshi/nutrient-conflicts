# .config/opencode/AGENTS.md APPLIED
# NutrientConflictsEvaluation Project AGENTS.md APPLIED
# PYTHON SKILL APPLIED
"""Launch the React GUI (Vite build) through the Fastify proxy server.

The Fastify server serves ``gui/dist`` and proxies ``/api/*`` to the FastAPI
process, so the browser only ever talks to one origin. Both processes are started
from here so a single ``nutrient-conflicts gui`` command is enough.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .config import Config


class GuiError(RuntimeError):
    """Raised when the GUI cannot be built or started."""


def _npm(config: Config) -> str:
    command = config.get("gui.npm.command", "npm")
    found = shutil.which(command)
    if found is None:
        raise GuiError(f"{command!r} was not found on PATH; install Node.js 18+ to use the GUI")
    return found


def _run(config: Config, args: list[str]) -> None:
    npm = _npm(config)
    project_root = config.root
    result = subprocess.run([npm, *args], cwd=project_root / "gui", check=False)
    if result.returncode != 0:
        raise GuiError(f"npm {' '.join(args)} failed with exit code {result.returncode}")


def ensure_build(config: Config, force: bool = False) -> Path:
    dist = config.path("gui.dist.dir")
    if (dist / "index.html").is_file() and not force:
        return dist
    if not (config.root / "gui" / "package.json").is_file():
        raise GuiError("gui/package.json is missing")
    if not (config.root / "gui" / "node_modules").is_dir():
        _run(config, ["install"])
    _run(config, ["run", "build"])
    if not (dist / "index.html").is_file():
        raise GuiError(f"the React build did not produce {dist / 'index.html'}")
    return dist


def _start_api(config: Config) -> threading.Thread:
    import uvicorn

    from .api import create_app

    app = create_app(config)
    host = config.get("api.host", "127.0.0.1")
    port = config.get_int("api.port", 8077)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))

    thread = threading.Thread(target=server.run, name="nutrient-conflicts-api", daemon=True)
    thread.start()
    return thread


def run_gui(config: Config, force_build: bool = False) -> int:
    ensure_build(config, force=force_build)
    _start_api(config)
    environment = dict(os.environ)
    environment["PORT"] = str(config.get_int("gui.port", 5173))
    environment["HOST"] = config.get("gui.host", "127.0.0.1")
    environment["API_BASE_URL"] = config.get("gui.api.base.url", "http://127.0.0.1:8077")
    environment["API_PREFIX"] = config.get("api.prefix", "/api/v1")
    environment["GUI_DIST_DIR"] = str(config.path("gui.dist.dir"))
    process = subprocess.Popen([shutil.which("node") or "node", str(config.root / "gui" / "server" / "server.js")],
                               cwd=config.root, env=environment)
    try:
        return process.wait()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        process.terminate()
        return 0


def main() -> int:  # pragma: no cover - console entry point
    from .config import ConfigError

    try:
        return run_gui(Config.load())
    except (ConfigError, GuiError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - surfaced to the console
        print(f"GUI failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

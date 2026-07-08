# Server Manager

A local dashboard to register, monitor, start, and stop other local Windows servers from one page.

## Prerequisites

- **Windows 10/11** — the tool shells out to `cmd`, `netstat`, and `taskkill`, so it's Windows-only.
- **Python 3.10 or later**, with `python` and `pip` available on your `PATH`. Check with:
  ```
  python --version
  ```
- Each server you plan to register needs its own working `start.bat` (or similar) that you'd normally double-click to run it manually.

## Setup

Clone the repo, then from the project folder run:

```
setup.bat
```

This creates a `venv` and installs the dependencies from `requirements.txt` (`fastapi`, `uvicorn`). Run this once, the first time only.

## Run

```
start.bat
```

Opens a console window running the dashboard and launches your browser to `http://localhost:8002` once it's ready. On first run, `servers.json` is created empty — add your servers from the UI (name, full path to their `.bat` file, port).

To stop the dashboard itself, close the "Server Manager" console window it opened, or `Ctrl+C` inside it.

## How it works

- Data is stored in a flat `servers.json` file (gitignored — it's local, machine-specific data, not part of the app).
- **Start**: launches the registered `.bat` file as an independent process, detached from the dashboard.
- **Stop**: finds whatever process is listening on the registered port (via `netstat`) and kills it (`taskkill /F /T`). This is deliberately port-based rather than PID-based, since a `.bat` can spawn further child processes — the port is the only reliable handle to "the real running server" regardless of what the script does internally.
- **Status**: polled every ~3s by attempting a socket connection to the registered port.

## Security

This tool has **no authentication** and will execute any local file path you register, on request, over HTTP. That's intentional — it's meant to be a trusted, single-user, localhost-only tool. This is only safe as long as:

- It stays bound to `127.0.0.1` (see the comment in `main.py`). **Never** change the host to `0.0.0.0` and never expose this via a tunnel (e.g. Cloudflare Tunnel), reverse proxy, or port-forward. Doing so would let anyone on the internet start/stop/remove arbitrary processes on your machine.
- You only register `.bat` paths you trust.

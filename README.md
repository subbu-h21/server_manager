# Server Manager

A local dashboard to register, monitor, start, and stop other local Windows servers from one page.

## Prerequisites

- **Windows 10/11** — it starts servers with `cmd`, stops them with `taskkill`, and asks Windows itself which program listens on which port, so it's Windows-only.
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

Opens a console window running the dashboard and launches your browser to `http://localhost:8770` once it's ready (if it's already running in the background, it just opens the page). On first run, `servers.json` is created empty — add your servers with the **Add a server** form below the list (name, full path to their `.bat` file, port).

Each server shows in words where it runs (or why it doesn't), with **Open** while it runs and the one of **Start** / **Stop** that applies. Its **⋯** menu has **Edit**, **Include in Start all** (servers left out show "Not in Start all") and **Remove**, which asks first and only takes it off the list: a running server keeps running and its files stay. The page looks like Windows 11 Settings, like Friday's control panel.

To stop the dashboard itself, close the "Server Manager" console window it opened, or `Ctrl+C` inside it.

## Run in the background at sign-in

```
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```

Makes a scheduled task, "Server Manager", that runs `autostart.py` with `pythonw.exe` when you sign in: Server Manager runs with no window and no browser tab, and starts again after a few seconds if it stops. Its output goes to `server.log`. Undo with `Unregister-ScheduledTask -TaskName "Server Manager" -Confirm:$false`.

## How it works

- Data is stored in a flat `servers.json` file (gitignored — it's local, machine-specific data, not part of the app).
- **Whose port is it**: a server's folder is the folder holding its `.bat`. For each program listening on a port, `processes.py` looks at it and its parent processes: a venv's `python.exe` inside a server's folder, `node` running a script from its `node_modules`, a `cmd` whose command line names the folder, or a program whose current folder is inside it, makes it that server's. (So a program you start some other way from inside a server's folder, on its port or one of the 20 above it, counts as that server too.) The `.bat` usually starts the server in a new console and exits, so the port and that chain are the only handles left on "the real running server".
- **Status**, polled every ~3s: *running* (a program from its folder listens on its port), *running on another port instead* (it moved, e.g. because its port was taken: found on one of the 20 ports above its own, where dev servers like Vite move; other programs run from its folder, like a Codex session on a random high port, don't count), *port in use by …* (another program holds its port: a registered server by name, otherwise the program and the folder it seems to run from), *starting* (started from here in the last 2 minutes, not listening yet), or stopped.
- **Start**: launches the registered `.bat` file as an independent process, detached from the dashboard. Refused while it runs, while it's starting, and while another program holds its port (the API's `{"replace": true}` stops that program first; `{"anyway": true}` starts it regardless, for a dev server that moves to the next free port).
- **Stop**: kills (`taskkill /F /T`) only that server's own programs listening on its port (or where it moved); never a program from elsewhere that happens to hold its port. It also closes the console window its `.bat` opened for it (`start "..." cmd /k "..."`), unless something else still runs in that window; never a terminal you opened yourself, nor Server Manager's own. Then it checks, 3 seconds later, that none of them listens any more; if one still does (Windows refused to end it, or something started it again), Stop answers "still running on port …" instead of OK.
- **Open**: shown only while the server runs; opens `http://localhost:<port>/` in a new tab (the port it moved to, if it did).

## Security

This tool has **no authentication** and will execute any local file path you register, on request, over HTTP. That's intentional — it's meant to be a trusted, single-user, localhost-only tool. This is only safe as long as:

- It stays bound to `127.0.0.1` (see the comment in `main.py`). **Never** change the host to `0.0.0.0` and never expose this via a tunnel (e.g. Cloudflare Tunnel), reverse proxy, or port-forward. Doing so would let anyone on the internet start/stop/remove arbitrary processes on your machine.
- You only register `.bat` paths you trust.

It also refuses requests whose `Host` isn't `127.0.0.1:8770` / `localhost:8770`, or that come from another site's page (`Origin`), so websites you visit can't reach it (DNS rebinding) or send it requests.

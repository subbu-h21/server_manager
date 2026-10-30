import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from processes import Holder, Ports, folder_key, kill, leftover_window

BASE_DIR = Path(__file__).parent
SERVERS_FILE = BASE_DIR / "servers.json"
INDEX_FILE = BASE_DIR / "index.html"
PORT = 8770  # not 8002: common dev ports would clash with the servers this manages
START_WAIT = 120.0  # a started server counts as "starting" this long (frontend builds take ~30 s)
FREE_WAIT = 10.0  # after stopping whatever held a port, how long to wait for the port to be free
STOP_CHECK = 3.0  # after stopping a server, check this long later that it's really gone from its ports
MOVE_WINDOW = 20  # a server found listening up to this far above its port has moved there

app = FastAPI()
starting: dict[str, float] = {}  # server id -> until when it counts as starting


@app.middleware("http")
async def only_this_pc(request: Request, call_next):
    # The API starts and kills programs, so only this page (and programs on this PC) may use it: the Host check stops
    # other websites reaching it through DNS rebinding, the Origin check stops them sending it requests.
    ours = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    origin = request.headers.get("origin")
    if request.headers.get("host") not in ours or (origin is not None and origin.split("://", 1)[-1] not in ours):
        return JSONResponse({"detail": "not allowed"}, status_code=403)
    return await call_next(request)


def seed_servers_file():
    if SERVERS_FILE.exists():
        return
    SERVERS_FILE.write_text(json.dumps([], indent=2))


def load_servers():
    if not SERVERS_FILE.exists():
        return []
    return json.loads(SERVERS_FILE.read_text())


def save_servers(servers):
    SERVERS_FILE.write_text(json.dumps(servers, indent=2))


def server_folder(server) -> str:
    return os.path.dirname(server["start_script"])


def look(servers) -> Ports:
    return Ports([server_folder(s) for s in servers])


def describe(holder: Holder, servers) -> dict:
    """Who holds a port, for the page and for Friday: a registered server by name, or a program and its folder."""
    names = [s["name"] for s in servers if folder_key(server_folder(s)) == holder.folder]
    return {"pid": holder.pid, "program": holder.name, "server": names[0] if names else None,
            "folder": holder.hint}


def own_ports(server, servers, ports: Ports) -> dict[int, list[int]]:
    """port -> process IDs of the programs from this server's folder listening on its port, or just above it (where
    dev servers move when theirs is taken: Vite 5173 -> 5174). Other programs run from the folder (a Codex or test
    session, on a random high port) don't count, nor do the ports of other servers registered from the same folder."""
    key = folder_key(server_folder(server))
    theirs = {s["port"] for s in servers if s["id"] != server["id"] and folder_key(server_folder(s)) == key}
    return {port: pids for port, pids in ports.ports_of(server_folder(server)).items()
            if port == server["port"] or (server["port"] < port <= server["port"] + MOVE_WINDOW and port not in theirs)}


def server_status(server, servers, ports: Ports) -> dict:
    """running: a program from its folder listens on its port. other_port: one listens, but only elsewhere (it moved,
    e.g. because its port was taken). port_taken: another program holds its port. starting: started from here in the
    last START_WAIT seconds, not listening yet. stopped: none of these."""
    own = own_ports(server, servers, ports)
    others = [h for h in ports.holders(server["port"]) if h.folder != folder_key(server_folder(server))]
    if server["port"] in own:
        state = "running"
        starting.pop(server["id"], None)
    elif own:
        state = "other_port"
        starting.pop(server["id"], None)
    elif others:
        state = "port_taken"
    elif starting.get(server["id"], 0) > time.monotonic():
        state = "starting"
    else:
        state = "stopped"
        starting.pop(server["id"], None)
    return {"state": state, "running": state in ("running", "other_port"), "ports": list(own),
            "holder": describe(others[0], servers) if others else None}


def holder_text(holder: dict) -> str:
    if holder["server"]:
        return holder["server"]
    return holder["program"] + (f" from {holder['folder']}" if holder["folder"] else "")


class ServerCreate(BaseModel):
    name: str
    start_script: str
    port: int


class EnabledUpdate(BaseModel):
    enabled: bool


class StartOptions(BaseModel):
    replace: bool = False  # stop whatever else holds the port first
    anyway: bool = False  # start although another program holds the port (a dev server may move to the next one)


seed_servers_file()


def checked(server: ServerCreate) -> ServerCreate:
    """What adding or editing a server accepts: a name (trimmed), the full path of an existing start.bat, a port."""
    name = server.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is empty")
    if not os.path.isabs(server.start_script):
        raise HTTPException(status_code=400, detail="start_script must be a full path")
    if not os.path.isfile(server.start_script):
        raise HTTPException(status_code=400, detail="start_script path does not exist")
    if not 1 <= server.port <= 65535:
        raise HTTPException(status_code=400, detail="port must be 1-65535")
    return server.model_copy(update={"name": name})


@app.get("/servers")
def list_servers():
    servers = load_servers()
    ports = look(servers)
    return [
        {**s, "enabled": s.get("enabled", True), **server_status(s, servers, ports)}
        for s in servers
    ]


@app.post("/servers")
def add_server(server: ServerCreate):
    server = checked(server)
    servers = load_servers()
    new_server = {
        "id": str(uuid.uuid4()),
        "name": server.name,
        "start_script": server.start_script,
        "port": server.port,
        "enabled": True,
    }
    servers.append(new_server)
    save_servers(servers)
    return new_server


@app.patch("/servers/{server_id}/enabled")
def set_enabled(server_id: str, body: EnabledUpdate):
    servers = load_servers()
    existing = next((s for s in servers if s["id"] == server_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="server not found")
    existing["enabled"] = body.enabled
    save_servers(servers)
    return existing


@app.put("/servers/{server_id}")
def update_server(server_id: str, server: ServerCreate):
    server = checked(server)
    servers = load_servers()
    existing = next((s for s in servers if s["id"] == server_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="server not found")
    existing["name"] = server.name
    existing["start_script"] = server.start_script
    existing["port"] = server.port
    save_servers(servers)
    return existing


@app.delete("/servers/{server_id}")
def remove_server(server_id: str):
    servers = load_servers()
    filtered = [s for s in servers if s["id"] != server_id]
    if len(filtered) == len(servers):
        raise HTTPException(status_code=404, detail="server not found")
    save_servers(filtered)
    starting.pop(server_id, None)
    return {"ok": True}


@app.post("/servers/{server_id}/start")
def start_server(server_id: str, options: StartOptions | None = None):
    servers = load_servers()
    server = next((s for s in servers if s["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")

    start_script = server["start_script"]
    if not os.path.isfile(start_script):
        raise HTTPException(status_code=400, detail="start_script path no longer exists")

    status = server_status(server, servers, look(servers))
    if status["state"] == "running":
        raise HTTPException(status_code=409, detail=f"already running on port {server['port']}")
    if status["state"] == "other_port":
        raise HTTPException(status_code=409, detail="already running, on port "
                            + ", ".join(str(p) for p in status["ports"]))
    if status["state"] == "starting":
        raise HTTPException(status_code=409, detail="already starting")
    if status["state"] == "port_taken" and not (options and options.anyway):
        if not (options and options.replace):
            raise HTTPException(status_code=409,
                                detail=f"port {server['port']} is in use by {holder_text(status['holder'])}")
        for holder in look(servers).holders(server["port"]):
            kill(holder.pid)
        deadline = time.monotonic() + FREE_WAIT
        while look(servers).holders(server["port"]) and time.monotonic() < deadline:
            time.sleep(0.5)
        if look(servers).holders(server["port"]):
            raise HTTPException(status_code=409, detail=f"port {server['port']} is still in use after stopping "
                                                        f"{holder_text(status['holder'])}")

    subprocess.Popen(
        ["cmd", "/c", start_script],
        cwd=os.path.dirname(start_script),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE,
    )
    starting[server_id] = time.monotonic() + START_WAIT
    return {"ok": True}


@app.post("/servers/{server_id}/stop")
def stop_server(server_id: str):
    servers = load_servers()
    server = next((s for s in servers if s["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")

    starting.pop(server_id, None)
    ports = look(servers)
    status = server_status(server, servers, ports)
    if not status["running"]:
        if status["state"] == "port_taken":
            raise HTTPException(status_code=409, detail=f"not running: port {server['port']} is in use by "
                                                        f"{holder_text(status['holder'])}, not by {server['name']}")
        raise HTTPException(status_code=400, detail="no process listening on port")

    # Only this server's own programs, on whichever ports they listen
    pids = sorted({pid for pids in own_ports(server, servers, ports).values() for pid in pids})
    windows = {w.pid for pid in pids if (w := leftover_window(ports.holder(pid).chain, ports.processes))}
    for pid in pids:
        kill(pid)
    for pid in windows:  # and the console window it ran in, which would stay open (cmd /k). Only the window's cmd:
        kill(pid, tree=False)  # anything else still running in it keeps running, and keeps the window open

    # Check it really stopped: a few seconds later, none of it listens any more
    time.sleep(STOP_CHECK)
    still = own_ports(server, servers, look(servers))
    if still:
        raise HTTPException(status_code=409, detail=f"still running on port {', '.join(map(str, sorted(still)))}, "
                                                    f"{STOP_CHECK:g} seconds after Stop")
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(INDEX_FILE)


if __name__ == "__main__":
    import uvicorn

    # host must stay 127.0.0.1 — this API can launch/kill arbitrary local
    # processes with no authentication. Never bind to 0.0.0.0 or expose via
    # a tunnel/port-forward.
    uvicorn.run(app, host="127.0.0.1", port=PORT)

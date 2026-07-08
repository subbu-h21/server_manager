import json
import os
import re
import socket
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
SERVERS_FILE = BASE_DIR / "servers.json"
INDEX_FILE = BASE_DIR / "index.html"

app = FastAPI()


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


def is_running(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=0.3):
            return True
    except OSError:
        return False


def find_pid_on_port(port: int) -> str | None:
    result = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        if local_addr.rsplit(":", 1)[-1] == str(port):
            return parts[-1]
    return None


class ServerCreate(BaseModel):
    name: str
    start_script: str
    port: int


class EnabledUpdate(BaseModel):
    enabled: bool


seed_servers_file()


@app.get("/servers")
def list_servers():
    servers = load_servers()
    return [
        {**s, "enabled": s.get("enabled", True), "running": is_running(s["port"])}
        for s in servers
    ]


@app.post("/servers")
def add_server(server: ServerCreate):
    if not os.path.isfile(server.start_script):
        raise HTTPException(status_code=400, detail="start_script path does not exist")
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
    if not os.path.isfile(server.start_script):
        raise HTTPException(status_code=400, detail="start_script path does not exist")
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
    return {"ok": True}


@app.post("/servers/{server_id}/start")
def start_server(server_id: str):
    servers = load_servers()
    server = next((s for s in servers if s["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")

    start_script = server["start_script"]
    if not os.path.isfile(start_script):
        raise HTTPException(status_code=400, detail="start_script path no longer exists")

    subprocess.Popen(
        ["cmd", "/c", start_script],
        cwd=os.path.dirname(start_script),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE,
    )
    return {"ok": True}


@app.post("/servers/{server_id}/stop")
def stop_server(server_id: str):
    servers = load_servers()
    server = next((s for s in servers if s["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")

    pid = find_pid_on_port(server["port"])
    if pid is None:
        raise HTTPException(status_code=400, detail="no process listening on port")

    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True, text=True)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(INDEX_FILE)


if __name__ == "__main__":
    import uvicorn

    # host must stay 127.0.0.1 — this API can launch/kill arbitrary local
    # processes with no authentication. Never bind to 0.0.0.0 or expose via
    # a tunnel/port-forward.
    uvicorn.run(app, host="127.0.0.1", port=8002)

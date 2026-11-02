"""순찰로봇 관제 서버 (노트북에서 실행)  — 담당: 서버/웹

- XIAO 노드  → POST /api/event      : 이벤트(+사진) 수신 → 웹 알림 + 로봇 출동
- XIAO 노드  → POST /api/heartbeat  : 노드 생존 신호 (10초마다)
- 로봇      ↔ WS   /ws/robot        : 상태 보고 / 명령 수신
- 웹 대시보드 ↔ WS   /ws/web          : 실시간 알림·상태
- 웹 대시보드 → POST /api/command    : 순찰/추종/정지/출동/등록 명령

실행: uvicorn app:app --host 0.0.0.0 --port 8000

메시지 형식 (JSON)
  서버 → 로봇 : {"cmd": "patrol" | "follow" | "stop" | "register" | "reset_pos"}
               {"cmd": "dispatch", "node": "B"}
  로봇 → 서버 : {"type": "status", "mode": ..., "node": ..., "target": ..., "dist": ..., "face": ...}
               {"type": "alert", "level": "info" | "warn", "msg": "..."}
  서버 → 웹   : {"type": "init" | "robot" | "event" | "alert" | "nodes", ...}
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
SNAP_DIR = BASE / "snapshots"
SNAP_DIR.mkdir(exist_ok=True)

NODES = ["A", "B", "C", "D"]  # robot/config.py 의 NODES 와 같아야 함
AUTO_DISPATCH = True          # 이벤트가 오면 자동으로 로봇 출동
EVENT_COOLDOWN = 10.0         # 같은 노드 이벤트 중복 무시 (초)
NODE_TIMEOUT = 30.0           # 이 시간 동안 heartbeat 없으면 오프라인 표시
MAX_LOG = 100

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(node_watchdog())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/snapshots", StaticFiles(directory=SNAP_DIR), name="snapshots")

web_clients: set[WebSocket] = set()
robot_ws: WebSocket | None = None
robot_ip: str | None = None
robot_status: dict = {"online": False}
nodes: dict = {n: {"last_seen": 0.0, "last_event": None, "level": None} for n in NODES}
log: list[dict] = []  # 이벤트 + 알림 기록 (최신이 앞)


# ───────────────────────── 공통 ─────────────────────────

async def broadcast(msg: dict):
    dead = []
    for ws in web_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        web_clients.discard(ws)


def add_log(entry: dict):
    log.insert(0, entry)
    del log[MAX_LOG:]


async def alert(msg: str, level: str = "info"):
    entry = {"type": "alert", "level": level, "msg": msg, "ts": time.time()}
    add_log(entry)
    await broadcast(entry)


async def send_robot(cmd: dict) -> bool:
    if robot_ws is None:
        return False
    try:
        await robot_ws.send_json(cmd)
        return True
    except Exception:
        return False


def robot_payload() -> dict:
    stream = f"http://{robot_ip}:8080/stream" if robot_ip and robot_status.get("online") else None
    return {"type": "robot", **robot_status, "stream": stream}


def nodes_payload() -> dict:
    now = time.time()
    return {
        "type": "nodes",
        "nodes": {n: {**v, "online": now - v["last_seen"] < NODE_TIMEOUT} for n, v in nodes.items()},
    }


# ───────────────────────── HTTP ─────────────────────────

@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.post("/api/event")
async def post_event(request: Request, node: str, type: str = "sound", level: int | None = None):
    """XIAO 노드 이벤트. 본문은 JPEG 사진(없어도 됨)."""
    node = node.upper()
    if node not in nodes:
        raise HTTPException(400, f"unknown node {node}")

    now = time.time()
    last = nodes[node]["last_event"]
    if last and now - last["ts"] < EVENT_COOLDOWN:
        return {"ok": True, "ignored": "cooldown"}

    body = await request.body()
    image = None
    if body:
        name = time.strftime("%Y%m%d_%H%M%S") + f"_{node}.jpg"
        (SNAP_DIR / name).write_bytes(body)
        image = f"/snapshots/{name}"

    event = {"type": "event", "node": node, "kind": type, "level": level, "image": image, "ts": now}
    nodes[node]["last_seen"] = now
    nodes[node]["last_event"] = event
    add_log(event)
    await broadcast(event)
    await broadcast(nodes_payload())

    dispatched = False
    if AUTO_DISPATCH:
        dispatched = await send_robot({"cmd": "dispatch", "node": node})
        if dispatched:
            await alert(f"{node} 지점으로 로봇 출동", "info")
        else:
            await alert(f"{node} 지점 이벤트 — 로봇 미연결로 출동 불가", "warn")
    return {"ok": True, "dispatched": dispatched}


@app.post("/api/heartbeat")
async def post_heartbeat(node: str, level: int | None = None):
    node = node.upper()
    if node not in nodes:
        raise HTTPException(400, f"unknown node {node}")
    was_online = time.time() - nodes[node]["last_seen"] < NODE_TIMEOUT
    nodes[node]["last_seen"] = time.time()
    nodes[node]["level"] = level
    if not was_online:
        await broadcast(nodes_payload())
    return {"ok": True}


@app.post("/api/command")
async def post_command(request: Request):
    """웹 → 로봇 명령. 예: {"cmd": "dispatch", "node": "B"}"""
    cmd = await request.json()
    if cmd.get("cmd") not in {"patrol", "follow", "stop", "dispatch", "register", "reset_pos"}:
        raise HTTPException(400, "unknown cmd")
    if cmd["cmd"] == "dispatch" and cmd.get("node") not in nodes:
        raise HTTPException(400, "unknown node")
    if not await send_robot(cmd):
        raise HTTPException(503, "로봇이 연결되어 있지 않습니다")
    return {"ok": True}


# ─────────────────────── WebSocket ───────────────────────

@app.websocket("/ws/web")
async def ws_web(ws: WebSocket):
    await ws.accept()
    web_clients.add(ws)
    await ws.send_json({**nodes_payload(), "type": "init", "log": log, "robot": robot_payload()})
    try:
        while True:
            await ws.receive_text()  # 대시보드는 명령을 /api/command 로 보냄. 여기선 연결 유지만.
    except WebSocketDisconnect:
        pass
    finally:
        web_clients.discard(ws)


@app.websocket("/ws/robot")
async def ws_robot(ws: WebSocket):
    global robot_ws, robot_ip, robot_status
    await ws.accept()
    robot_ws, robot_ip = ws, ws.client.host
    robot_status = {"online": True}
    await alert(f"로봇 연결됨 ({robot_ip})")
    await broadcast(robot_payload())
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "status":
                robot_status = {"online": True, **{k: v for k, v in msg.items() if k != "type"}}
                await broadcast(robot_payload())
            elif msg.get("type") == "alert":
                await alert(msg.get("msg", ""), msg.get("level", "info"))
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    finally:
        if robot_ws is ws:
            robot_ws = None
            robot_status = {"online": False}
            await alert("로봇 연결 끊김", "warn")
            await broadcast(robot_payload())


async def node_watchdog():
    """노드 온라인/오프라인 변화를 주기적으로 대시보드에 반영"""
    prev = None
    while True:
        cur = {n: p["online"] for n, p in nodes_payload()["nodes"].items()}
        if cur != prev:
            await broadcast(nodes_payload())
            prev = cur
        await asyncio.sleep(5)

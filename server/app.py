"""경비로봇 관제 서버 (노트북에서 실행)  — 담당: 서버/웹

- 감지 센서(XIAO) → POST /api/event?sensor=..  : 이벤트(+사진) 수신 → 웹 알림 + 로봇 경비 시작
- 감지 센서(XIAO) → POST /api/heartbeat        : 센서 생존 신호 (10초마다)
- 로봇          ↔ WS   /ws/robot              : 상태 보고 / 명령 수신
- 로봇          → POST /api/photo             : 로봇이 찍은 사진 (JPEG, 얼굴 확인 시 10초마다)
- 웹 대시보드    ↔ WS   /ws/web                : 실시간 알림·상태
- 웹 대시보드    → POST /api/command           : 경비 시작 / 정지
- 웹 대시보드    → POST /api/capture           : 캡처 — 로봇 카메라 원본 사진을 받아 기록에 남김
- 웹 대시보드    → POST /api/ack               : 센서 이벤트 확인 처리

실행: uvicorn app:app --host 0.0.0.0 --port 8000

메시지 형식 (JSON)
  서버 → 로봇 : {"cmd": "guard" | "stop"}, 센서 이벤트면 {"cmd": "guard", "source": "<센서 이름>"}
  로봇 → 서버 : {"type": "status", "mode", "nav", "person", "face", "dist", "move", "pan", "tilt", "health"}
               {"type": "alert", "level": "info" | "warn" | "critical", "msg": "..."}
  서버 → 웹   : {"type": "init" | "robot" | "event" | "photo" | "alert" | "ack", ...}
"""
import asyncio
import itertools
import json
import re
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
SNAP_DIR = BASE / "snapshots"
SNAP_DIR.mkdir(exist_ok=True)

AUTO_GUARD = True             # 센서 이벤트가 오면 로봇 경비 시작
EVENT_COOLDOWN = 10.0         # 같은 센서 이벤트 중복 무시 (초)
MAX_LOG = 150
ROBOT_SILENT_SEC = 5.0        # 로봇이 연결돼 있는데 이 시간(초) 넘게 상태 보고가 없으면 '응답 없음' (프로그램 멈춤 의심)
COMMANDS = {"guard", "stop"}


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(watchdog())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/snapshots", StaticFiles(directory=SNAP_DIR), name="snapshots")

web_clients: set[WebSocket] = set()
robot_ws: WebSocket | None = None
robot_ip: str | None = None
robot_status: dict = {"online": False}
sensors: dict = {}              # 센서 이름 → {"last_seen", "last_event"}
log: list[dict] = []            # 이벤트 + 알림 + 사진 기록 (최신이 앞)
event_ids = itertools.count(1)  # 이벤트 번호 (확인 처리에 사용)
robot_last_status = 0.0         # 로봇이 마지막으로 상태를 보낸 시각
robot_silent = False            # '응답 없음' 알림을 이미 보냈는지
cmd_hosts = {"127.0.0.1", "::1"}  # 로봇 명령을 보낸 적 있는 기기 (처음 보는 기기면 알림)


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


def sensor_name(name: str) -> str:
    name = name.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{1,16}", name):
        raise HTTPException(400, "센서 이름은 영문·숫자 16자 이내")
    return name


def save_jpeg(body: bytes, tag: str) -> str:
    name = time.strftime("%Y%m%d_%H%M%S") + f"_{tag}.jpg"
    (SNAP_DIR / name).write_bytes(body)
    return f"/snapshots/{name}"


# ───────────────────────── HTTP ─────────────────────────

@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.post("/api/event")
async def post_event(request: Request, sensor: str = "", node: str = "", type: str = "sound", level: int | None = None):
    """감지 센서 이벤트. 본문은 JPEG 사진(없어도 됨). node= 는 예전 이름"""
    name = sensor_name(sensor or node)
    now = time.time()
    s = sensors.setdefault(name, {"last_seen": 0.0, "last_event": None})
    if s["last_event"] and now - s["last_event"]["ts"] < EVENT_COOLDOWN:
        return {"ok": True, "ignored": "cooldown"}

    body = await request.body()
    event = {"type": "event", "id": next(event_ids), "node": name, "kind": type[:20], "level": level,
             "image": save_jpeg(body, name) if body else None, "ts": now, "ack": None}  # ack: 확인되면 {"ts", "by"}
    s["last_seen"], s["last_event"] = now, event
    add_log(event)
    await broadcast(event)

    started = False
    if AUTO_GUARD:
        started = await send_robot({"cmd": "guard", "source": name})
        if not started:
            await alert(f"센서 {name} 이벤트 — 로봇이 연결되어 있지 않아 경비를 시작하지 못함", "warn")
    return {"ok": True, "guard": started}


@app.post("/api/heartbeat")
async def post_heartbeat(sensor: str = "", node: str = "", level: int | None = None):
    name = sensor_name(sensor or node)
    sensors.setdefault(name, {"last_seen": 0.0, "last_event": None})["last_seen"] = time.time()
    return {"ok": True}


@app.post("/api/photo")
async def post_photo(request: Request, reason: str = "face"):
    """로봇이 찍은 사진. 본문은 JPEG. reason: face(얼굴 확인)"""
    body = await request.body()
    if not body:
        raise HTTPException(400, "사진이 비어 있음")
    reason = re.sub(r"[^a-z_]", "", reason.lower())[:20] or "photo"
    entry = {"type": "photo", "reason": reason, "image": save_jpeg(body, f"robot_{reason}"), "ts": time.time()}
    add_log(entry)
    await broadcast(entry)
    return {"ok": True, "image": entry["image"]}


@app.post("/api/capture")
async def post_capture(request: Request):
    """대시보드 캡처 버튼: 로봇 카메라 원본 사진을 받아 기록에 남김"""
    if not (robot_ip and robot_status.get("online")):
        raise HTTPException(503, "로봇이 연결되어 있지 않습니다")
    url = f"http://{robot_ip}:8080/snapshot.jpg"

    def fetch():
        with urllib.request.urlopen(url, timeout=4) as r:
            return r.read()

    try:
        body = await asyncio.to_thread(fetch)
    except Exception as e:
        raise HTTPException(502, f"로봇 카메라에서 사진을 받지 못함: {e}")
    entry = {"type": "photo", "reason": "capture", "image": save_jpeg(body, "capture"), "ts": time.time()}
    add_log(entry)
    await broadcast(entry)
    return {"ok": True, "image": entry["image"]}


@app.post("/api/ack")
async def post_ack(request: Request):
    """센서 이벤트 확인 처리. {"id": 3, "by": "홍길동"} 또는 {"all": true, "by": "홍길동"}"""
    body = await request.json()
    by = str(body.get("by") or "관리자").strip()[:20] or "관리자"
    want_all = body.get("all") is True
    target = body.get("id")
    if not want_all and not isinstance(target, int):
        raise HTTPException(400, "id 또는 all 필요")

    now = time.time()
    acked = []
    for e in log:
        if e.get("type") != "event" or e.get("ack"):
            continue
        if want_all or e.get("id") == target:
            e["ack"] = {"ts": now, "by": by}
            acked.append({"id": e["id"], "ack": e["ack"]})
    if not acked and not want_all:
        raise HTTPException(404, "확인할 이벤트가 없거나 이미 확인됨")
    if acked:
        await broadcast({"type": "ack", "acks": acked})
    return {"ok": True, "acked": len(acked)}


@app.post("/api/command")
async def post_command(request: Request):
    """웹 → 로봇 명령. {"cmd": "guard"} 또는 {"cmd": "stop"}"""
    cmd = await request.json()
    if cmd.get("cmd") not in COMMANDS:
        raise HTTPException(400, "unknown cmd")
    host = request.client.host if request.client else "?"
    if host not in cmd_hosts:   # 처음 보는 기기에서 로봇을 조종 → 외부 조작 의심 알림
        cmd_hosts.add(host)
        await alert(f"처음 보는 기기({host})에서 로봇 명령: {cmd.get('cmd')} — 본인 기기가 아니면 확인 필요", "critical")
    if not await send_robot({"cmd": cmd["cmd"]}):
        raise HTTPException(503, "로봇이 연결되어 있지 않습니다")
    return {"ok": True}


# ─────────────────────── WebSocket ───────────────────────

@app.websocket("/ws/web")
async def ws_web(ws: WebSocket):
    await ws.accept()
    web_clients.add(ws)
    await ws.send_json({"type": "init", "log": log, "robot": robot_payload()})
    try:
        while True:
            await ws.receive_text()  # 대시보드는 명령을 /api/command 로 보냄. 여기선 연결 유지만.
    except WebSocketDisconnect:
        pass
    finally:
        web_clients.discard(ws)


@app.websocket("/ws/robot")
async def ws_robot(ws: WebSocket):
    global robot_ws, robot_ip, robot_status, robot_last_status, robot_silent
    await ws.accept()
    robot_ws, robot_ip = ws, ws.client.host
    robot_status = {"online": True}
    robot_last_status, robot_silent = time.time(), False
    await alert(f"로봇 연결됨 ({robot_ip})")
    await broadcast(robot_payload())
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "status":
                changed = {k: v for k, v in msg.items() if k != "type"} != {k: v for k, v in robot_status.items() if k != "online"}
                robot_status = {"online": True, **{k: v for k, v in msg.items() if k != "type"}}
                robot_last_status = time.time()
                if robot_silent:
                    robot_silent = False
                    await alert("로봇 응답 재개")
                if changed:
                    await broadcast(robot_payload())
            elif msg.get("type") == "alert":
                await alert(str(msg.get("msg", ""))[:200], msg.get("level", "info"))
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    finally:
        if robot_ws is ws:
            robot_ws = None
            robot_status = {"online": False}
            await alert("로봇 연결 끊김 — 전원·배터리·와이파이 확인 (로봇은 3초 뒤 스스로 정지)", "critical")
            await broadcast(robot_payload())


async def watchdog():
    """로봇 무응답 감시 (1초 간격): 연결은 살아 있는데 상태 보고가 끊김 → 프로그램 멈춤·과부하 의심"""
    global robot_silent
    while True:
        if robot_ws is not None and not robot_silent and time.time() - robot_last_status > ROBOT_SILENT_SEC:
            robot_silent = True
            await alert(f"로봇이 {ROBOT_SILENT_SEC:.0f}초 넘게 응답하지 않음 — 프로그램 멈춤·과부하 의심", "critical")
        await asyncio.sleep(1)

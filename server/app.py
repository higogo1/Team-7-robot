"""순찰로봇 관제 서버 (노트북에서 실행)  — 담당: 서버/웹

- XIAO 노드  → POST /api/event      : 이벤트(+사진) 수신 → 웹 알림 + 로봇 출동
- 로봇      ↔ WS   /ws/robot        : 상태 보고 / 명령 수신
- 웹 대시보드 ↔ WS   /ws/web          : 실시간 알림·상태
- 웹 대시보드 → POST /api/command    : 순찰/추종/정지/출동/등록 명령

실행: uvicorn app:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse

app = FastAPI()


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/event")
async def post_event(request: Request, node: str):
    # TODO: 사진 저장, 이벤트 기록, 웹에 알림, 로봇에 출동 명령
    pass


@app.post("/api/command")
async def post_command():
    # TODO: 웹에서 받은 명령을 로봇으로 전달
    pass


@app.websocket("/ws/web")
async def ws_web(ws: WebSocket):
    # TODO: 대시보드 연결 관리 + 실시간 알림 전송
    pass


@app.websocket("/ws/robot")
async def ws_robot(ws: WebSocket):
    # TODO: 로봇 연결 관리 + 상태 수신
    pass

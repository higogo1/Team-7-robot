# 🚓 Team 7 순찰로봇

지정 구역을 순찰하다가 **이벤트가 발생하면 그 위치로 출동**하고, **등록된 특정인을 따라가며**, 모든 상황을 **웹으로 알려주는** 순찰로봇 미니 프로젝트입니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| 지정구역 순찰 | 바닥 라인을 따라 체크포인트 A → B → C → D를 반복 순찰 |
| 이벤트 감지 | 각 지점의 XIAO ESP32S3 Sense가 큰 소리를 감지하면 사진을 찍어 서버로 전송 |
| 출동 | 이벤트가 발생한 지점으로 로봇이 자동 이동 |
| 특정인 추종 | 카메라로 등록한 사람의 얼굴을 인식해서 따라감 |
| 웹 알림 | 대시보드에 실시간 알림, 현장 사진, 로봇 카메라 영상 표시 |

## 사용 장비

- **라즈봇 V2** (Yahboom, Raspberry Pi 5): 메카넘 휠, 카메라 짐벌, 초음파 센서, 라인센서 4개
- **XIAO ESP32S3 Sense** × 4: 카메라와 마이크가 달린 감시 노드
- **아두이노 고급키트** (Uno 호환): 시간이 남으면 추가 기능용
- **노트북**: 관제 서버와 웹 대시보드

## 시스템 구조

```
[XIAO 노드 A~D] ──HTTP(이벤트+사진)──▶ [노트북: 관제 서버] ◀──WebSocket──▶ [웹 대시보드]
                                            ▲
                                            │ WebSocket (명령/상태)
                                            ▼
                                       [라즈봇 V2]
                                       순찰 · 출동 · 추종
                                       카메라 스트림 :8080
```

- 모든 장비는 **같은 2.4GHz Wi-Fi**에 연결합니다. XIAO는 5GHz를 지원하지 않습니다.
- 바닥에는 검정 테이프로 순찰 루프를 만들고, 각 체크포인트(A~D)에는 **가로 테이프**를 붙입니다.

## 폴더 구조

```
Team-7-robot/
├── server/            # 관제 서버 + 웹 대시보드 (노트북)
│   ├── app.py
│   ├── static/index.html
│   └── requirements.txt
├── robot/             # 라즈봇 V2 코드 (라즈베리파이)
│   ├── main.py        # 메인 루프, 모드 전환 (순찰/출동/추종)
│   ├── hal.py         # 모터·센서·서보 제어
│   ├── camera.py      # 카메라 + 영상 스트리밍
│   ├── vision.py      # 특정인 얼굴 등록/인식
│   ├── config.py      # 설정값 (서버 IP, 속도 등)
│   ├── models/        # 얼굴 인식 모델 파일 (git 제외)
│   └── requirements.txt
├── xiao_node/         # XIAO ESP32S3 Sense 감시 노드 (Arduino IDE)
│   └── xiao_node.ino
├── arduino/           # 아두이노 추가 기능 (선택)
└── docs/              # 회의록, 사진, 발표 자료
```

## 역할 분담

| 담당 | 폴더 | 할 일 |
|---|---|---|
| 팀원 1: 로봇 주행 | `robot/main.py`, `hal.py` | 라인 추종, 체크포인트 카운트, 출동 |
| 팀원 2: 로봇 비전 | `robot/camera.py`, `vision.py` | 영상 스트리밍, 얼굴 등록·인식, 추종 |
| 팀원 3: 센서 노드 | `xiao_node/` | 카메라·마이크 이벤트 감지, 서버 전송, 노드 4개 설치 |
| 팀원 4: 서버/웹 | `server/` | 관제 서버, 대시보드, 전체 통합 |

## 일정

| 시점 | 목표 |
|---|---|
| 사전 준비 | 장비 확인, 개발환경 설치, Yahboom 기본 예제 실행 |
| 1일차 (월) 오전 | 파트별 기능 단독 동작 |
| 1일차 (월) 오후 | 서버 ↔ 로봇 ↔ XIAO 연결 |
| 2일차 (화) 오전 | 전체 통합 + 튜닝 |
| 2일차 (화) 오후 | 시연 리허설 |

## 실행 방법

### 서버 (노트북)
```bash
cd server
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```
브라우저에서 `http://localhost:8000` 접속. Windows 방화벽 창이 뜨면 **허용**을 누르세요.

### 로봇 (라즈베리파이)
```bash
cd robot
python3 -m venv --system-site-packages venv && source venv/bin/activate
pip install -r requirements.txt
python download_models.py      # 얼굴 인식 모델 받기 (최초 1회)
python main.py
```
- `config.py`의 `SERVER_URL`을 노트북 IP로 바꾸세요. 노트북 IP는 `ipconfig`로 확인합니다.
- Yahboom 자료의 `Raspbot_Lib.py`를 `robot/` 폴더에 복사하세요. 이 파일이 없으면 **가짜 하드웨어 모드**로 돌아서 노트북에서도 실행할 수 있습니다.
- 처음에는 `config.py`의 `LINE_ACTIVE_LOW`, `MOTOR_SIGN`부터 확인하세요. 라인 위에서 센서 값이 반대로 나오거나 바퀴가 거꾸로 돌면 이 두 값을 바꿉니다.

### XIAO 노드
Arduino IDE에서 `xiao_node.ino`를 열고 Wi-Fi 정보, 서버 IP, `NODE_ID`를 수정한 뒤 업로드합니다.
시리얼 모니터에 1초마다 `level=` 값이 찍힙니다. 이 값을 보고 `SOUND_THRESHOLD`를 맞추세요. 시연할 때는 **BOOT 버튼**을 눌러 이벤트를 강제로 발생시킬 수 있습니다.

## 시연 순서

1. 노트북에서 서버 실행 → 대시보드 접속 → **🔔 알림 켜기** 클릭
2. XIAO 노드 4개 전원 ON → 대시보드 A~D가 "노드 온라인"인지 확인
3. 로봇을 D와 A 사이 출발점에 두고 `python main.py` → **📍 위치 초기화** → **▶ 순찰 시작**
4. 노드 근처에서 박수 또는 BOOT 버튼 → 웹 알림과 사진 표시 → 로봇이 해당 지점으로 출동
5. 카메라 앞에서 **📸 특정인 등록**(2~3번 누르면 인식이 더 안정적) → **👤 사람 따라가기**
6. 추종 후에는 위치를 잃습니다. 로봇을 출발점에 다시 두고 위치 초기화

## 서버 API

| 경로 | 보내는 쪽 | 내용 |
|---|---|---|
| `POST /api/event?node=A&type=sound&level=N` | XIAO | 본문은 JPEG. 웹 알림 후 로봇 자동 출동 (같은 노드는 10초 쿨다운) |
| `POST /api/heartbeat?node=A` | XIAO | 10초마다 보내는 생존 신호 |
| `POST /api/command` | 웹 | `{"cmd": "patrol" \| "follow" \| "stop" \| "register" \| "reset_pos"}`, `{"cmd": "dispatch", "node": "B"}` |
| `WS /ws/robot` | 로봇 | 명령 수신, 상태와 알림 보고 |
| `WS /ws/web` | 웹 | 이벤트, 알림, 로봇 상태 실시간 수신 |

## 협업 규칙

- `main` 브랜치에는 **동작 확인된 코드만** 올립니다.
- 작업은 각자 브랜치에서 합니다. 예: `feat/robot-line`, `feat/vision`, `feat/xiao`, `feat/server`
- 자기 담당 폴더 위주로 수정합니다. 다른 파트 파일을 고칠 때는 담당자에게 먼저 말해주세요.
- 커밋 메시지는 `[파트] 내용` 형식으로 씁니다. 예: `[robot] 라인 추종 구현`

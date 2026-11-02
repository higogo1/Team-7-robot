# Team 7 경비로봇

주변을 돌며 **사람을 찾아 얼굴을 확인·촬영하고 따라가는** 경비로봇과, CCTV 관제실처럼 쓰는 **웹 대시보드** 프로젝트입니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| 경계 | 로봇이 몸을 조금씩 돌며 카메라로 사람 모양(다리·몸통 등 몸 일부만 보여도)을 찾음 |
| 얼굴 확인 | 사람이 보이면 멈추고, 2축 카메라를 그쪽으로 돌린 뒤 위로 올려 얼굴을 찾음 |
| 촬영 | 얼굴이 보이는 동안 10초마다 사진을 찍어 대시보드 이벤트 목록에 올림 |
| 추적 | 사람을 화면 가운데에 두고, 초음파로 약 70cm 거리를 유지하며 따라감 |
| 감지 센서 | XIAO ESP32S3 Sense가 큰 소리를 감지하면 사진과 함께 알리고 로봇 경비 시작 |
| 관제 대시보드 | 실시간 영상, 녹화(.webm), 캡처, 작동 단계 표시, 사람 감지 경보, 이벤트 확인 처리 |
| 기기 이상 알림 | 카메라 가림·멈춤, 끼임·들림, 외부 힘으로 이동, 센서 통신 이상, 전원 부족, 과열, 무응답, 연결 끊김, 처음 보는 기기의 명령 |
| 안전 정지 | 서버 연결이 3초 넘게 끊기면 로봇이 스스로 멈춤 |

알림은 **상태가 바뀔 때만** 보냅니다. 같은 내용은 30초 안에 다시 보내지 않습니다.

## 사용 장비

- **라즈봇 V2** (Yahboom, Raspberry Pi 5): 메카넘 휠, 2DOF 카메라 받침대(좌우 0~180°, 상하 0~110°), 초음파 센서
- **XIAO ESP32S3 Sense**: 카메라와 마이크가 달린 감지 센서 (여러 대 가능)
- **노트북**: 관제 서버와 웹 대시보드

## 시스템 구조

```
[XIAO 감지 센서] ──HTTP(이벤트+사진)──▶ [노트북: 관제 서버] ◀──WebSocket──▶ [웹 대시보드]
                                            ▲
                                            │ WebSocket (명령/상태/알림), HTTP (사진)
                                            ▼
                                       [라즈봇 V2]
                                       경계 · 얼굴 확인 · 추적
                                       카메라 스트림 :8080
```

모든 장비는 **같은 2.4GHz Wi-Fi**에 연결합니다. XIAO는 5GHz를 지원하지 않습니다.

## 폴더 구조

```
Team-7-robot/
├── server/            # 관제 서버 + 웹 대시보드 (노트북)
│   ├── app.py
│   ├── static/index.html
│   └── requirements.txt
├── robot/             # 라즈봇 V2 코드 (라즈베리파이)
│   ├── main.py        # 메인 루프, 경비(경계 → 얼굴 확인 → 추적), 안전 정지
│   ├── health.py      # 기기 이상(통제불능) 감시
│   ├── vision.py      # 사람 인식(NanoDet), 얼굴 찾기(YuNet)
│   ├── hal.py         # 모터·초음파·서보 제어
│   ├── camera.py      # 카메라 + 영상 스트리밍
│   ├── config.py      # 설정값 (서버 IP, 속도, 감시 기준 등)
│   ├── download_models.py
│   ├── models/        # 인식 모델 파일 (git 제외)
│   └── requirements.txt
├── xiao_node/         # XIAO ESP32S3 Sense 감지 센서 (Arduino IDE)
│   └── xiao_node.ino
├── arduino/           # 아두이노 추가 기능 (선택)
└── docs/              # 회의록, 사진, 발표 자료
```

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
python download_models.py      # 사람·얼굴 인식 모델 받기 (최초 1회)
python main.py
```
- `config.py`의 `SERVER_URL`을 노트북 IP로 바꾸세요. 노트북 IP는 `ipconfig`로 확인합니다.
- Yahboom 자료의 `Raspbot_Lib.py`를 `robot/` 폴더에 복사하세요. 이 파일이 없으면 **가짜 하드웨어 모드**로 돌아서 노트북에서도 실행할 수 있습니다.
- 바퀴가 거꾸로 돌면 `MOTOR_SIGN`, 카메라가 정면을 안 보면 `SERVO_PAN`을 조정합니다. 카메라 받침대는 서보를 90°에 둔 상태로 정면을 보게 조립해야 합니다.

### XIAO 감지 센서
Arduino IDE에서 `xiao_node.ino`를 열고 Wi-Fi 정보, 서버 IP, `SENSOR_ID`(예: S1)를 수정한 뒤 업로드합니다.
시리얼 모니터에 1초마다 `level=` 값이 찍힙니다. 이 값을 보고 `SOUND_THRESHOLD`를 맞추세요. 시연할 때는 **BOOT 버튼**을 눌러 이벤트를 발생시킬 수 있습니다.

## 시연 순서

1. 노트북에서 서버 실행 → 대시보드 접속 → **알림 켜기**
2. 로봇에서 `python main.py` → 대시보드에 ROBOT, CAMERA가 초록색인지 확인
3. **경비 시작** → 로봇이 몸을 돌리며 경계
4. 로봇 앞을 지나가기 → 멈춤 → 카메라가 올라가며 얼굴 확인 → 사진(10초마다) → 따라오기
5. XIAO 센서 근처에서 박수 또는 BOOT 버튼 → 센서 이벤트 알림 → 로봇 경비 시작
6. 대시보드에서 **녹화**, **캡처**로 기록 남기기

## 서버 API

| 경로 | 보내는 쪽 | 내용 |
|---|---|---|
| `POST /api/event?sensor=S1&type=sound&level=N` | XIAO | 본문은 JPEG. 웹 알림 후 로봇 경비 시작 (같은 센서는 10초 쿨다운) |
| `POST /api/heartbeat?sensor=S1` | XIAO | 10초마다 보내는 생존 신호 |
| `POST /api/command` | 웹 | `{"cmd": "guard"}` 경비 시작, `{"cmd": "stop"}` 정지 |
| `POST /api/capture` | 웹 | 로봇 카메라 원본 사진을 받아 기록에 남김 |
| `POST /api/ack` | 웹 | 센서 이벤트 확인 처리 `{"id": 3}` 또는 `{"all": true}` |
| `POST /api/photo?reason=face` | 로봇 | 로봇이 찍은 사진 (JPEG) |
| `WS /ws/robot` | 로봇 | 명령 수신, 상태와 알림 보고 |
| `WS /ws/web` | 웹 | 이벤트, 알림, 사진, 로봇 상태 실시간 수신 |

## 협업 규칙

- `main` 브랜치에는 **동작 확인된 코드만** 올립니다.
- 작업은 각자 브랜치에서 합니다. 예: `feat/guard`, `feat/xiao`, `feat/server`
- 자기 담당 폴더 위주로 수정합니다. 다른 파트 파일을 고칠 때는 담당자에게 먼저 말해주세요.
- 커밋 메시지는 `[파트] 내용` 형식으로 씁니다. 예: `[robot] 경비 모드 구현`

# 라즈봇 설정값 — 현장에서 튜닝하는 숫자는 전부 여기 모아둠

# 관제 서버(노트북) 주소: 노트북 cmd에서 ipconfig → 무선 LAN IPv4 주소
SERVER_URL = "ws://192.168.0.10:8000/ws/robot"

# 순찰 루프 위 체크포인트(가로 테이프) 순서. XIAO 노드의 NODE_ID와 같아야 함.
# 로봇은 D와 A 사이 출발점에 놓고 시작 → 처음 만나는 체크포인트가 A
NODES = ["A", "B", "C", "D"]

# ── 라인 주행 ──
BASE_SPEED = 50           # 기본 전진 속도 (모터 값 0~255)
LINE_KP = 25              # 라인 오차 → 회전 보정
SEARCH_TURN = 40          # 라인을 놓쳤을 때 찾는 회전 속도
CROSS_MIN_INTERVAL = 1.0  # 체크포인트 중복 카운트 방지 (초)
PATROL_PAUSE = 2.0        # 순찰 중 체크포인트에서 멈추는 시간 (초)
LINE_ACTIVE_LOW = True    # 센서가 검정선 위에서 0을 내면 True — Yahboom 라인트레이싱 예제로 확인
MOTOR_SIGN = [1, 1, 1, 1] # [왼앞, 왼뒤, 오앞, 오뒤] 바퀴가 거꾸로 돌면 그 자리를 -1로

# ── 사람 따라가기 ──
FOLLOW_FACE_SIZE = 0.18   # 화면 폭 대비 얼굴 폭 목표 (클수록 더 가까이 붙음)
FOLLOW_KP_TURN = 70
FOLLOW_KP_DIST = 500
FOLLOW_MAX_SPEED = 70
STOP_DISTANCE_CM = 25     # 초음파 거리가 이보다 가까우면 전진 금지
MATCH_THRESHOLD = 0.363   # SFace 코사인 유사도 기준 (OpenCV 권장값)
LOST_ALERT_SEC = 3.0      # 대상을 이 시간 이상 못 찾으면 웹에 알림

# ── 카메라 / 서보 ──
CAMERA_INDEX = 0
FRAME_W, FRAME_H = 640, 480
STREAM_PORT = 8080
SERVO_PAN = 90            # 좌우 정면 각도
SERVO_TILT = 90           # 상하 기본 각도 (얼굴이 잘 보이게 조정)

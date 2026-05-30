"""사용 가능한 오디오 입력 장치 목록 출력."""
import sounddevice as sd

default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
print(f"=== 기본 입력 장치 인덱스: {default_in} ===\n")
print("=== 입력 가능 장치 ===")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        marker = " *" if i == default_in else "  "
        name = d["name"]
        ch = d["max_input_channels"]
        sr = int(d["default_samplerate"])
        host = sd.query_hostapis(d["hostapi"])["name"]
        print(f"{marker}[{i:2d}] {name}  (in={ch}, sr={sr}, host={host})")

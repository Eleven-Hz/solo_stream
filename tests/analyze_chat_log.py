"""chat_log 분석: STT 발화가 있는 라운드들의 봇 반응 추출."""
import io
import re
import sys
from pathlib import Path

# stdout을 UTF-8로 강제 (cp949 콘솔에서 이모지 인코딩 깨짐 방지)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

LOG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/chat_20260524_192134.log")
text = LOG_PATH.read_text(encoding="utf-8")

# 라운드 블록 분리
round_blocks = re.split(r"(?=========== 라운드 #\d+ \[)", text)
round_blocks = [b for b in round_blocks if "라운드 #" in b]

print(f"총 라운드 블록 {len(round_blocks)}개\n")

for block in round_blocks:
    m_round = re.search(r"라운드 #(\d+) \[(.+?)\] ==========", block)
    if not m_round:
        continue
    round_num = m_round.group(1)

    # STT 발화 추출
    m_stt = re.search(r"\[STT 발화 (\d+)개\](.*?)\n\n", block, re.DOTALL)
    if not m_stt:
        continue  # 발화 없는 라운드 건너뜀
    stt_lines = []
    for line in m_stt.group(2).strip().splitlines():
        sline = line.strip()
        if sline.startswith("("):
            stt_lines.append(sline)

    # vision 위치/상황 추출
    loc = ""
    sit = ""
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("위치:"):
            loc = s[3:].strip()
        elif s.startswith("상황:"):
            sit = s[3:].strip()

    # CHAT 결과 추출
    m_chat = re.search(r"\[CHAT 결과\] (\d+)개\n(.*?)(?=\n\[|\Z)", block, re.DOTALL)
    chat_lines = []
    if m_chat:
        for line in m_chat.group(2).strip().splitlines():
            sline = line.strip()
            if sline and ":" in sline:
                chat_lines.append(sline)

    print(f"━━ 라운드 #{round_num} | 위치: {loc[:40]} | 상황: {sit[:60]}")
    for s in stt_lines:
        print(f"   🎤 {s}")
    for c in chat_lines:
        print(f"   💬 {c}")
    print()

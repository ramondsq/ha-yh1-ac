#!/usr/bin/env python3
"""用 RM4 发送自己合成的 Y-H1 码.

用法: uv run --with broadlink tools/yh1_send.py mode=cool temp=26 fan=auto cmd=temp [开关...]
开关: power_toggle sleep super_ vswing_toggle hswing_toggle dimmer
"""
import sys

from rm4 import connect
from yh1_codec import build_frame, frame_to_broadlink, parse_frame, to_b64


def main():
    kw = {"mode": "cool", "temp": 26, "fan": "auto", "cmd": "temp"}
    for a in sys.argv[1:]:
        if "=" in a:
            k, v = a.split("=", 1)
            kw[k] = int(v, 0) if k in ("temp", "room_temp") or (k == "cmd" and v[0].isdigit()) else v
        elif a == "toggle":
            kw["power_toggle"] = True
        else:
            kw[a] = True
    frame = build_frame(**kw)
    packet = frame_to_broadlink(frame)
    print("帧:", frame[:6].hex(" "), "|", frame[6:14].hex(" "), "|", frame[14:].hex(" "))
    print("解析:", parse_frame(frame))
    connect().send_data(packet)
    print("已发送. HA 用:", to_b64(packet)[:60] + "...")


if __name__ == "__main__":
    main()

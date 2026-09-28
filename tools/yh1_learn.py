#!/usr/bin/env python3
"""RM4 连续学习 Y-H1 按键并解码. 用法: uv run --with broadlink tools/yh1_learn.py [秒数, 默认40] [最多几个, 默认1]

每个抓到的码追加到当前目录的 captures.jsonl.
"""
import datetime
import json
import sys
import time

from broadlink.exceptions import ReadError, StorageError

from rm4 import connect
from yh1_codec import decode, parse_frame


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    got = 0
    dev = connect()
    deadline, last_enter = time.time() + seconds, 0
    print("学习模式已开启, 请把遥控器对准 RM4 按键...", flush=True)
    while time.time() < deadline:
        if time.time() - last_enter > 8:  # RM4 学习窗口很短, 定期重进保活
            dev.enter_learning()
            last_enter = time.time()
        try:
            data = dev.check_data()
        except (StorageError, ReadError):
            time.sleep(0.5)
            continue
        if data:
            got += 1
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            rec = {"ts": ts, "raw": data.hex()}
            try:
                fr = decode(data)
                rec["frame"] = fr.hex(" ")
                rec["parsed"] = parse_frame(fr)
                print(f"#{got} [{ts}]", fr[:6].hex(" "), "|", fr[6:14].hex(" "), "|", fr[14:].hex(" "))
                print("    ", rec["parsed"], flush=True)
            except ValueError as e:
                got -= 1  # 噪声/不完整的码不计数
                print(f"   [{ts}] 忽略无效码: {e}", flush=True)
                dev.enter_learning()
                last_enter = time.time()
                continue
            with open("captures.jsonl", "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if got >= count:
                return
            dev.enter_learning()
            last_enter = time.time()
            continue
        time.sleep(0.5)
    print(f"结束, 共抓到 {got} 个")


if __name__ == "__main__":
    main()

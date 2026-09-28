"""连接 Broadlink RM4: 优先用环境变量 RM4_HOST, 否则自动发现局域网里唯一的一台."""
import os
import pathlib
import sys

import broadlink

# 让工具脚本能直接 import 集成里的编解码模块
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "yh1_ac"))


def connect():
    host = os.environ.get("RM4_HOST")
    if host:
        dev = broadlink.hello(host)
    else:
        devs = [d for d in broadlink.discover(timeout=5) if hasattr(d, "send_data")]
        if not devs:
            sys.exit("没找到 Broadlink 红外设备, 请用 RM4_HOST=<ip> 指定")
        if len(devs) > 1:
            sys.exit("找到多台设备, 请用 RM4_HOST 指定: " + ", ".join(d.host[0] for d in devs))
        dev = devs[0]
    dev.auth()
    return dev

"""用所有实抓的码 (captures.jsonl) 回归验证编解码器."""
import json
import pathlib

import pytest

from custom_components.yh1_ac.yh1_codec import build_frame, decode, frame_to_broadlink, parse_frame

# 实体遥控器按键 -> 期望解析结果 (与抓码时遥控器屏幕/空调反应对照过)
EXPECTED = {
    "old:ON_HEX": {"fan": "high", "cmd": "fan", "ifeel": True, "room_temp": 28},
    "old:TOGGLE_HEX": {"power_toggle": True, "cmd": "power"},
    "old:calib1": {"temp": 27, "cmd": "temp"},
    "01:00:40": {"mode": "cool", "temp": 25, "cmd": "temp", "ifeel": False},
    "01:02:10": {"mode": "dry", "cmd": "mode"},
    "01:02:12": {"mode": "fan_only", "fan": "high", "cmd": "mode"},
    "01:02:14": {"mode": "heat", "cmd": "mode"},
    "01:02:16": {"mode": "cool", "temp": 26, "cmd": "mode"},
    "01:03:32": {"vswing_toggle": True, "cmd": "vswing"},
    "01:03:36": {"hswing_toggle": True, "cmd": "hswing"},
    "01:04:42": {"sleep": True, "fan": "low", "cmd": "sleep"},
    "01:04:47": {"super": True, "temp": 18, "fan": "high", "cmd": "super"},
    "01:04:59": {"cmd": "mute"},
    "01:05:05": {"dimmer": True, "cmd": "dimmer"},
    "01:08:01": {"power_toggle": True, "cmd": "power"},
    "01:10:32": {"mode": "smart", "power_toggle": False, "cmd": "smart"},
}


def _valid_captures():
    out = []
    for line in (pathlib.Path(__file__).parent / "captures.jsonl").read_text().splitlines():
        rec = json.loads(line)
        try:
            out.append((rec["ts"], decode(rec["raw"])))
        except ValueError:
            pass  # 抓到的环境噪声
    return out


CAPTURES = _valid_captures()


def test_enough_captures():
    assert len(CAPTURES) == 21


@pytest.mark.parametrize("ts,frame", CAPTURES, ids=[ts for ts, _ in CAPTURES])
def test_capture(ts, frame):
    p = parse_frame(frame)
    assert p["checksum_ok"]
    for k, v in EXPECTED.get(ts, {}).items():
        assert p[k] == v, k
    # 用解析出的字段重新合成, 应与原帧逐字节一致
    h, m = map(int, p["clock"].split(":"))
    rebuilt = build_frame(
        p["mode"], p["temp"], p["fan"], p["cmd"],
        power_toggle=p["power_toggle"], sleep=p["sleep"], super_=p["super"],
        vswing_toggle=p["vswing_toggle"], hswing_toggle=p["hswing_toggle"],
        dimmer=p["dimmer"], clock=(h, m), ifeel=p["ifeel"], room_temp=p["room_temp"],
    )
    assert rebuilt == frame
    assert decode(frame_to_broadlink(frame)) == frame

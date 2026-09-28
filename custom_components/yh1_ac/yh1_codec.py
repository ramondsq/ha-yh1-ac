"""Y-H1 空调遥控器 (Kelon168 协议变种) 编解码, 不依赖 Home Assistant.

帧结构: 21 字节 = 6 + 8 + 7 三段, LSB first, 只有第一段带 9ms 引导头, 段间隔 8ms.

  byte 0-1  固定 83 06
  byte 2    bit0-1 风速(0自动 1高 2中 3低)  bit2 电源翻转  bit3 睡眠  bit7 上下摆风翻转(风管机无效)
  byte 3    bit0-2 模式(0制热 1智能 2制冷 3除湿 4送风)  bit4-7 温度-18
  byte 4    bit7 智能(SMART)
  byte 5    bit4+bit7 超强(SUPER)
  byte 6    bit0-4 时钟-小时  bit5 DIMMER(与按键码 0x00 一起发)  bit7 恒为1
  byte 7    bit0-5 时钟-分钟  bit7 定时关使能
  byte 8    bit0-4 定时关-小时  bit6 上下摆风翻转  bit7 左右摆风翻转 (风管机无效)
  byte 9    bit0-5 定时关-分钟  bit7 定时开使能
  byte 10   定时开-小时
  byte 11   bit0-5 定时开-分钟  bit7 I FEEL 开
  byte 12   遥控器测到的室温 °C, I FEEL 开时才有
  byte 13   校验1 = byte2..12 异或
  byte 15   按键码
  byte 20   校验2 = byte14..19 异或

实测行为:
  - 关机时, 除 SMART 和电源翻转外的码只响不开机.
  - SMART 码 (模式=智能, 无翻转位) 在任何状态下都会让空调处于开机.
  - 电源 和 DIMMER(面板指示灯) 是翻转式, 空调自己记住状态.
  - 指示灯关着时, 收到任何码会闪一下.
  - MUTE 在这台机器上无效 (蜂鸣器和风声都不变).
  - 风管机调风速后要几秒才听得出变化.
  - SMART 之后 2 秒内的码会被忽略, 5 秒可以.
"""
from __future__ import annotations

import base64
import datetime

TICK_US = 1e6 / 32768  # Broadlink 时间单位 ≈ 30.52us

# 发射时序 (us), 取 IRremoteESP8266 Kelon168 标称值
HDR_MARK, HDR_SPACE = 9000, 4600
BIT_MARK, ONE_SPACE, ZERO_SPACE = 560, 1680, 600
SECTION_GAP = 8000
FINAL_GAP = 101700
SECTIONS = (6, 8, 7)

TEMP_OFFSET = 18  # 实测: 遥控器显示 25°C 时字段=7
TEMP_MIN, TEMP_MAX = 18, 32

MODES = {"heat": 0, "smart": 1, "cool": 2, "dry": 3, "fan_only": 4}
FANS = {"auto": 0, "high": 1, "medium": 2, "low": 3}
CMDS = {
    "dimmer": 0x00, "power": 0x01, "temp": 0x02, "sleep": 0x03, "super": 0x04,
    "on_timer": 0x05, "mode": 0x06, "vswing": 0x07, "hswing": 0x08,
    "mute": 0x0B, "ifeel": 0x0D, "fan": 0x11, "smart": 0x17, "off_timer": 0x1D,
}
_MODE_NAMES = {v: k for k, v in MODES.items()}
_FAN_NAMES = {v: k for k, v in FANS.items()}
_CMD_NAMES = {v: k for k, v in CMDS.items()}


def _xor(data: bytes) -> int:
    r = 0
    for b in data:
        r ^= b
    return r


def build_frame(
    mode: str = "cool",
    temp: int = 26,
    fan: str = "auto",
    cmd: str | int = "temp",
    *,
    power_toggle: bool = False,
    sleep: bool = False,
    super_: bool = False,
    vswing_toggle: bool = False,
    hswing_toggle: bool = False,
    dimmer: bool = False,
    clock: tuple[int, int] | None = None,
    ifeel: bool = False,
    room_temp: int = 0,
) -> bytes:
    """按状态组出 21 字节帧. clock=(时, 分), 默认当前时间."""
    if not TEMP_MIN <= temp <= TEMP_MAX:
        raise ValueError(f"温度 {temp} 超出范围 {TEMP_MIN}-{TEMP_MAX}")
    if clock is None:
        now = datetime.datetime.now()
        clock = (now.hour, now.minute)
    s = bytearray(21)
    s[0], s[1] = 0x83, 0x06
    s[2] = FANS[fan] | power_toggle << 2 | sleep << 3 | vswing_toggle << 7
    s[3] = MODES[mode] | (temp - TEMP_OFFSET) << 4
    s[4] = 0x80 if mode == "smart" else 0x00
    s[5] = 0x90 if super_ else 0x00
    s[6] = 0x80 | dimmer << 5 | (clock[0] & 0x1F)
    s[7] = clock[1] & 0x3F
    s[8] = vswing_toggle << 6 | hswing_toggle << 7
    s[11] = 0x80 if ifeel else 0x00
    s[12] = room_temp & 0xFF if ifeel else 0x00
    s[13] = _xor(s[2:13])
    s[15] = CMDS[cmd] if isinstance(cmd, str) else cmd
    s[20] = _xor(s[14:20])
    return bytes(s)


def parse_frame(s: bytes) -> dict:
    """21 字节 -> 可读字典."""
    return {
        "mode": _MODE_NAMES.get(s[3] & 7, s[3] & 7),
        "temp": (s[3] >> 4) + TEMP_OFFSET,
        "fan": _FAN_NAMES[s[2] & 3],
        "cmd": _CMD_NAMES.get(s[15], hex(s[15])),
        "power_toggle": bool(s[2] & 0x04),
        "sleep": bool(s[2] & 0x08),
        "super": bool(s[5] & 0x90),
        "vswing_toggle": bool(s[8] & 0x40),
        "hswing_toggle": bool(s[8] & 0x80),
        "dimmer": bool(s[6] & 0x20),
        "clock": f"{s[6] & 0x1F:02d}:{s[7] & 0x3F:02d}",
        "ifeel": bool(s[11] & 0x80),
        "room_temp": s[12],
        "checksum_ok": s[13] == _xor(s[2:13]) and s[20] == _xor(s[14:20]),
    }


# ---------- Broadlink 原始包 <-> 21 字节帧 ----------

def frame_to_broadlink(s: bytes) -> bytes:
    pulses, pos = [], 0
    for idx, n in enumerate(SECTIONS):
        if idx == 0:
            pulses += [HDR_MARK, HDR_SPACE]
        for byte in s[pos:pos + n]:
            for j in range(8):
                pulses += [BIT_MARK, ONE_SPACE if (byte >> j) & 1 else ZERO_SPACE]
        pulses += [BIT_MARK, SECTION_GAP]
        pos += n
    pulses[-1] = FINAL_GAP
    body = bytearray()
    for us in pulses:
        t = round(us / TICK_US)
        body += bytes([t]) if t < 256 else bytes([0x00, t >> 8, t & 0xFF])
    return bytes([0x26, 0x00, len(body) & 0xFF, len(body) >> 8]) + bytes(body)


def broadlink_to_frame(packet: bytes) -> bytes:
    n = packet[2] | packet[3] << 8
    d, i, pulses = packet[4:4 + n], 0, []
    while i < len(d):
        if d[i] == 0:
            if i + 2 >= len(d):
                break
            pulses.append((d[i + 1] << 8 | d[i + 2]) * TICK_US)
            i += 3
        else:
            pulses.append(d[i] * TICK_US)
            i += 1
    bits = []
    for m, sp in zip(pulses[::2], pulses[1::2]):
        if m > 6000 or sp > 5000:  # 引导头 / 段间隔 / 结尾
            continue
        bits.append(1 if sp > 1100 else 0)
    if len(bits) != 168:
        raise ValueError(f"位数 {len(bits)} != 168, 不是完整的 Y-H1 码")
    return bytes(sum(bits[k + j] << j for j in range(8)) for k in range(0, 168, 8))


def to_b64(packet: bytes) -> str:
    """HA 的 remote.send_command 可直接使用的 'b64:...' 字符串."""
    return "b64:" + base64.b64encode(packet).decode()


def decode(packet: bytes | str) -> bytes:
    """接受 bytes / hex 字符串 / 'b64:...', 返回 21 字节帧."""
    if isinstance(packet, str):
        packet = (base64.b64decode(packet[4:]) if packet.startswith("b64:")
                  else bytes.fromhex(packet))
    return broadlink_to_frame(packet)


def encode_b64(**kw) -> str:
    return to_b64(frame_to_broadlink(build_frame(**kw)))

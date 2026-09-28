"""Y-H1 空调 climate 实体.

电源是翻转式的, 但 SMART 码在任何状态下都会让空调开机, 所以:
  确保开机 = SMART -> 等 delay 秒 -> 目标状态码
  确保关机 = SMART -> 等 delay 秒 -> 电源翻转码
开/关不依赖 HA 记住的状态. 温度/风速等调整只发一个状态码 (空调关着时会被忽略).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    PRESET_BOOST,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import yh1_codec as codec

_LOGGER = logging.getLogger(__name__)

CONF_REMOTE = "remote_entity_id"
CONF_TEMP_SENSOR = "temperature_sensor"
CONF_POWER_SENSOR = "power_sensor"
CONF_DELAY = "delay"
CONF_HVAC_MODES = "hvac_modes"

HVAC_TO_CODEC = {
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "fan_only",
    HVACMode.AUTO: "smart",
}
FAN_TO_CODEC = {FAN_AUTO: "auto", FAN_LOW: "low", FAN_MEDIUM: "medium", FAN_HIGH: "high"}

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME, default="Air Conditioner"): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Required(CONF_REMOTE): cv.entity_id,
        vol.Optional(CONF_TEMP_SENSOR): cv.entity_id,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
        vol.Optional(CONF_DELAY, default=5.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(CONF_HVAC_MODES, default=list(HVAC_TO_CODEC)): vol.All(
            cv.ensure_list, [vol.In([m.value for m in HVAC_TO_CODEC])]
        ),
    }
)


async def async_setup_platform(
    hass: HomeAssistant, config: dict, async_add_entities, discovery_info=None
) -> None:
    async_add_entities([YH1Climate(config)])


class YH1Climate(ClimateEntity, RestoreEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = codec.TEMP_MIN
    _attr_max_temp = codec.TEMP_MAX
    _attr_target_temperature_step = 1
    _attr_fan_modes = list(FAN_TO_CODEC)
    _attr_preset_modes = [PRESET_NONE, PRESET_BOOST, PRESET_SLEEP]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _enable_turn_on_off_backwards_compatibility = False  # 旧版 HA 需要, 新版忽略

    def __init__(self, config: dict) -> None:
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = config.get(CONF_UNIQUE_ID)
        self._remote = config[CONF_REMOTE]
        self._temp_sensor = config.get(CONF_TEMP_SENSOR)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._delay = config[CONF_DELAY]
        self._attr_hvac_modes = [HVACMode.OFF] + [HVACMode(m) for m in config[CONF_HVAC_MODES]]
        self._attr_assumed_state = self._power_sensor is None

        self._attr_hvac_mode = HVACMode.OFF
        self._last_on_mode = HVACMode.COOL
        self._attr_target_temperature = 26
        self._attr_fan_mode = FAN_AUTO
        self._attr_preset_mode = PRESET_NONE
        self._attr_current_temperature = None
        self._lock = asyncio.Lock()

    # ---------- 生命周期 ----------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            a = last.attributes
            if last.state in self.hvac_modes:
                self._attr_hvac_mode = HVACMode(last.state)
            if a.get("last_on_mode") in self.hvac_modes:
                self._last_on_mode = HVACMode(a["last_on_mode"])
            if isinstance(a.get(ATTR_TEMPERATURE), (int, float)):
                self._attr_target_temperature = int(a[ATTR_TEMPERATURE])
            if a.get("fan_mode") in FAN_TO_CODEC:
                self._attr_fan_mode = a["fan_mode"]
            if a.get("preset_mode") in self.preset_modes:
                self._attr_preset_mode = a["preset_mode"]

        if self._temp_sensor:
            self.async_on_remove(async_track_state_change_event(
                self.hass, [self._temp_sensor], self._on_temp_sensor))
            self._update_current_temp(self.hass.states.get(self._temp_sensor))
        if self._power_sensor:
            self.async_on_remove(async_track_state_change_event(
                self.hass, [self._power_sensor], self._on_power_sensor))
            self._update_power(self.hass.states.get(self._power_sensor))

    @callback
    def _on_temp_sensor(self, event: Event[EventStateChangedData]) -> None:
        self._update_current_temp(event.data["new_state"])
        self.async_write_ha_state()

    def _update_current_temp(self, state) -> None:
        try:
            self._attr_current_temperature = float(state.state)
        except (AttributeError, TypeError, ValueError):
            self._attr_current_temperature = None

    @callback
    def _on_power_sensor(self, event: Event[EventStateChangedData]) -> None:
        if self._update_power(event.data["new_state"]):
            self.async_write_ha_state()

    def _update_power(self, state) -> bool:
        """用外部电源传感器纠正开关状态 (用过实体遥控器也能跟上)."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return False
        on = state.state == STATE_ON
        if on and self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_mode = self._last_on_mode
        elif not on and self._attr_hvac_mode != HVACMode.OFF:
            self._attr_hvac_mode = HVACMode.OFF
        else:
            return False
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_on_mode": self._last_on_mode}

    # ---------- 发码 ----------

    def _frame_kwargs(self, cmd: str, mode: HVACMode | None = None) -> dict:
        mode = mode or (self._attr_hvac_mode if self._attr_hvac_mode != HVACMode.OFF
                        else self._last_on_mode)
        kw = {
            "mode": HVAC_TO_CODEC[mode],
            "temp": int(self._attr_target_temperature),
            "fan": FAN_TO_CODEC[self._attr_fan_mode],
            "cmd": cmd,
        }
        if mode == HVACMode.AUTO:
            kw["temp"] = 25  # 与实体遥控器 SMART 键抓到的码一致
        if mode == HVACMode.FAN_ONLY and kw["fan"] == "auto":
            kw["fan"] = "high"  # 送风模式没有自动风, 遥控器会切到高风
        if self._attr_preset_mode == PRESET_BOOST:
            kw.update(super_=True, temp=codec.TEMP_MIN, fan="high")
        elif self._attr_preset_mode == PRESET_SLEEP:
            kw.update(sleep=True, fan="low")
        return kw

    @staticmethod
    def _smart_kwargs() -> dict:
        # 与实体遥控器 SMART 键抓到的码一致
        return {"mode": "smart", "temp": 25, "fan": "auto", "cmd": "smart"}

    async def _send(self, *frames: dict) -> None:
        """依次发送多个帧, 帧之间等待 delay 秒 (空调在切模式后短时间内会忽略新码)."""
        async with self._lock:
            for i, kw in enumerate(frames):
                if i:
                    await asyncio.sleep(self._delay)
                now = dt_util.now()
                b64 = codec.encode_b64(clock=(now.hour, now.minute), **kw)
                _LOGGER.debug("发送 %s", kw)
                await self.hass.services.async_call(
                    "remote", "send_command",
                    {"entity_id": self._remote, "command": [b64]},
                    blocking=True,
                )

    # ---------- climate 接口 ----------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        was = self._attr_hvac_mode
        if hvac_mode == HVACMode.OFF:
            self._attr_hvac_mode = HVACMode.OFF
            self._attr_preset_mode = PRESET_NONE  # 关机后 SUPER/睡眠失效
            self.async_write_ha_state()
            await self._send(self._smart_kwargs(), self._frame_kwargs("power") | {"power_toggle": True})
            return

        self._attr_hvac_mode = self._last_on_mode = hvac_mode
        if hvac_mode == HVACMode.AUTO:
            self._attr_preset_mode = PRESET_NONE  # 智能模式下 SLEEP/SUPER 不可用
        self.async_write_ha_state()

        if hvac_mode == HVACMode.AUTO:
            frames = [self._smart_kwargs()]
            if self._attr_fan_mode != FAN_AUTO:
                frames.append(self._frame_kwargs("fan"))
            await self._send(*frames)
        elif was == HVACMode.OFF or was == hvac_mode:
            # 从关机开机, 或重复选择当前模式 (可用于纠正状态): 先用 SMART 确保开机
            await self._send(self._smart_kwargs(), self._frame_kwargs("mode"))
        else:
            await self._send(self._frame_kwargs("mode"))

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(self._last_on_mode)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = max(codec.TEMP_MIN, min(codec.TEMP_MAX, round(temp)))
            if self._attr_preset_mode == PRESET_BOOST:
                self._attr_preset_mode = PRESET_NONE  # 调温度会退出 SUPER
        if (mode := kwargs.get("hvac_mode")) is not None:
            await self.async_set_hvac_mode(mode)  # 会带上新温度一起发
            return
        self.async_write_ha_state()
        # 智能模式下调温度未验证, 只记录
        if self._attr_hvac_mode not in (HVACMode.OFF, HVACMode.AUTO):
            await self._send(self._frame_kwargs("temp"))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = fan_mode
        if self._attr_preset_mode == PRESET_BOOST:
            self._attr_preset_mode = PRESET_NONE  # 调风速会退出 SUPER
        self.async_write_ha_state()
        if self._attr_hvac_mode != HVACMode.OFF:
            await self._send(self._frame_kwargs("fan"))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        old = self._attr_preset_mode
        if preset_mode == old:
            return
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()
        if self._attr_hvac_mode in (HVACMode.OFF, HVACMode.AUTO):
            return
        cmd = {PRESET_BOOST: "super", PRESET_SLEEP: "sleep"}[preset_mode if preset_mode != PRESET_NONE else old]
        await self._send(self._frame_kwargs(cmd))

"""在真实 HA 核心里跑 yh1_ac, 截获 remote.send_command 并解码每一帧."""
import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.yh1_ac.yh1_codec import decode, parse_frame

ENTITY = "climate.kong_diao"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def setup(hass: HomeAssistant, **extra):
    conf = {"platform": "yh1_ac", "name": "空调", "remote_entity_id": "remote.rm4", "delay": 0}
    assert await async_setup_component(hass, CLIMATE, {CLIMATE: conf | extra})
    await hass.async_block_till_done()
    return async_mock_service(hass, "remote", "send_command")


def frames(calls):
    out = [parse_frame(decode(c.data["command"][0])) for c in calls]
    calls.clear()
    return out


async def svc(hass, name, **data):
    await hass.services.async_call(CLIMATE, name, {"entity_id": ENTITY, **data}, blocking=True)


async def test_full_flow(hass: HomeAssistant):
    calls = await setup(hass)
    assert hass.states.get(ENTITY).state == "off"

    # 开机: SMART 确保开机, 再发目标状态
    await svc(hass, "turn_on")
    f = frames(calls)
    assert [x["cmd"] for x in f] == ["smart", "mode"]
    assert f[0]["mode"] == "smart" and not f[0]["power_toggle"]
    assert (f[1]["mode"], f[1]["temp"], f[1]["fan"]) == ("cool", 26, "auto")
    assert hass.states.get(ENTITY).state == "cool"

    await svc(hass, "set_temperature", temperature=24)
    f = frames(calls)
    assert [(x["cmd"], x["temp"]) for x in f] == [("temp", 24)]

    await svc(hass, "set_fan_mode", fan_mode="high")
    assert [(x["cmd"], x["fan"]) for x in frames(calls)] == [("fan", "high")]

    # 模式切换 (已开机): 只发一个码
    await svc(hass, "set_hvac_mode", hvac_mode="dry")
    assert [(x["cmd"], x["mode"]) for x in frames(calls)] == [("mode", "dry")]
    # 送风模式没有自动风
    await svc(hass, "set_fan_mode", fan_mode="auto")
    frames(calls)
    await svc(hass, "set_hvac_mode", hvac_mode="fan_only")
    assert [(x["mode"], x["fan"]) for x in frames(calls)] == [("fan_only", "high")]
    await svc(hass, "set_hvac_mode", hvac_mode="cool")
    frames(calls)

    # 重复选当前模式 = 用 SMART 纠正状态
    await svc(hass, "set_hvac_mode", hvac_mode="cool")
    assert [x["cmd"] for x in frames(calls)] == ["smart", "mode"]

    # SUPER: 18°C 高风; 调温度退出
    await svc(hass, "set_preset_mode", preset_mode="boost")
    f = frames(calls)
    assert [(x["cmd"], x["super"], x["temp"], x["fan"]) for x in f] == [("super", True, 18, "high")]
    await svc(hass, "set_temperature", temperature=25)
    f = frames(calls)
    assert [(x["cmd"], x["super"], x["temp"]) for x in f] == [("temp", False, 25)]
    assert hass.states.get(ENTITY).attributes["preset_mode"] == "none"

    await svc(hass, "set_preset_mode", preset_mode="sleep")
    assert [(x["cmd"], x["sleep"], x["fan"]) for x in frames(calls)] == [("sleep", True, "low")]
    await svc(hass, "set_preset_mode", preset_mode="none")
    assert [(x["cmd"], x["sleep"]) for x in frames(calls)] == [("sleep", False)]

    # 关机: SMART 确保开机, 再翻转
    await svc(hass, "turn_off")
    f = frames(calls)
    assert [(x["cmd"], x["power_toggle"]) for x in f] == [("smart", False), ("power", True)]
    assert hass.states.get(ENTITY).state == "off"

    # 关机时调温度只记录, 不发码; 下次开机带上
    await svc(hass, "set_temperature", temperature=27)
    assert frames(calls) == []
    await svc(hass, "turn_on")
    f = frames(calls)
    assert [(x["cmd"], x["mode"], x["temp"]) for x in f] == [("smart", "smart", 25), ("mode", "cool", 27)]

    # 智能模式 = 只发 SMART; 智能模式下可调风速, 调温度只记录
    await svc(hass, "set_hvac_mode", hvac_mode="auto")
    assert [x["cmd"] for x in frames(calls)] == ["smart"]
    await svc(hass, "set_fan_mode", fan_mode="low")
    assert [(x["mode"], x["cmd"], x["fan"]) for x in frames(calls)] == [("smart", "fan", "low")]
    await svc(hass, "set_temperature", temperature=24)
    assert frames(calls) == []
    # 带非自动风速切到智能模式: SMART 后补发风速
    await svc(hass, "set_hvac_mode", hvac_mode="cool")
    frames(calls)
    await svc(hass, "set_hvac_mode", hvac_mode="auto")
    assert [(x["cmd"], x["fan"]) for x in frames(calls)] == [("smart", "auto"), ("fan", "low")]
    assert "swing_modes" not in hass.states.get(ENTITY).attributes


async def test_power_sensor_and_temp_sensor(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.ac_running", "off")
    hass.states.async_set("sensor.room", "27.5")
    calls = await setup(hass, power_sensor="binary_sensor.ac_running",
                        temperature_sensor="sensor.room")
    st = hass.states.get(ENTITY)
    assert st.state == "off" and st.attributes["current_temperature"] == 27.5

    # 用实体遥控器开了机 -> 传感器变 on -> HA 跟上, 不发码
    hass.states.async_set("binary_sensor.ac_running", "on")
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "cool"
    hass.states.async_set("binary_sensor.ac_running", "off")
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"
    assert frames(calls) == []


async def test_hvac_modes_option(hass: HomeAssistant):
    await setup(hass, hvac_modes=["cool", "dry", "fan_only"])
    assert hass.states.get(ENTITY).attributes["hvac_modes"] == ["off", "cool", "dry", "fan_only"]

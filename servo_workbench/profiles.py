"""Reviewed model facts, robot layouts and explicit preset operations."""
import hashlib
import json
import math

XGO_REV = "b401d85d20bb5bfd0237242a8a5ab6847fc962a2"
FT_REV = "9e6e342f6011dc727ba7144fc835bca5357b3ca9"
IDS = list(range(10, 15)) + list(range(20, 25)) + list(range(30, 35))
LEG = ["hip_yaw", "hip_roll", "hip_pitch", "knee", "ankle"]
HEAD = ["neck_pitch", "head_pitch", "head_yaw", "head_roll", "mouth"]
VERSIONS = ("0.16.3", "0.17.9", "0.18.3")


class NoAck(ConnectionError):
    """TX completed without ACK; same-connection readback is required."""


class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def integer(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise Refused("RANGE", f"需要整数 {lo}..{hi}")
    return value


def finite(value, lo, hi):
    if type(value) not in (float, int) or not math.isfinite(value) or not lo <= value <= hi:
        raise Refused("RANGE", f"需要有限数值 {lo}..{hi}")
    return value


def layout(name):
    if name not in ("microduck", "xgo"):
        raise Refused("LAYOUT", "未知机器人布局")
    sides = ("right", "left") if name == "microduck" else ("left", "right")
    names = [sides[0] + "_" + n for n in LEG] + [sides[1] + "_" + n for n in LEG] + HEAD
    return [dict(id=i, name=n, label=f"{n} / ID{i}") for i, n in zip(IDS, names)]


# address: (size, display name). Persistent and runtime fields are intentionally separate.
DXL = {0:(2,"model"),6:(1,"firmware"),7:(1,"id"),8:(1,"baud"),9:(1,"return_delay"),
       10:(1,"drive_mode"),11:(1,"mode"),12:(1,"secondary_id"),20:(4,"homing_offset"),
       31:(1,"temp_limit"),32:(2,"voltage_max"),34:(2,"voltage_min"),36:(2,"pwm_limit"),
       38:(2,"current_limit"),48:(4,"position_max"),52:(4,"position_min"),60:(1,"startup"),
       62:(1,"pwm_slope"),63:(1,"shutdown"),64:(1,"torque"),68:(1,"response"),
       70:(1,"fault"),80:(2,"d"),82:(2,"i"),84:(2,"p"),98:(1,"watchdog"),
       100:(2,"goal_pwm"),108:(4,"acceleration"),112:(4,"speed"),116:(4,"goal"),
       124:(2,"pwm"),126:(2,"current"),128:(4,"velocity"),132:(4,"position"),
       144:(2,"voltage"),146:(1,"temperature")}
HD = {0:(1,"firmware_major"),1:(1,"firmware_minor"),2:(1,"endian"),3:(2,"model_code"),
      5:(1,"id"),6:(1,"baud"),7:(1,"secondary_id"),8:(1,"response"),
      9:(2,"position_min"),11:(2,"position_max"),13:(1,"temp_limit"),
      14:(1,"voltage_max"),15:(1,"voltage_min"),16:(2,"max_output"),18:(1,"phase"),
      19:(1,"protection"),21:(1,"p"),22:(1,"d"),23:(1,"i"),28:(2,"protection_current"),
      31:(2,"offset"),33:(1,"mode"),38:(1,"overcurrent_time"),
      40:(1,"torque"),41:(1,"acceleration"),42:(2,"goal"),44:(2,"goal_current"),
      46:(2,"speed"),48:(2,"output_limit"),50:(1,"temporary_p"),51:(1,"temporary_d"),
      52:(1,"temporary_i"),55:(1,"lock"),56:(2,"position"),58:(2,"velocity"),
      60:(2,"load"),62:(1,"voltage"),63:(1,"temperature"),64:(1,"registered"),
      65:(1,"fault"),66:(1,"moving"),67:(2,"goal_readback"),69:(2,"current")}
PROFILES = {
    "xl330": dict(protocol="dxl2", registers=DXL, id_addr=7, baud_addr=8, torque=64,
                  position=132, offset=20, fault=70, voltage=144, temperature=146,
                  current=126, velocity=128, voltage_scale=.1, current_scale=1.,
                  voltage_range=[3.7,6.], mode_addr=11, mode=3, baud_code=3),
    "hd1910": dict(protocol="feetech", registers=HD, id_addr=5, baud_addr=6, torque=40,
                   position=56, offset=31, fault=65, voltage=62, temperature=63,
                   current=69, velocity=58, voltage_scale=.1, current_scale=6.5,
                   voltage_range=[4.,8.4], mode_addr=33, mode=4, baud_code=0),
}
BAUDS = {
    "xl330": {0:9600,1:57600,2:115200,3:1000000,4:2000000,5:3000000,6:4000000},
    "hd1910": dict(enumerate([1000000,500000,250000,128000,115200,76800,57600,38400])),
}
PERSISTENT = {
    'xl330': (7,8,9,10,11,12,20,31,32,34,36,38,48,52,60,62,63),
    'hd1910': (5,6,7,8,9,11,13,14,15,16,18,19,21,22,23,28,31,33,38),
}


def profile(name):
    if name not in PROFILES:
        raise Refused("PROFILE", "只支持 XL330-M288 / HD1910-C001")
    return PROFILES[name]


def signed(value, bit=15):
    return -(value & ((1 << bit)-1)) if value & (1 << bit) else value


def identity(kind, raw):
    if kind == "xl330":
        ok = raw.get("0") == 1200
        return dict(compatible=ok, model=raw.get("0"), firmware=raw.get("6"),
                    basis="model_register", physical_unique_identity=False)
    ok = raw.get("2") == 0 and raw.get("0") == 3 and raw.get("1", 0) >= 43
    return dict(compatible=ok, model=raw.get("3"), firmware=[raw.get("0"),raw.get("1")],
                basis="operator_HD1910_claim_plus_HLS_firmware_readback",
                physical_unique_identity=False, model_claim_requires_confirmation=True)


def metrics(kind, raw):
    p = profile(kind)
    def get(a, scale=1):
        v = raw.get(str(a))
        return None if v is None else v * scale
    def sign(a):
        v = raw.get(str(a))
        if v is None:
            return None
        if kind == "hd1910":
            return signed(v)
        bits = p["registers"][a][0]*8
        return v-(1<<bits) if v & (1<<(bits-1)) else v
    speed_scale = 50*360/4096 if kind == "hd1910" else .229*6
    pos = sign(p["position"])
    return dict(position_raw=pos, current_ma=None if sign(p["current"]) is None else
                sign(p["current"])*p["current_scale"],
                speed_deg_s=None if sign(p["velocity"]) is None else sign(p["velocity"])*speed_scale,
                voltage_v=get(p["voltage"],.1),temperature_c=get(p["temperature"]),
                torque=get(p["torque"]),fault=get(p["fault"]),
                unit_basis="pinned_model_reference_not_hardware_calibrated")


def options(kind, values=None):
    values = values or {}
    if not isinstance(values,dict) or set(values)-{"shutdown52","gains","enable_protection","protection_current_a"}:
        raise Refused("OPTIONS","未知预设选项")
    out = {}
    for k in ("shutdown52","enable_protection"):
        if k in values and type(values[k]) is not bool:
            raise Refused("OPTIONS", "开关必须是布尔值")
    if kind == "xl330":
        if set(values)-{"shutdown52"}:
            raise Refused("OPTIONS","XL330 不使用 Feetech 预设")
        out["shutdown52"] = values.get("shutdown52",False)
    else:
        if "shutdown52" in values:
            raise Refused("OPTIONS","HD1910 不使用 XL330 Shutdown")
        out["enable_protection"] = values.get("enable_protection",False)
        if "gains" in values:
            g = values["gains"]
            if not isinstance(g,list) or len(g)!=2:
                raise Refused("OPTIONS","gains=[KP,KD]")
            out["gains"] = [integer(v,0,254) for v in g]
        if "protection_current_a" in values:
            out["protection_current_a"] = finite(values["protection_current_a"],.05,1.5)
    return out


def desired(kind, target, raw, opts):
    p = profile(kind)
    result = {p["id_addr"]: target,p["baud_addr"]:p["baud_code"],p["mode_addr"]:p["mode"]}
    if kind == "xl330":
        result.update({9:0,62:255,20:(-512 if target in (13,23) else 0) & 0xffffffff})
        if opts.get("shutdown52"):
            result[63] = 52
    else:
        if "gains" in opts:
            result.update({21:opts["gains"][0],22:opts["gains"][1]})
        if opts.get("enable_protection"):
            result[19] = raw.get("19",0) | 0x0d  # preserve other protection bits
        if "protection_current_a" in opts:
            result[28] = round(opts["protection_current_a"]*1000/6.5)
    return result


def public_profiles():
    return dict(component_version="0.1.1",hardware_acceptance=False,
                profiles=[dict(id=k,protocol=v["protocol"],voltage_range=v["voltage_range"],
                    registers={str(a):dict(size=s,name=n) for a,(s,n) in v["registers"].items()})
                    for k,v in PROFILES.items()],
                layouts={n:layout(n) for n in ("microduck","xgo")},versions=list(VERSIONS),
                sources=dict(xgo=XGO_REV,feetech_sdk=FT_REV),
                warnings=["软件核验不代表实机验收","硬件中位、机械零位与HOME不同",
                          "工作台不使官方DYNAMIXEL daemon自动支持Feetech"])

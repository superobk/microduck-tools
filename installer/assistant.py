"""One offline kit, one next step. Delegate transactions to their original owners."""

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

SCHEMA = "microduck-install-kit/v1"
STATE = Path("/var/lib/microduck-install-assistant")
TITLES = {
    "backup": "保存升级退路",
    "install": "换成官方0.18.3",
    "configure": "接回原校正与Tools",
    "start": "启动待验证的机器人程序",
    "studio-install": "安装独立Studio",
    "studio-start": "启动Studio只读界面",
}
WHY = {
    "backup": "先核对运行库，再用已有整盘镜像保存应用备份；此步会停止原机器人服务。",
    "install": "使用原可信更新器验证固定签名包；仅替换官方套件，不执行运动。",
    "configure": "沿用真实IMU方向/比例和15关节校正，写入0.18.3用户层；保持no-policy。",
    "start": "文件已就位，明确启动robotd和原Tools；会打开总线，现场须按原流程支撑并断舵机动力。",
    "studio-install": "离线建立独立普通用户服务；控制和Camera默认关闭，不改变robotd。",
    "studio-start": "只启动Studio及受限助手；不初始化舵机、不启用policy、不接管手柄。",
}


class Refused(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text())


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def choose_next(robot, studio):
    """Pure state mapping of the pinned 0.5.0 status contract. Unknown means review."""

    def result(step, title, reason):
        return dict(step=step, title=title, reason=reason, actionable=step in TITLES)

    if studio.get("busy"):
        return result(
            "busy", "Studio原任务仍在执行", "等待原任务，不能重复安装或恢复。"
        )
    if studio.get("handoff_pending"):
        return result(
            "handoff", "先明确交还手柄", "暂停标记仍在；不切版本、不自动恢复手柄。"
        )
    if not studio.get("ok", False):
        return result(
            "review",
            "Studio登记有异常",
            "；".join(studio.get("problems", [])) or "先看状态与证据，不重复安装。",
        )
    if studio.get("phase") in ("preparing", "configuring"):
        return result(
            "studio-recover",
            "Studio安装/配置未完成",
            "选择恢复；不要覆盖登记或直接重装。",
        )
    actual = robot.get("actual") or {}
    stage = robot.get("stage", "")
    if actual.get("pending") or robot.get("operation") and stage.startswith("正在执行"):
        return result(
            "busy", "原任务仍在执行", "等待原任务，不能重复提交或启动另一安装。"
        )
    if robot.get("error") or robot.get("last_error") or robot.get("operation"):
        return result(
            "recover",
            "上一步失败或未完成",
            "先查看原错误并选择机器人恢复；不再次提交安装。",
        )
    if robot.get("mode") == "本机预览":
        return result(
            "preview",
            "当前只是本机审阅",
            "真实升级需在Linux ARM64目标板；本轮不会连接远端。",
        )
    if stage == "备份介质离线":
        return result(
            "media", "请接回原备份介质", "镜像与证明必须可访问；不绕过原升级器检查。"
        )
    steps = {
        "待备份": "backup",
        "备份未完成，原服务可能已停止": "backup",
        "已备份，待安装": "install",
        "官方已安装，待配置校正": "configure",
        "已配置，待启动": "start",
    }
    if stage in steps:
        if (
            studio.get("units", {})
            .get("microduck-studio.service", {})
            .get("ActiveState")
            == "active"
        ):
            return result(
                "studio-stop",
                "先明确停止Studio",
                "版本维护不能与活动Studio混用；不会自动交还手柄。",
            )
        step = steps[stage]
        return result(step, TITLES[step], WHY[step])
    if (
        actual.get("release") != "0.18.3"
        or actual.get("official_integrity") is not True
    ):
        return result("review", "实际版本/完整性还不能确认", "先检查，不猜测当前版本。")
    accepted = (
        "进程已启动，等待真实回执",
        "校正已加载；插件和硬件分别查看",
        "检测到外部已安装版本，尚未接管",
    )
    if stage not in accepted:
        return result(
            "review",
            "保留当前模式，先审阅",
            "官方回退模式、撤销或未知状态不会被“下一步”自动改回校正模式。",
        )
    if not studio.get("installed"):
        if studio.get("phase") not in ("not_installed", "rolled_back"):
            return result("review", "Studio状态未知", "先查看安装登记。")
        return result(
            "studio-install",
            TITLES["studio-install"],
            WHY["studio-install"] + " 真实校正回执仍单独验收。",
        )
    units = studio.get("units", {})
    if units.get("microduck-studio.service", {}).get("ActiveState") != "active":
        return result("studio-start", TITLES["studio-start"], WHY["studio-start"])
    return result(
        "done",
        "软件步骤完成；实机验收待确认",
        "生成登录码后看网页；运行进程、校正、真实设备和资源分别验证，不自动运动。",
    )


class Kit:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.checked = False

    def verify(self):
        if not (self.root / "KIT-MANIFEST.json").is_file():
            raise Refused(
                "当前是源码或不完整目录；请解压install-kit后运行同一duck.sh。源码可用 ./duck.sh guide 查看流程。"
            )
        manifest = read_json(self.root / "KIT-MANIFEST.json")
        if manifest.get("schema") != SCHEMA:
            raise Refused(
                "不是完整安装套件；请解压deliverables中的install-kit，再运行同一duck.sh入口。"
            )
        if not isinstance(manifest.get("files"), list):
            raise Refused("套件清单格式错误，未执行。")
        for row in manifest["files"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("path"), str)
                or not isinstance(row.get("sha256"), str)
            ):
                raise Refused("套件清单条目错误，未执行。")
            path = self.root / row["path"]
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(self.root)
                or not path.is_file()
                or sha(path) != row["sha256"]
            ):
                raise Refused("套件文件缺失/改变：" + row["path"])
        if (self.root / "upgrade/tool/VERSION").read_text().strip() != "0.5.0":
            raise Refused("机器人升级器必须保持冻结0.5.0")
        self.checked = True
        return manifest

    def manager(self, *args):
        return [
            sys.executable,
            str(self.root / "upgrade/tool/tools/duck-manager.py"),
            "--bundle",
            str(self.root / "upgrade"),
            *args,
        ]

    def studio(self, *args):
        return [sys.executable, str(self.root / "installer/studio_cli.py"), *args]

    def assets(self):
        return [
            "--assets",
            str(self.root / "studio/runtime-assets"),
            "--wheels",
            str(self.root / "studio/wheels"),
        ]


def query(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise Refused(
            result.stderr.strip()[-1000:] or "状态回复不是JSON，先检查包和Python依赖。"
        )
    if not isinstance(data, dict):
        raise Refused("状态回复不是对象，未执行。")
    if result.returncode and data.get("ok") is not False:
        raise Refused(result.stderr.strip()[-1000:] or "状态读取失败")
    return data


def run_child(command):
    # No shell, timeout kill or automatic replay of a privileged transaction.
    process = subprocess.Popen(command)
    try:
        code = process.wait()
        if code:
            raise Refused(
                "原步骤未完成（退出码" + str(code) + "）；先看状态/恢复，不重复安装。"
            )
    except KeyboardInterrupt:
        raise Refused(
            "前台已中断；原更新事务可能仍在运行。先status，再决定recover，不能重复install。"
        )


@contextlib.contextmanager
def operation_lock(state=STATE):
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = state.lstat()
    if state.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Refused("助手锁目录须为当前root私有真实目录")
    fd = os.open(
        state / "operation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("另一个安装助手仍在工作；先查看status。")
        yield
    finally:
        os.close(fd)


class Assistant:
    def __init__(self, kit, reader=query, runner=run_child, target=None):
        self.kit = kit
        self.reader = reader
        self.runner = runner
        self.target = target or (
            lambda: platform.system() == "Linux"
            and platform.machine() in ("arm64", "aarch64")
            and os.geteuid() == 0
        )

    def snapshot(self):
        self.kit.verify()
        robot = self.reader(self.kit.manager("status", "--json"))
        studio = self.reader(self.kit.studio("status", "--json"))
        return dict(
            robot=robot,
            studio=studio,
            next=choose_next(robot, studio),
            hardware="NOT_RUN",
        )

    def execute(self, step, apply=False, ip=None, confirm=None):
        report = self.snapshot()
        if step == "next":
            step = report["next"]["step"]
        commands = []
        if step in ("backup", "install", "configure", "start"):
            if report["next"]["step"] != step:
                raise Refused("状态已改变，重新查看下一步；不按旧状态继续。")
            commands = [self.kit.manager("check")] if step == "backup" else []
            commands.append(
                self.kit.manager("services", "start", "--apply")
                if step == "start"
                else self.kit.manager(step, "--apply")
            )
        elif step == "studio-install":
            if report["next"]["step"] != "studio-install":
                raise Refused("当前不需要或不能重复安装Studio；先看状态。")
            if ip is None and apply:
                if not sys.stdin.isatty():
                    raise Refused(
                        "安装Studio需要--server-ip，或在交互菜单中输入实际局域网地址。"
                    )
                ip = input("板端固定局域网IPv4（用于HTTPS证书和入口）：").strip()
            if ip is not None:
                address = ipaddress.ip_address(ip)
                if (
                    address.version != 4
                    or address.is_unspecified
                    or address.is_multicast
                    or str(address) == "255.255.255.255"
                ):
                    raise Refused("请输入实际IPv4，不能用0.0.0.0/组播/广播地址。")
            commands = [
                self.kit.studio(
                    "setup",
                    *self.kit.assets(),
                    "--server-ip",
                    ip or "待输入实际IP",
                    "--apply",
                    "--confirm",
                    "EXECUTE"
                )
            ]
        elif step == "studio-start":
            if report["next"]["step"] != "studio-start":
                raise Refused("当前不应启动Studio；先看状态。")
            commands = [self.kit.studio("start", "--apply", "--confirm", "EXECUTE")]
        else:
            raise Refused(report["next"]["title"] + "：" + report["next"]["reason"])
        print("本次只做：" + TITLES[step] + "\n意义：" + WHY[step], flush=True)
        if not apply:
            print("预览：没有执行；在菜单选N，或 next --apply 后按原提示确认。")
            return
        if not self.target():
            raise Refused("真实安装/升级仅在Linux ARM64板端以root运行；未执行。")
        if step.startswith("studio-"):
            doctor = (
                self.reader(self.kit.studio("doctor", *self.kit.assets(), "--json"))
                if step == "studio-install"
                else {"ok": True}
            )
            if not doctor.get("ok"):
                raise Refused(
                    "Studio依赖检查未通过；未创建账号/改服务："
                    + "；".join(
                        c["item"] + "：" + c["detail"]
                        for c in doctor.get("checks", [])
                        if not c["ok"]
                    )
                )
            require_confirmation(confirm, "EXECUTE")
        for command in commands:
            self.runner(command)
        print("本步骤已返回。请刷新状态；不会自动执行下一步或启用运动。", flush=True)

    def configure(self, controls=None, camera=None, apply=False, confirm=None):
        report = self.snapshot()
        if not report["studio"].get("installed") or not report["studio"].get("ok"):
            raise Refused("先完成Studio安装，不能覆盖异常登记。")
        if (
            controls not in (None, "on", "off")
            or camera not in (None, "on", "off")
            or controls is None
            and camera is None
        ):
            raise Refused("指定控制/Camera开关。")
        print("只保存Studio开关并停止Studio；robotd不变；重新start后页面核对运行值。")
        command = self.kit.studio("configure")
        if controls is not None:
            command += ["--controls", controls]
        if camera is not None:
            command += ["--camera", camera]
        if not apply:
            print("预览：未保存。")
            return
        if not self.target():
            raise Refused("此操作只在Linux ARM64 root执行。")
        require_confirmation(confirm, "EXECUTE")
        self.runner(command + ["--apply", "--confirm", "EXECUTE"])

    def utility(self, action, apply=False, confirm=None):
        self.kit.verify()
        studio = {
            "studio-stop": "stop",
            "pair": "pair",
            "hand-back": "hand-back",
            "studio-recover": "recover",
            "studio-rollback": "rollback",
        }
        manager = {
            "calibration": ["configure", "web"],
            "camera-maintenance": ["configure", "web", "--camera-apply"],
            "reload-calibration": ["services", "reload"],
            "robot-recover": ["recover"],
            "robot-stop": ["services", "stop"],
            "robot-official": ["rollback", "--to", "official"],
            "upgrade-session": ["rollback", "--to", "session"],
            "image-backup": ["image", "backup"],
            "image-restore": ["image", "restore"],
        }
        print("范围：" + action + "；机器人回退、Studio回退与整盘恢复是不同操作。")
        if action in studio:
            command = self.kit.studio(
                studio[action],
                "--apply",
                "--confirm",
                "NEUTRAL" if action == "hand-back" else "EXECUTE",
            )
        elif action in manager:
            command = self.kit.manager(*manager[action], "--apply")
        else:
            raise Refused("未知操作")
        if not apply:
            print("预览：未执行。")
            return
        if action.startswith("image-"):
            if platform.system() != "Linux" or os.geteuid() != 0:
                raise Refused("镜像操作只在Linux救援环境以root运行。")
        elif not self.target():
            raise Refused("此操作只在Linux ARM64板端以root运行。")
        if action in manager and not action.startswith("image-"):
            snapshot = self.snapshot()
            if (
                snapshot["studio"].get("handoff_pending")
                or snapshot["studio"]
                .get("units", {})
                .get("microduck-studio.service", {})
                .get("ActiveState")
                == "active"
            ):
                raise Refused("先明确停止Studio并处理手柄交接，不能并行改机器人服务。")
        if action in studio:
            require_confirmation(
                confirm, "NEUTRAL" if action == "hand-back" else "EXECUTE"
            )
        self.runner(command)


def require_confirmation(provided, word):
    if provided == word:
        return
    if not sys.stdin.isatty():
        raise Refused("执行需要交互确认，或明确提供 --confirm " + word)
    print("确认现场允许本次操作；交还手柄还须所有按键释放/摇杆居中。")
    if input("输入 " + word + " 执行，否则取消：").strip() != word:
        raise Refused("已取消，未执行。")


def display(report):
    robot = report["robot"]
    studio = report["studio"]
    actual = robot.get("actual") or {}
    units = studio.get("units", {})
    print("\nDuck 安装助手 | 目标：robotd 0.18.3 + Studio 0.6.0")
    print(
        "机器人："
        + str(actual.get("release") or "未在板端核验")
        + " | "
        + robot.get("stage", "未知")
    )
    print(
        "校正："
        + (
            "实际回执已一致"
            if robot.get("calibration_verified")
            else "待真实运行回执核对"
        )
    )
    phase = {
        "not_installed": "未安装",
        "installed": "已安装",
        "preparing": "安装未完成",
        "configuring": "配置未完成",
        "rolled_back": "已撤销",
        "busy": "任务正在执行",
    }.get(studio.get("phase"), "未知")
    active = units.get("microduck-studio.service", {}).get("ActiveState")
    print(
        "Studio："
        + phase
        + " | "
        + {"active": "进程运行中", "inactive": "已停止", "failed": "启动失败"}.get(
            active, "进程待核对"
        )
    )
    if studio.get("settings"):
        print(
            "网页："
            + str(studio["settings"].get("public_origin"))
            + "/studio/；控制="
            + str(studio["settings"].get("controls_enabled"))
            + " / Camera="
            + str(studio["settings"].get("camera_enabled"))
        )
    print(
        "下一步：" + report["next"]["title"] + "\n为什么：" + report["next"]["reason"]
    )
    if robot.get("last_error") or robot.get("error"):
        print("原错误：" + str(robot.get("last_error") or robot.get("error")))
    print("硬件：待现场验证；不会自动init、开动力、运动或交还手柄。")


GUIDE = """简明升级路径：
1. 先在独立Linux救援系统制作原盘镜像（菜单I），回到当前0.16.3。
2. 进入完整安装套件，运行 sudo ./duck.sh。
3. 按 N 继续下一步：检查+备份 → 官方0.18.3 → 原校正/Tools → 明确启动。
4. 再按 N：输入一次板端IP → 安装Studio → 明确启动只读页面。
5. 按 P 生成登录码；核对CA指纹并信任，再在手机/电脑打开显示的HTTPS地址。
每次N只执行一步；断线后重开同一入口。显示中断时先恢复，不重复安装。
回退分为Studio、同版官方、安装前撤销、救援镜像四类；按R查看范围。
"""


def require_host(image=False):
    if (
        platform.system() != "Linux"
        or os.geteuid() != 0
        or not image
        and platform.machine() not in ("arm64", "aarch64")
    ):
        raise Refused(
            "真实操作需Linux root；机器人/Studio还需ARM64。镜像在独立Linux救援系统运行。"
        )
    if not image and sys.version_info[:2] != (3, 11):
        raise Refused(
            "完整安装套件板端执行要求Python3.11；入口优先查找python3.11。请先匹配环境，没有执行升级。"
        )


def menu(app):
    if not sys.stdin.isatty():
        raise Refused("菜单需要终端；只读用 status 或 guide。")
    while True:
        try:
            print("正在核对材料和两个服务状态（只读）…", flush=True)
            display(app.snapshot())
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            print("状态不能确认：" + str(e))
        print(
            "N 下一步 | S 刷新 | P 登录码 | C 配置 | R 恢复/回退 | I 整盘镜像 | X 停Studio | Q 退出"
        )
        choice = input("选择：").strip().lower()
        try:
            if choice == "q":
                return
            if choice in ("n", "p", "x"):
                require_host()
                with operation_lock():
                    if choice == "n":
                        app.execute("next", apply=True)
                    else:
                        app.utility(
                            "pair" if choice == "p" else "studio-stop", apply=True
                        )
            elif choice == "c":
                print(
                    "1 Studio仅监控（控制/Camera都关）\n2 Studio开关（输入on/off，保存后保持停止）\n3 原IMU/关节维护Web\n4 相机应用维护（可能重启mediad）\n5 加载已保存校正（重启robotd/Tools）"
                )
                item = input("选择（空白取消）：").strip()
                if item in ("1", "2", "3", "4", "5"):
                    require_host()
                    with operation_lock():
                        if item == "1":
                            app.configure("off", "off", apply=True)
                        elif item == "2":
                            app.configure(
                                input("控制 on/off：").strip(),
                                input("Camera on/off：").strip(),
                                apply=True,
                            )
                        else:
                            app.utility(
                                {
                                    "3": "calibration",
                                    "4": "camera-maintenance",
                                    "5": "reload-calibration",
                                }[item],
                                apply=True,
                            )
            elif choice in ("r", "i"):
                options = (
                    [
                        (
                            "image-backup",
                            "制作原盘镜像（救援系统；读取原盘，写外部介质）",
                        ),
                        ("image-restore", "恢复整盘（救援系统；覆盖目标盘）"),
                    ]
                    if choice == "i"
                    else [
                        ("robot-recover", "核对机器人中断事务，不重新提交安装"),
                        ("studio-recover", "撤销Studio未完成安装/配置，不动robotd"),
                        ("hand-back", "全部输入释放后，明确交还手柄"),
                        ("studio-rollback", "卸下Studio，保留robotd及版本文件"),
                        ("robot-official", "退回同版官方0.18.3，不是回0.16.3"),
                        ("upgrade-session", "安装提交前取消维护，恢复原状态"),
                    ]
                )
                for i, (_, meaning) in enumerate(options, 1):
                    print(str(i) + " " + meaning)
                item = input("选择（空白取消）：").strip()
                if item.isdigit() and 1 <= int(item) <= len(options):
                    action = options[int(item) - 1][0]
                    require_host(action.startswith("image-"))
                    with operation_lock():
                        app.utility(action, apply=True)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            print("已停止：" + str(e))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kit", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=[
            "menu",
            "guide",
            "status",
            "next",
            "studio-install",
            "studio-start",
            "configure",
            "calibration",
            "camera-maintenance",
            "reload-calibration",
            "pair",
            "studio-stop",
            "robot-stop",
            "hand-back",
            "robot-recover",
            "studio-recover",
            "studio-rollback",
            "robot-official",
            "upgrade-session",
            "image-backup",
            "image-restore",
        ],
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm")
    p.add_argument("--server-ip")
    p.add_argument("--controls", choices=["on", "off"])
    p.add_argument("--camera", choices=["on", "off"])
    a = p.parse_args()
    if a.command == "guide":
        print(GUIDE)
        return
    if sys.version_info[:2] < (3, 11):
        raise Refused(
            "安装套件要求Python3.11+才能核验；板端离线执行使用3.11，未执行操作。"
        )
    app = Assistant(Kit(a.kit))
    if a.command == "menu":
        app.kit.verify()
        menu(app)
        return
    if a.command == "status":
        result = app.snapshot()
        (
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if a.json
            else display(result)
        )
        return
    if a.json:
        raise Refused("--json只用于只读status。")

    def execute():
        if a.command == "configure":
            app.configure(a.controls, a.camera, a.apply, a.confirm)
        elif a.command in ("next", "studio-install", "studio-start"):
            app.execute(a.command, a.apply, a.server_ip, a.confirm)
        else:
            app.utility(a.command, a.apply, a.confirm)

    if a.apply:
        # Before creating even the lock directory, refuse unsupported hosts.
        require_host(a.command.startswith("image-"))
        with operation_lock():
            execute()
    else:
        execute()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print("停止：" + str(e), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("已退出；先status确认原事务，不重复安装。", file=sys.stderr)
        raise SystemExit(130)

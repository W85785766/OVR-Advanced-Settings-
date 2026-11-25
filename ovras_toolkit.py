#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OVR 一键工具 (OVRAS Toolkit)
============================

给 SteamVR + NOLO / 一体机串流用户准备的一键小工具，做三件事：

1. SteamVR 仪表盘修复
   NOLO / 一体机串流套装、网吧预设、某些优化脚本会把 SteamVR 的仪表盘
   整个关掉（dashboard.enableDashboard = false），导致按系统键毫无反应、
   仪表盘里的应用（OVR Advanced Settings、桌面、Steam 库……）全都打不开。
   本工具会：备份配置 -> 打开仪表盘 -> 打开"桌面"页签 -> 关闭街机模式。

2. 一键安装 OVR Advanced Settings
   从 GitHub 官方仓库下载最新发布包（免付费的开源版），解压到指定目录，
   并注册进 SteamVR（--force-install-manifest）。

3. 一键汉化 OVR Advanced Settings
   把 OVRAS 的 QML 界面文本替换为简体中文（内置 249 条对照表），
   自动备份英文原版，可随时一键还原。

4. 修复 SteamVR「绑定」页白屏
   SteamVR 2.18.x 的绑定页（controllerbindingui.js）在「该应用没有用户
   自定义绑定」时会读到 null 而抛 TypeError，整页渲染中断变成空白，
   而且崩掉的页面不会自动重载。本工具给那段代码补上空值保护，并在没有
   自定义绑定时回落到应用自带的默认绑定。修改会自动备份，可一键还原。

只依赖 Python 标准库。解压 7z 需要 7-Zip，找不到时会自动下载 7zr.exe。

用法：
    python ovras_toolkit.py            # 打开图形界面
    python ovras_toolkit.py status     # 查看当前状态
    python ovras_toolkit.py fix        # 修复 SteamVR 仪表盘
    python ovras_toolkit.py restore    # 用最近一次备份还原 SteamVR 配置
    python ovras_toolkit.py install    # 下载并安装 OVRAS 到 --dir（默认读配置）
    python ovras_toolkit.py localize   # 汉化已安装的 OVRAS
    python ovras_toolkit.py revert     # 还原英文界面
    python ovras_toolkit.py bindfix    # 修复 SteamVR「绑定」页白屏
    python ovras_toolkit.py bindrevert # 还原「绑定」页原始文件
    python ovras_toolkit.py all        # 安装 + 汉化 + 修复仪表盘 + 修复绑定页

许可证：MIT
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

APP_NAME = "OVR 一键工具"
APP_VER = "1.1.0"
if getattr(sys, "frozen", False):  # PyInstaller 打包后
    EXE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(getattr(sys, "_MEIPASS", EXE_DIR), "data")
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(EXE_DIR, "data")

CONFIG_PATH = os.path.join(EXE_DIR, "config.json")
DATA_PATH = os.path.join(DATA_DIR, "zh_cn.json")
GITHUB_REPOS = [
    "OpenVR-Advanced-Settings/OpenVR-AdvancedSettings",
    "OpenVR-AdvancedSettings/OpenVR-AdvancedSettings",
]
UA = {"User-Agent": "OVRAS-Toolkit/%s" % APP_VER}

# SteamVR 仪表盘相关的三项设置
DASHBOARD_FIX = {
    "enableDashboard": True,   # 仪表盘总开关
    "showDesktop": True,       # 仪表盘里的"桌面"页签
    "arcadeMode": False,       # 街机模式会进一步裁剪仪表盘
}


# ---------------------------------------------------------------- 基础工具

def _attach_console():
    """打包成窗口程序后，命令行模式仍要把输出接到父控制台。"""
    if not getattr(sys, "frozen", False) or len(sys.argv) <= 1:
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
    except Exception:  # noqa: BLE001
        pass


def _init_console():
    """让 Windows 控制台能正常显示中文。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def log(msg):
    try:
        print(msg, flush=True)
    except (BrokenPipeError, ValueError):
        pass


def run(cmd, cwd=None, timeout=120):
    """执行命令，返回 (returncode, 输出)。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def steam_libraries():
    """扫描所有 Steam 库目录（主目录 + libraryfolders.vdf 里的）。"""
    libs = []
    steam = find_steam()
    if steam:
        libs.append(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="ignore") as f:
                for m in re.finditer(r'"path"\s*"([^"]+)"', f.read()):
                    p = m.group(1).replace("\\\\", "\\")
                    if os.path.isdir(p):
                        libs.append(p)
        except OSError:
            pass
    # 去重
    out, seen = [], set()
    for p in libs:
        k = os.path.normcase(os.path.normpath(p))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def detect_ovras():
    """自动探测已安装的 OVRAS（Steam 版 / 便携版）。"""
    names = ("OVR Advanced Settings", "OpenVR-AdvancedSettings", "OpenVR Advanced Settings")
    cands = []
    for lib in steam_libraries():
        for n in names:
            cands.append(os.path.join(lib, "steamapps", "common", n))
    local = os.environ.get("LOCALAPPDATA", "")
    cands += [
        os.path.join(local, "Programs", "OVR Advanced Settings") if local else "",
        r"C:\OVR", r"D:\OVR", r"E:\OVR", r"F:\OVR",
    ]
    for p in cands:
        if p and os.path.isfile(os.path.join(p, "AdvancedSettings.exe")):
            return os.path.normpath(p)
    return None


def suggest_install_dir(cfg=None):
    """给界面用的建议目录：配置 -> 自动探测 -> 用户目录（不硬编码盘符）。"""
    cfg = cfg if cfg is not None else load_config()
    if cfg.get("install_dir") and os.path.isdir(cfg["install_dir"]):
        return cfg["install_dir"]
    return detect_ovras() or os.path.join(os.path.expanduser("~"), "OVR")


def require_install_dir(arg=None, interactive=True):
    """操作前解析安装目录；没有就让用户选，而不是猜一个。"""
    cfg = load_config()
    if arg:
        return os.path.abspath(arg)
    if cfg.get("install_dir"):
        return cfg["install_dir"]
    found = detect_ovras()
    if found:
        log("[检测] 发现已安装的 OVRAS：%s" % found)
        return found
    if interactive:
        log("[提示] 没有自动找到 OVRAS，请输入它的安装目录（含 AdvancedSettings.exe），直接回车取消。")
        try:
            entered = input("> ").strip().strip('"')
        except EOFError:
            entered = ""
        if entered:
            return os.path.abspath(entered)
    raise SystemExit("[提示] 未指定安装目录。请加 --dir 参数，或在图形界面里点「浏览…」选择。")


# ---------------------------------------------------------------- SteamVR

def find_steam():
    """返回 Steam 安装目录。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            path = winreg.QueryValueEx(k, "SteamPath")[0]
        if path and os.path.isdir(path):
            return os.path.normpath(path)
    except Exception:  # noqa: BLE001
        pass
    for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
              r"D:\Steam", r"E:\Steam"):
        if os.path.isdir(p):
            return p
    return None


def vrsettings_path():
    steam = find_steam()
    if not steam:
        return None
    return os.path.join(steam, "config", "steamvr.vrsettings")


def steamvr_running():
    rc, out = run(["tasklist", "/FI", "IMAGENAME eq vrserver.exe", "/NH"])
    return "vrserver.exe" in out.lower()


def backup_file(path):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-%s" % (path, ts)
    shutil.copy2(path, dst)
    return dst


def list_backups(path):
    d = os.path.dirname(path)
    base = os.path.basename(path) + ".bak-"
    items = [os.path.join(d, n) for n in os.listdir(d) if n.startswith(base)]
    return sorted(items, reverse=True)


def fix_dashboard(force=False):
    """把 SteamVR 仪表盘恢复成可用状态。"""
    path = vrsettings_path()
    if not path or not os.path.isfile(path):
        log("[错误] 没有找到 steamvr.vrsettings，请确认 SteamVR 已安装并运行过一次。")
        return False
    if steamvr_running() and not force:
        log("[中止] SteamVR 正在运行。请先完全退出 SteamVR 再执行，")
        log("       否则 SteamVR 退出时会把内存里的旧配置写回去。")
        return False

    with open(path, encoding="utf-8") as f:
        text = f.read()

    bk = backup_file(path)
    log("[备份] %s" % bk)

    changed = []

    def set_key(raw, key, want):
        pattern = r'("%s"\s*:\s*)(true|false|0|1)' % re.escape(key)
        found = False

        def repl(m):
            nonlocal found
            found = True
            cur = m.group(2).lower() in ("true", "1")
            if cur == want:
                return m.group(0)
            return m.group(1) + ("true" if want else "false")

        new = re.sub(pattern, repl, raw)
        return new, found

    for key, want in DASHBOARD_FIX.items():
        text, found = set_key(text, key, want)
        if found:
            changed.append(key)
        else:
            # 键不存在：塞进 dashboard 段
            text = _ensure_dashboard_key(text, key, want)
            changed.append(key + "(新增)")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    # 校验
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        dash = data.get("dashboard", {})
        ok = all(bool(dash.get(k)) == v for k, v in DASHBOARD_FIX.items())
    except Exception as e:  # noqa: BLE001
        log("[错误] 写入后 JSON 解析失败：%s" % e)
        log("       已保留备份，可执行 restore 还原。")
        return False

    log("[完成] 已修改：%s" % ", ".join(changed))
    log("[状态] " + ", ".join("%s=%s" % (k, dash.get(k)) for k in DASHBOARD_FIX))
    if not ok:
        log("[警告] 校验未全部通过，请检查上面的状态。")
    log("[提示] 重启 SteamVR 后生效。")
    return ok


def _ensure_dashboard_key(text, key, want):
    """在 dashboard 段落里补一个键（不改动其它内容）。"""
    m = re.search(r'"dashboard"\s*:\s*\{', text)
    if not m:
        # 没有 dashboard 段：在结尾补一段
        body = text.rstrip()
        if body.endswith("}"):
            body = body[:-1].rstrip()
            if not body.endswith(","):
                body += ","
            body += '\n   "dashboard" : {\n      "%s" : %s\n   }\n}\n' % (
                key, "true" if want else "false")
            return body
        return text
    insert_at = m.end()
    value = "true" if want else "false"
    return text[:insert_at] + '\n      "%s" : %s,' % (key, value) + text[insert_at:]


def restore_dashboard():
    path = vrsettings_path()
    if not path:
        log("[错误] 没有找到 steamvr.vrsettings")
        return False
    if steamvr_running():
        log("[中止] SteamVR 正在运行，请先退出后再还原。")
        return False
    backups = list_backups(path)
    if not backups:
        log("[错误] 没有找到备份文件。")
        return False
    src = backups[0]
    backup_file(path)
    shutil.copy2(src, path)
    log("[完成] 已用 %s 还原。" % os.path.basename(src))
    return True


# ---------------------------------------------------------------- 绑定页白屏

BINDING_UI_REL = os.path.join("resources", "webinterface", "dashboard",
                              "controllerbindingui.js")

# 【1】没有用户自定义绑定时 SteamVR 给出 current_binding_url = null，
#     原版直接 .hasOwnProperty() 会抛 TypeError，整页白屏。
BINDING_OLD1 = (
    "null==this.m_sLoadedBindingURI&&t.current_binding_url.hasOwnProperty("
    "this.m_sSelectedControllerType)&&(this.m_sLoadedBindingURI="
    "t.current_binding_url[this.m_sSelectedControllerType])")
BINDING_NEW1 = (
    "null==this.m_sLoadedBindingURI&&(t.current_binding_url&&"
    "t.current_binding_url.hasOwnProperty(this.m_sSelectedControllerType)"
    "?this.m_sLoadedBindingURI=t.current_binding_url[this.m_sSelectedControllerType]"
    ":t.default_bindings&&t.default_bindings[this.m_sSelectedControllerType]&&"
    "(this.m_sLoadedBindingURI=t.default_bindings[this.m_sSelectedControllerType]))")
# 【2】action_sets 为 null 时的同类崩溃
BINDING_OLD2 = (
    "null==this.m_sSelectedActionSet||!this.m_SelectedAppActions.action_sets."
    "hasOwnProperty(this.m_sSelectedActionSet)")
BINDING_NEW2 = (
    "null==this.m_sSelectedActionSet||!this.m_SelectedAppActions.action_sets||"
    "!this.m_SelectedAppActions.action_sets.hasOwnProperty("
    "this.m_sSelectedActionSet)")
BINDING_MARK = "t.current_binding_url&&t.current_binding_url.hasOwnProperty"


def steamvr_dir():
    """返回 SteamVR 安装目录。"""
    for lib in steam_libraries():
        d = os.path.join(lib, "steamapps", "common", "SteamVR")
        if os.path.isdir(d):
            return os.path.normpath(d)
    return None


def binding_ui_path():
    d = steamvr_dir()
    if not d:
        return None
    p = os.path.join(d, BINDING_UI_REL)
    return p if os.path.isfile(p) else None


def binding_patch_state():
    """返回 (状态文字, 文件路径)。"""
    p = binding_ui_path()
    if not p:
        return "未找到 SteamVR", None
    try:
        with open(p, encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()
    except OSError as e:
        return "读取失败: %s" % e, p
    if BINDING_MARK in text:
        return "已修复", p
    if BINDING_OLD1 in text:
        return "未修复（建议修复）", p
    return "代码版本未知（可能已升级）", p


def fix_binding_page(dry_run=False):
    """给 SteamVR 绑定页补上空值保护，修掉白屏。"""
    p = binding_ui_path()
    if not p:
        log("[错误] 没有找到 SteamVR 的绑定页文件，请确认 SteamVR 已安装。")
        return False
    with open(p, encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()
    log("[文件] %s" % p)
    if BINDING_MARK in text:
        log("[跳过] 已经修复过了，无需重复操作。")
        return True
    if BINDING_OLD1 not in text:
        log("[提示] 没找到预期代码，SteamVR 版本可能不同，未做任何修改。")
        return False

    new = text.replace(BINDING_OLD1, BINDING_NEW1, 1)
    if BINDING_OLD2 in new:
        new = new.replace(BINDING_OLD2, BINDING_NEW2, 1)
        log("[加固] 同时给 action_sets 加上空值判断")
    if dry_run:
        log("[预演] 将替换 1 处（+%d 字符），未写入。" % (len(new) - len(text)))
        return True

    bk = backup_file(p)
    log("[备份] %s" % bk)
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(new)

    with open(p, encoding="utf-8", errors="surrogateescape", newline="") as f:
        check = f.read()
    if BINDING_MARK not in check:
        log("[错误] 写入后校验失败，请用「还原绑定页」恢复。")
        return False
    log("[完成] 已修补绑定页（%d -> %d 字符）。" % (len(text), len(check)))
    log("[提示] 回到 SteamVR 重新打开「绑定」页即可生效，不需要重启 SteamVR。")
    log("       若仍空白，退出 SteamVR 再启动一次。")
    return True


def revert_binding_page():
    p = binding_ui_path()
    if not p:
        log("[错误] 没有找到 SteamVR 的绑定页文件。")
        return False
    backups = list_backups(p)
    if not backups:
        log("[错误] 没有找到备份文件，无法还原。")
        return False
    src = backups[0]
    backup_file(p)
    shutil.copy2(src, p)
    log("[完成] 已用 %s 还原绑定页。" % os.path.basename(src))
    log("[提示] 重新打开「绑定」页生效；若仍空白，重启一次 SteamVR。")
    return True


def show_status():
    log("== 环境 ==")
    steam = find_steam()
    log("Steam 目录 : %s" % (steam or "未找到"))
    path = vrsettings_path()
    cfg = load_config()
    log("SteamVR 配置: %s" % (path or "未找到"))
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            dash = data.get("dashboard", {})
            for k, v in DASHBOARD_FIX.items():
                cur = dash.get(k, "(无)")
                flag = "OK " if cur == v else "需修"
                log("  %s %-16s = %s" % (flag, k, cur))
        except Exception as e:  # noqa: BLE001
            log("  解析失败: %s" % e)
    log("SteamVR 运行: %s" % ("是（修改前请退出）" if steamvr_running() else "否"))
    log("备份数量   : %d" % (len(list_backups(path)) if path else 0))
    state, bpath = binding_patch_state()
    log("绑定页修复 : %s" % state)
    if bpath:
        log("绑定页文件 : %s" % bpath)

    log("== OVRAS ==")
    d = suggest_install_dir(cfg)
    found = detect_ovras()
    log("自动探测   : %s" % (found or "未找到（可手动选择目录）"))
    log("当前目录   : %s" % d)
    exe = os.path.join(d, "AdvancedSettings.exe")
    log("主程序     : %s" % ("存在" if os.path.isfile(exe) else "不存在"))
    qml = os.path.join(d, "res", "qml")
    bak = os.path.join(d, "res", "qml_en_backup")
    if os.path.isdir(qml):
        n = _count_chinese(qml)
        log("界面语言   : %s（中文字符 %d）" % ("中文" if n > 0 else "英文", n))
    if os.path.isdir(bak):
        log("英文备份   : 存在 (%s)" % bak)


def _count_chinese(folder):
    total = 0
    for dp, _, files in os.walk(folder):
        for fn in files:
            if fn.lower().endswith(".qml"):
                with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as f:
                    total += len(re.findall(r"[\u4e00-\u9fff]", f.read()))
    return total


# ---------------------------------------------------------------- 安装 OVRAS

def find_7z():
    for p in (r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe",
              shutil.which("7z"), shutil.which("7za"), shutil.which("7zr")):
        if p and os.path.isfile(p):
            return p
    return None


def fetch_7zr(dest_dir):
    url = "https://www.7-zip.org/a/7zr.exe"
    dst = os.path.join(dest_dir, "7zr.exe")
    log("[下载] 7zr.exe（解压工具）…")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, \
            open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    return dst


def latest_release_7z():
    """返回 (tag, 下载地址, 文件名)。"""
    last_err = None
    for repo in GITHUB_REPOS:
        api = "https://api.github.com/repos/%s/releases/latest" % repo
        try:
            req = urllib.request.Request(api, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            tag = data.get("tag_name", "?")
            for a in data.get("assets", []):
                if a["name"].lower().endswith(".7z"):
                    return tag, a["browser_download_url"], a["name"]
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError("获取最新发布失败：%s" % last_err)


def download_file(url, dst):
    log("[下载] %s" % os.path.basename(dst))
    req = urllib.request.Request(url, headers=UA)

    def hook(blocks, block_size, total):
        if total > 0:
            pct = min(100, blocks * block_size * 100 // total)
            sys.stdout.write("\r        %3d%%" % pct)
            sys.stdout.flush()

    with urllib.request.urlopen(req, timeout=300) as r, open(dst, "wb") as f:
        length = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if length:
                sys.stdout.write("\r        %3d%%" % (done * 100 // length))
                sys.stdout.flush()
    sys.stdout.write("\r        100%\n")
    sys.stdout.flush()


def install_ovras(target_dir=None, archive=None, register=True):
    cfg = load_config()
    target_dir = target_dir or require_install_dir()
    os.makedirs(target_dir, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix="ovras_")
    try:
        if not archive:
            tag, url, name = latest_release_7z()
            log("[信息] 最新版本：%s（%s）" % (tag, name))
            archive = os.path.join(tmp, name)
            download_file(url, archive)
        else:
            log("[信息] 使用本地安装包：%s" % archive)

        seven = find_7z()
        if not seven:
            seven = fetch_7zr(tmp)
        log("[解压] -> %s" % target_dir)
        rc, out = run([seven, "x", archive, "-o" + target_dir, "-y"], timeout=600)
        if rc != 0:
            log("[错误] 解压失败：\n%s" % out[-2000:])
            return False

        exe = os.path.join(target_dir, "AdvancedSettings.exe")
        if not os.path.isfile(exe):
            log("[错误] 解压后没有找到 AdvancedSettings.exe，请检查安装包。")
            return False

        cfg["install_dir"] = target_dir
        save_config(cfg)
        log("[完成] OVRAS 已解压到 %s" % target_dir)

        if register:
            register_manifest(target_dir)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def register_manifest(target_dir):
    exe = os.path.join(target_dir, "AdvancedSettings.exe")
    log("[注册] 写入 SteamVR 应用清单 …")
    rc, out = run([exe, "--force-install-manifest"], cwd=target_dir, timeout=120)
    if rc == 0:
        log("[完成] 已注册，下次启动 SteamVR 即可在仪表盘里看到。")
        return True
    log("[警告] 注册返回码 %s，可稍后手动运行：\n  \"%s\" --force-install-manifest" % (rc, exe))
    return False


# ---------------------------------------------------------------- 汉化

STR = r'"((?:[^"\\]|\\.)*)"'
PROP_UI = re.compile(r'(?P<prop>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:qsTr\s*\(\s*)?' + STR)
PROP_OK = re.compile(
    r'^(text|title|subtitle|headerText|placeholderText|displayText|labelText|'
    r'valueText|tooltipText|unitText|descriptionText|hintText|suffixText|prefixText|'
    r'buttonText|messageText|infoText|nameText|detailText|statusText|warningText|'
    r'dialogTitle|dialogText|dialogMessage|message|.*Title|.*Message|.*Label|'
    r'.*Hint|.*Tooltip|.*Description|.*Text)$', re.I)
SKIP_PROPS = re.compile(r'^(color|source|image|font|icon|anchors|layout|import|model)$', re.I)
QS = re.compile(r'qsTr\s*\(\s*' + STR)
ASSIGN = re.compile(r'(?:\.\s*(?:text|label|title|message)\s*=\s*)' + STR)
CALL = re.compile(r'(?:showMessage|showKeyboard|setMessage)\s*\(\s*' + STR + r'\s*,\s*' + STR)


def _spans(src):
    out = []
    for m in PROP_UI.finditer(src):
        prop = m.group("prop")
        if not PROP_OK.match(prop) or SKIP_PROPS.match(prop):
            continue
        if src[m.end():m.end() + 8].lstrip()[:1] in ("+", ".", "?", "%"):
            continue
        out.append((m.start(2), m.end(2), m.group(2)))
    for m in QS.finditer(src):
        out.append((m.start(1), m.end(1), m.group(1)))
    for m in ASSIGN.finditer(src):
        out.append((m.start(1), m.end(1), m.group(1)))
    for m in CALL.finditer(src):
        out.append((m.start(1), m.end(1), m.group(1)))
        out.append((m.start(2), m.end(2), m.group(2)))
    return out


def load_pairs():
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {en: zh for en, zh in data["pairs"]}


def localize(target_dir=None, dry_run=False):
    cfg = load_config()
    target_dir = target_dir or require_install_dir()
    qml_dir = os.path.join(target_dir, "res", "qml")
    bak_dir = os.path.join(target_dir, "res", "qml_en_backup")
    if not os.path.isdir(qml_dir):
        log("[错误] 没有找到 %s，请先安装 OVRAS。" % qml_dir)
        return False

    pairs = load_pairs()
    total_files = total_hits = total_apply = 0
    matched_keys = set()
    for dp, _, files in os.walk(qml_dir):
        for fn in files:
            if not fn.lower().endswith(".qml"):
                continue
            path = os.path.join(dp, fn)
            with open(path, encoding="utf-8", newline="") as f:
                src = f.read()
            spans = _spans(src)
            if not spans:
                continue
            uniq = {}
            for s, e, val in spans:
                uniq[(s, e)] = val
            hits = sum(1 for v in uniq.values() if v in pairs)
            if hits == 0:
                continue
            total_files += 1
            total_hits += hits
            matched_keys.update(v for v in uniq.values() if v in pairs)
            if not dry_run:
                new = src
                for (s, e), val in sorted(uniq.items(), reverse=True):
                    if val in pairs:
                        new = new[:s] + pairs[val] + new[e:]
                if new != src:
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(new)
                total_apply += hits

    ratio = len(matched_keys) * 100.0 / max(1, len(pairs))
    if dry_run:
        log("[预演] 预计汉化 %d 个文件，%d 处文本，词条覆盖 %.0f%%。"
            % (total_files, total_hits, ratio))
        return True

    if total_files == 0:
        log("[提示] 没有可替换的英文文本，可能已经汉化过了。")
    else:
        log("[完成] 汉化 %d 个界面文件，替换 %d 处文本，词条覆盖 %.0f%%（%d/%d）。"
            % (total_files, total_apply, ratio, len(matched_keys), len(pairs)))
        if ratio < 80:
            log("[警告] 覆盖偏低，当前 OVRAS 版本可能不是 5.8.17，部分界面仍为英文。")

    if not os.path.isdir(bak_dir) and total_files > 0:
        log("[提示] 首次汉化，建议先备份英文版。")

    log("[提示] 重新启动 SteamVR 或 OVRAS 后即可看到中文界面。")
    return True


def backup_english(target_dir=None):
    cfg = load_config()
    target_dir = target_dir or require_install_dir()
    qml_dir = os.path.join(target_dir, "res", "qml")
    bak_dir = os.path.join(target_dir, "res", "qml_en_backup")
    if not os.path.isdir(qml_dir):
        log("[错误] 没有找到 %s" % qml_dir)
        return False
    if os.path.isdir(bak_dir):
        log("[跳过] 英文备份已存在：%s" % bak_dir)
        return True
    shutil.copytree(qml_dir, bak_dir)
    log("[完成] 英文备份 -> %s" % bak_dir)
    return True


def revert_english(target_dir=None):
    cfg = load_config()
    target_dir = target_dir or require_install_dir()
    qml_dir = os.path.join(target_dir, "res", "qml")
    bak_dir = os.path.join(target_dir, "res", "qml_en_backup")
    if not os.path.isdir(bak_dir):
        log("[错误] 找不到英文备份：%s" % bak_dir)
        return False
    shutil.rmtree(qml_dir, ignore_errors=True)
    os.rename(bak_dir, qml_dir)
    log("[完成] 已还原英文界面。")
    return True


# ---------------------------------------------------------------- 图形界面

def run_gui():  # pragma: no cover
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    cfg = load_config()
    root = tk.Tk()
    root.title("%s v%s" % (APP_NAME, APP_VER))
    root.geometry("900x640")
    root.minsize(820, 560)
    try:
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        ttk.Style(root).theme_use("vista")
    except Exception:  # noqa: BLE001
        pass

    head = tk.Frame(root)
    head.pack(fill="x", padx=12, pady=(10, 0))
    tk.Label(head, text="%s" % APP_NAME,
             font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
    tk.Label(head, text="OVR Advanced Settings 一键汉化 · SteamVR 仪表盘 / 绑定页修复（NOLO X1 等老设备）",
             fg="#666666").pack(anchor="w")

    top = tk.Frame(root)
    top.pack(fill="x", padx=12, pady=10)

    tk.Label(top, text="OVRAS 安装目录：").pack(side="left")
    dir_var = tk.StringVar(value=suggest_install_dir(cfg))
    tk.Entry(top, textvariable=dir_var, width=62).pack(side="left", padx=4, fill="x", expand=True)

    def browse():
        d = filedialog.askdirectory(initialdir=dir_var.get() or os.path.expanduser("~"))
        if d:
            dir_var.set(os.path.normpath(d))

    ttk.Button(top, text="浏览…", command=browse, width=8).pack(side="left")

    logbox = scrolledtext.ScrolledText(root, height=24, font=("Consolas", 10))
    logbox.pack(fill="both", expand=True, padx=12, pady=6)

    def ui_log(msg):
        logbox.insert("end", msg + "\n")
        logbox.see("end")
        root.update_idletasks()

    old_log = globals()["log"]

    def patched(msg):
        ui_log(msg)

    def worker(title, fn):
        globals()["log"] = patched
        ui_log("---- %s ----" % title)

        def run_fn():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                ui_log("[异常] %s" % e)
            finally:
                globals()["log"] = old_log
                ui_log("")

        threading.Thread(target=run_fn, daemon=True).start()

    def pick_dir_if_needed():
        """目录为空或无效时，让用户自己选一个，绝不猜。"""
        d = dir_var.get().strip()
        if d and os.path.isdir(d):
            return True
        dlg = filedialog.askdirectory(
            title="请选择 OVR Advanced Settings 的安装目录（或要安装到的目录）",
            initialdir=os.path.expanduser("~"))
        if not dlg:
            return False
        dir_var.set(os.path.normpath(dlg))
        return True

    def task_status():
        worker("检测状态", show_status)

    def task_fix():
        if messagebox.askokcancel("修复仪表盘", "需要先完全退出 SteamVR。\n修改前会自动备份配置。\n继续？"):
            worker("修复 SteamVR 仪表盘", lambda: fix_dashboard(force=False))

    def task_restore():
        if messagebox.askokcancel("还原", "用最近一次备份还原 SteamVR 配置？"):
            worker("还原 SteamVR 配置", restore_dashboard)

    def task_install():
        if not pick_dir_if_needed():
            return
        worker("安装 OVRAS", lambda: install_ovras(dir_var.get()))

    def task_localize():
        if not pick_dir_if_needed():
            return
        worker("汉化 OVRAS", lambda: (backup_english(dir_var.get()),
                                    localize(dir_var.get())))

    def task_revert():
        if not pick_dir_if_needed():
            return
        if messagebox.askokcancel("还原英文", "把界面还原成英文？"):
            worker("还原英文界面", lambda: revert_english(dir_var.get()))

    def task_bindfix():
        if messagebox.askokcancel(
                "修复绑定页",
                "给 SteamVR 的绑定页打空值保护补丁（修白屏）。\n"
                "修改前会自动备份，可随时还原。\n"
                "SteamVR 运行中也能执行，重开「绑定」页即生效。\n继续？"):
            worker("修复 SteamVR 绑定页", fix_binding_page)

    def task_bindrevert():
        if messagebox.askokcancel("还原绑定页", "用最近一次备份还原 SteamVR 绑定页文件？"):
            worker("还原 SteamVR 绑定页", revert_binding_page)

    def task_all():
        def seq():
            if not install_ovras(dir_var.get()):
                return
            backup_english(dir_var.get())
            localize(dir_var.get())
            if steamvr_running():
                log("[提示] SteamVR 正在运行，仪表盘修复已跳过。退出后点「修复仪表盘」即可。")
            else:
                fix_dashboard(force=False)
            fix_binding_page()

        if not pick_dir_if_needed():
            return
        if messagebox.askokcancel(
                "一键全部",
                "将依次执行：\n  1) 下载并安装 OVRAS\n  2) 汉化\n"
                "  3) 修复 SteamVR 仪表盘（SteamVR 需已退出）\n"
                "  4) 修复 SteamVR 绑定页白屏\n继续？"):
            worker("一键全部", seq)

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=10, pady=(8, 0))
    rows = [
        [("检测状态", task_status), ("修复仪表盘", task_fix), ("还原配置", task_restore),
         ("修复绑定页", task_bindfix), ("还原绑定页", task_bindrevert)],
        [("安装 OVRAS", task_install), ("一键汉化", task_localize),
         ("还原英文", task_revert), ("一键全部", task_all)],
    ]
    for row in rows:
        line = tk.Frame(bar)
        line.pack(fill="x", pady=2)
        for text, fn in row:
            ttk.Button(line, text=text, command=fn).pack(side="left", padx=3, ipadx=6, ipady=3)

    ui_log("提示：修复仪表盘前请先退出 SteamVR；安装 / 汉化 / 修复绑定页不需要退出。")
    ui_log("第一次使用请确认上面的「OVRAS 安装目录」是否正确，不确定就点「浏览…」选择。")
    ui_log("")
    root.mainloop()


# ---------------------------------------------------------------- 命令行

def main(argv=None):
    _attach_console()
    _init_console()
    parser = argparse.ArgumentParser(
        description="%s v%s —— SteamVR 仪表盘修复 + OVRAS 安装/汉化" % (APP_NAME, APP_VER))
    parser.add_argument("command", nargs="?", default="gui",
                        choices=["gui", "status", "fix", "restore", "install",
                                 "localize", "revert", "backup", "register",
                                 "bindfix", "bindrevert", "all"])
    parser.add_argument("--dir", help="OVRAS 安装目录")
    parser.add_argument("--archive", help="使用本地 7z 安装包（离线安装）")
    parser.add_argument("--force", action="store_true", help="SteamVR 运行中也强行修改")
    parser.add_argument("--dry-run", action="store_true", help="只预演，不写文件")
    parser.add_argument("--no-register", action="store_true", help="安装后不注册进 SteamVR")
    args = parser.parse_args(argv)

    if args.command == "gui":
        run_gui()
    elif args.command == "status":
        show_status()
    elif args.command == "fix":
        return 0 if fix_dashboard(force=args.force) else 1
    elif args.command == "restore":
        return 0 if restore_dashboard() else 1
    elif args.command == "bindfix":
        return 0 if fix_binding_page(dry_run=args.dry_run) else 1
    elif args.command == "bindrevert":
        return 0 if revert_binding_page() else 1
    elif args.command == "install":
        return 0 if install_ovras(args.dir, args.archive, not args.no_register) else 1
    elif args.command == "register":
        d = require_install_dir(args.dir)
        return 0 if register_manifest(d) else 1
    elif args.command == "localize":
        if args.dry_run:
            return 0 if localize(args.dir, dry_run=True) else 1
        backup_english(args.dir)
        return 0 if localize(args.dir) else 1
    elif args.command == "revert":
        return 0 if revert_english(args.dir) else 1
    elif args.command == "backup":
        return 0 if backup_english(args.dir) else 1
    elif args.command == "all":
        ok = install_ovras(args.dir, args.archive, not args.no_register)
        if ok:
            backup_english(args.dir)
            localize(args.dir)
            if steamvr_running() and not args.force:
                log("[提示] SteamVR 正在运行，仪表盘修复已跳过。")
            else:
                fix_dashboard(force=args.force)
            fix_binding_page()
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

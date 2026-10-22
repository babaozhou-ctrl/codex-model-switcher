# -*- coding: utf-8 -*-
"""
Codex 模型切换器 v2 · 安装脚本
============================================================
把源码目录里的文件复制到运行目录（%USERPROFILE%\\.codex\\profiles），
并在桌面创建一个指向 pythonw.exe 的快捷方式。

用法：
    python install.py                # 部署 + 建桌面快捷方式
    python install.py --no-shortcut  # 只部署，不建快捷方式
    python install.py --target D:\\x  # 部署到指定目录（调试用）

说明：
  * 不会覆盖已存在的 providers.json（那是用户的配置，升级时要保住）
  * 建 .lnk 不用 pywin32（本机没装），直接 ctypes 调 COM，
    思路来自 v1 的 make_shortcut.py，逻辑内联在这里
  * 不需要管理员权限
"""

import argparse
import ctypes
import glob
import os
import re
import shutil
import sys
from ctypes import (POINTER, WINFUNCTYPE, byref, c_int, c_void_p, c_wchar_p)

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DEFAULT_TARGET = os.path.join(HOME, ".codex", "profiles")
CODEX_CONFIG = os.path.join(HOME, ".codex", "config.toml")

# 需要部署到运行目录的文件（providers.json 不在此列，绝不覆盖）
PAYLOAD = ("main.pyw", "core.py", "presets.json", "README.md")
PRESERVE = ("providers.json",)

# 第一步尝试：直接 glob。注意这条在大多数机器上注定失败 ——
# WindowsApps 对普通用户不可枚举（listdir 报 PermissionError），
# 但里面的 exe 本身是可以访问的。所以 glob 只能算「万一能行」。
CODEX_ICON_GLOBS = (
    r"C:\Program Files\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe",
    r"C:\Program Files\WindowsApps\OpenAI.Codex_*\ChatGPT.exe",
)
CODEX_EXE_NAME = "ChatGPT.exe"
CODEX_PACKAGE_PREFIX = "OpenAI.Codex_"
PACKAGES_KEY = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
                r"\CurrentVersion\AppModel\Repository\Packages")

# 从 config.toml 里捞绝对路径。两种引号都要覆盖：
# TOML 里单引号是字面量，双引号里 \\ 需要还原成 \。
CODEX_IN_CONFIG_RE = re.compile(
    r"""command\s*=\s*(?P<quote>['"])(?P<path>[^'"]*?CHATGPT_EXE)(?P=quote)"""
    .replace("CHATGPT_EXE", re.escape(CODEX_EXE_NAME)))

SHORTCUT_NAME = "Codex 模型切换.lnk"


# ---------------------------------------------------------------- COM 建快捷方式
ole32 = ctypes.WinDLL("ole32")


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def make_guid(text):
    """CLSIDFromString 必须传带花括号的字符串，否则调用失败。"""
    guid = GUID()
    value = text if text.startswith("{") else "{" + text + "}"
    result = ole32.CLSIDFromString(c_wchar_p(value), byref(guid))
    if result < 0:
        raise RuntimeError("CLSIDFromString 失败：%s（hr=0x%08X）"
                           % (value, result & 0xFFFFFFFF))
    return guid


CLSID_SHELL_LINK = make_guid("00021401-0000-0000-C000-000000000046")
IID_ISHELL_LINK_W = make_guid("000214F9-0000-0000-C000-000000000046")
IID_IPERSIST_FILE = make_guid("0000010b-0000-0000-C000-000000000046")

HRESULT = ctypes.c_long
CLSCTX_INPROC_SERVER = 1


def vfunc(pointer, index, restype, *argtypes):
    """按 vtable 索引取虚函数并包装成可调用对象。"""
    table = ctypes.cast(pointer, POINTER(POINTER(c_void_p)))[0]
    return WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])


def create_shortcut(path, target, arguments, workdir, icon, description):
    """用 IShellLinkW + IPersistFile 写一个 .lnk 文件。"""
    ole32.CoInitialize(None)
    shell_link = c_void_p()
    result = ole32.CoCreateInstance(byref(CLSID_SHELL_LINK), None,
                                    CLSCTX_INPROC_SERVER,
                                    byref(IID_ISHELL_LINK_W), byref(shell_link))
    if result < 0:
        raise RuntimeError("CoCreateInstance 失败（hr=0x%08X）" % (result & 0xFFFFFFFF))
    try:
        vfunc(shell_link, 20, HRESULT, c_wchar_p)(shell_link, target)       # SetPath
        vfunc(shell_link, 11, HRESULT, c_wchar_p)(shell_link, arguments)    # SetArguments
        vfunc(shell_link, 9, HRESULT, c_wchar_p)(shell_link, workdir)       # SetWorkingDirectory
        vfunc(shell_link, 7, HRESULT, c_wchar_p)(shell_link, description)   # SetDescription
        if icon:
            vfunc(shell_link, 17, HRESULT, c_wchar_p, c_int)(shell_link, icon, 0)
        vfunc(shell_link, 15, HRESULT, c_int)(shell_link, 1)                # SetShowCmd

        persist = c_void_p()
        result = vfunc(shell_link, 0, HRESULT, POINTER(GUID),
                       POINTER(c_void_p))(shell_link, byref(IID_IPERSIST_FILE),
                                          byref(persist))
        if result < 0:
            raise RuntimeError("QueryInterface(IPersistFile) 失败（hr=0x%08X）"
                               % (result & 0xFFFFFFFF))
        try:
            result = vfunc(persist, 6, HRESULT, c_wchar_p,
                           c_int)(persist, path, 1)                          # Save
            if result < 0:
                raise RuntimeError("IPersistFile::Save 失败（hr=0x%08X）"
                                   % (result & 0xFFFFFFFF))
        finally:
            vfunc(persist, 2, ctypes.c_ulong)(persist)                       # Release
    finally:
        vfunc(shell_link, 2, ctypes.c_ulong)(shell_link)


# ---------------------------------------------------------------- 环境探测
def find_pythonw():
    """找到与当前解释器同目录的 pythonw.exe；没有就退回 python.exe。

    不用硬编码 Python38 路径，换机器也能跑。
    """
    executable = sys.executable or ""
    folder = os.path.dirname(executable)
    if folder:
        candidate = os.path.join(folder, "pythonw.exe")
        if os.path.isfile(candidate):
            return candidate
    return executable


def _winreg_module():
    """延迟导入 winreg：非 Windows 上执行 --help 时不该炸。"""
    try:
        import winreg
        return winreg
    except Exception:
        return None


def _version_key(package_name):
    """从包名里取版本号转成可比较的元组，用于「装了多个版本时选最新」。

    OpenAI.Codex_26.1016.6573.0_x64__2p2nqsd0c76g0 → ([26, 1016, 6573, 0], name)
    """
    tail = package_name[len(CODEX_PACKAGE_PREFIX):]
    version = tail.split("_", 1)[0]
    parts = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return (parts, package_name)


def _icon_from_glob():
    """第一步：直接 glob。某些机器（或管理员会话）能枚举 WindowsApps。"""
    for pattern in CODEX_ICON_GLOBS:
        try:
            matches = glob.glob(pattern)
        except Exception:
            matches = []
        if matches:
            matches.sort(key=len)
            return matches[-1]
    return ""


def _icon_from_config():
    """第二步：从 ~/.codex/config.toml 里捞已有绝对路径。

    MCP 段经常直接写着 Codex 的 exe 绝对路径，这是最可靠的非枚举来源：
    只要用户跑过 Codex，这个文件里基本都有。逐个 isfile 校验。
    """
    try:
        with open(CODEX_CONFIG, "r", encoding="utf-8") as handle:
            text = handle.read()
    except Exception:
        return ""

    for match in CODEX_IN_CONFIG_RE.finditer(text):
        candidate = match.group("path").strip()
        if match.group("quote") == '"':
            # TOML 基本字符串里的 \\ 是转义，还原成单个反斜杠
            candidate = candidate.replace("\\\\", "\\")
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _package_root(winreg, package_name):
    """读某个包注册表项的安装根目录。键名在不同 Windows 版本上不止一种。"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             PACKAGES_KEY + "\\" + package_name)
    except Exception:
        return ""
    try:
        for value_name in ("PackageRootFolder", "InstallLocation", ""):
            try:
                value, _kind = winreg.QueryValueEx(key, value_name)
            except Exception:
                continue
            if isinstance(value, str) and value:
                return value
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
    return ""


def _icon_from_registry():
    """第三步：查注册表 AppModel Repository 拿 PackageRootFolder。

    这条不依赖 config.toml，是「用户刚装好 Codex、还没跑过任何 MCP」时的兜底。
    """
    winreg = _winreg_module()
    if winreg is None:
        return ""

    names = []
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PACKAGES_KEY)
    except Exception:
        return ""
    try:
        count = winreg.QueryInfoKey(key)[0]
        for index in range(count):
            try:
                name = winreg.EnumKey(key, index)
            except OSError:
                continue
            if name.startswith(CODEX_PACKAGE_PREFIX):
                names.append(name)
    except Exception:
        return ""
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass

    # 升级后可能同时存在新旧两个包，优先用版本号最大的
    for package in sorted(names, key=_version_key, reverse=True):
        root = _package_root(winreg, package)
        if not root:
            continue
        for relative in (os.path.join(root, "app", CODEX_EXE_NAME),
                         os.path.join(root, CODEX_EXE_NAME)):
            if os.path.isfile(relative):
                return relative
    return ""


def find_codex_icon():
    """定位 Codex 的 ChatGPT.exe，用作桌面快捷方式的图标。

    为什么不能只靠 glob：`C:\\Program Files\\WindowsApps` 对普通用户是
    「不可枚举但文件可访问」的 —— `os.listdir` 抛 PermissionError，
    `glob.glob` 把异常吞掉后返回空列表。所以枚举式查找在**每台机器**上
    都会失败，必须从别处拿到「确切路径」再 os.path.isfile 校验。

    三级兜底，从最可靠到最兜底：
      1. glob                     —— 万一能枚举
      2. config.toml 里已有的路径   —— 跑过 Codex 就基本都有
      3. 注册表 PackageRootFolder  —— 装完还没跑过 MCP 时的兜底

    全失败返回 ""，调用方会退回系统默认图标（功能不受影响，只是图标难看）。
    """
    for finder in (_icon_from_glob, _icon_from_config, _icon_from_registry):
        try:
            found = finder()
        except Exception:
            found = ""
        if found and os.path.isfile(found):
            return found
    return ""


def find_desktop():
    """桌面目录可能被 OneDrive 重定向，优先问系统。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        try:
            value, _kind = winreg.QueryValueEx(key, "Desktop")
        finally:
            winreg.CloseKey(key)
        if value and os.path.isdir(value):
            return value
    except Exception:
        pass
    fallback = os.path.join(HOME, "Desktop")
    return fallback if os.path.isdir(fallback) else HOME


# ---------------------------------------------------------------- 主流程
def deploy(source_dir, target_dir):
    """复制 payload 到 target_dir，返回 (已复制列表, 跳过列表)。"""
    os.makedirs(target_dir, exist_ok=True)
    copied = []
    skipped = []

    for name in PAYLOAD:
        source = os.path.join(source_dir, name)
        if not os.path.isfile(source):
            skipped.append("%s（源文件不存在）" % name)
            continue
        destination = os.path.join(target_dir, name)
        # 在运行目录里原地跑 install.py 时源和目标是同一个文件，
        # shutil.copy2 会抛 SameFileError，这里直接跳过。
        if os.path.abspath(source) == os.path.abspath(destination):
            skipped.append("%s（已是最新，原地运行）" % name)
            continue
        shutil.copy2(source, destination)
        copied.append(name)

    for name in PRESERVE:
        destination = os.path.join(target_dir, name)
        if os.path.isfile(destination):
            skipped.append("%s（已存在，保留用户配置）" % name)

    return copied, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="部署 Codex 模型切换器并在桌面创建快捷方式")
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help="部署目标目录（默认 %%USERPROFILE%%\\.codex\\profiles）")
    parser.add_argument("--no-shortcut", action="store_true",
                        help="只部署文件，不创建桌面快捷方式")
    parser.add_argument("--shortcut-only", action="store_true",
                        help="只重建桌面快捷方式，不复制文件")
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("[X] 本脚本只支持 Windows（依赖 COM 与 explorer.exe）")
        return 1

    print("=" * 62)
    print("Codex 模型切换器 v2 · 安装")
    print("=" * 62)
    print("源码目录：%s" % SOURCE_DIR)
    print("运行目录：%s" % args.target)

    if not args.shortcut_only:
        copied, skipped = deploy(SOURCE_DIR, args.target)
        print("\n[1/2] 部署文件")
        for name in copied:
            print("      已复制  %s" % name)
        for name in skipped:
            print("      已跳过  %s" % name)
        if not copied:
            print("      提示：没有文件需要复制（可能已在运行目录里原地运行）")

    if args.no_shortcut:
        print("\n[2/2] 已按参数要求跳过快捷方式")
        print("\n完成。运行方式：pythonw \"%s\""
              % os.path.join(args.target, "main.pyw"))
        return 0

    print("\n[2/2] 创建桌面快捷方式")
    pythonw = find_pythonw()
    script = os.path.join(args.target, "main.pyw")
    if not os.path.isfile(script):
        print("[X] 找不到 %s，请先执行部署" % script)
        return 1
    icon = find_codex_icon()
    desktop = find_desktop()
    shortcut = os.path.join(desktop, SHORTCUT_NAME)

    print("      解释器  %s" % pythonw)
    print("      图标    %s" % (icon or "（未找到 Codex，使用默认图标）"))

    try:
        create_shortcut(
            path=shortcut,
            target=pythonw,
            arguments='"%s"' % script,
            workdir=args.target,
            icon=icon,
            description="在多个模型厂商之间切换 Codex 配置",
        )
    except Exception as exc:
        print("[X] 创建快捷方式失败：%s" % exc)
        print("    可以手动建一个快捷方式：目标 %s，参数 \"%s\"" % (pythonw, script))
        return 1

    print("      已创建  %s" % shortcut)
    print("\n完成。双击桌面的「Codex 模型切换」即可使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

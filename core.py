# -*- coding: utf-8 -*-
"""
Codex 模型切换器 · 核心逻辑
============================================================
本模块不含任何 UI 依赖，只负责：

  * providers.json 的读写 / 默认值填充 / 首次播种
  * config.toml 的「增量改写」与静态校验（绝不整文件覆盖模板）
  * models.<id>.json 的生成（Codex 靠它认识模型）
  * HTTP 协议探测（判断厂商是否支持 /responses）
  * 网络体检
  * Codex 进程控制

设计原则：

  1. 任何函数都不写死绝对路径，路径一律由调用方传入，或从 ~ 推导
  2. 所有面向用户的文本里 api_key 一律掩码，异常信息也要过滤
  3. 只用 Python 3.8 标准库（本机 pythonw.exe 是 3.8.10）
  4. 纯函数优先，副作用（读写文件 / 发请求 / 杀进程）集中在少数入口

Python 3.8 兼容性注意：不使用 dict | dict、list[str] 之类的新语法。
"""

import copy
import datetime
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 路径常量
HOME = os.path.expanduser("~")
CODEX_HOME = os.path.join(HOME, ".codex")
CONFIG_PATH = os.path.join(CODEX_HOME, "config.toml")
AUTH_JSON = os.path.join(CODEX_HOME, "auth.json")
MODELS_JSON = os.path.join(CODEX_HOME, "models.json")
BACKUP_DIR = os.path.join(CODEX_HOME, "profiles", "backup")

# Codex 桌面版的 AppUserModelId，用 explorer.exe shell:AppsFolder 拉起
APP_ID = r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"
NO_WIN = 0x08000000 if os.name == "nt" else 0

# 新增厂商时按序取色，用完循环
PALETTE = [
    "#4d6bfe", "#10a37f", "#ffb454", "#ff6b8a", "#a78bfa",
    "#2dd4bf", "#f97316", "#60a5fa", "#f472b6", "#34d399",
]

# 切换时会被清掉的顶层键（先删后按 kind 重新写，避免重复键）
DROP_TOP_KEYS = (
    "model",
    "model_provider",
    "preferred_auth_method",
    "forced_login_method",
    "model_catalog_json",
    "web_search",
)

PROVIDERS_VERSION = 2
DEFAULT_WIRE_API = "responses"
# Codex 0.163 只认 responses；wire_api = "chat" 已被官方移除，写进去 Codex 会拒绝加载
SUPPORTED_WIRE_API = "responses"

# provider id 会原样拼进 [model_providers.<id>]，必须是合法 TOML 裸键。
# 带空格 → TOMLDecodeError；带点号 → 变成嵌套表，model_provider 找不到；
# 带斜杠 → 可能被当成路径。一律只放行字母数字下划线连字符。
PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 备份保留份数上限。每份备份都含明文 token，不能无限堆积。
BACKUP_KEEP = 30


# ---------------------------------------------------------------- 异常类型
class ConfigValidationError(Exception):
    """config.toml 改写后自检不通过。"""


class NetError(Exception):
    """网络层失败（区别于 HTTP 状态码错误）。"""


# ---------------------------------------------------------------- 基础工具
def ensure_trailing_slash(url):
    """Base URL 统一补尾斜杠。Codex 拼接时会直接相加，少了斜杠会 404。"""
    text = (url or "").strip()
    if not text:
        return ""
    return text if text.endswith("/") else text + "/"


def to_forward_slashes(path):
    """Windows 反斜杠在 TOML 基本字符串里要转义，统一成正斜杠最省事。"""
    return (path or "").replace("\\", "/")


def mask_key(key):
    """把 api_key 掩码成 sk-****c3b 形式，界面与日志只允许出现这个。"""
    text = (key or "").strip()
    if not text:
        return "(未填写)"
    if len(text) <= 8:
        return "*" * len(text)
    return text[:3] + "****" + text[-3:]


def redact(text, secret):
    """把文本里出现的密钥替换成掩码。任何要写日志/异常的地方都先过一遍。"""
    out = "" if text is None else str(text)
    token = (secret or "").strip()
    if token and len(token) >= 4 and token in out:
        out = out.replace(token, mask_key(token))
    return out


def _toml_str(value):
    """把 Python 字符串序列化成 TOML 基本字符串。

    json.dumps 产生的转义序列（\\" \\\\ \\n \\t \\uXXXX ...）恰好是 TOML
    基本字符串的合法子集，因此可以直接复用，比自己写转义靠谱。
    """
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def _bracket_delta(line):
    """返回单行里 [] {} 的净增深度，字符串内与 # 注释后的括号不计入。"""
    depth = 0
    quote = None
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = None
        elif quote == "'":
            if char == "'":
                quote = None
        else:
            if char == "#":
                break
            if char in ('"', "'"):
                quote = char
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
        index += 1
    return depth


def _bracket_balance(text):
    """整篇文本的括号净深度与是否有未闭合引号，用于写后自检。"""
    depth = 0
    quote = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = None
        elif quote == "'":
            if char == "'":
                quote = None
        else:
            if char == "#":
                newline = text.find("\n", index)
                if newline < 0:
                    break
                index = newline
                continue
            if char in ('"', "'"):
                quote = char
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
        index += 1
    return depth, quote


def atomic_write_text(path, text):
    """先写 .tmp 再 os.replace，避免写一半断电留下半截配置。"""
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def read_text(path):
    """读 UTF-8 文本；文件不存在返回空串。"""
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------- config 解析
def split_config(text):
    """把 config.toml 切成 (顶层键区, section 区)。

    section 从第一个「行首 [」开始。用正则 (?m)^\\[ 是为了避开
    `notify = [ ... ]` 这种数组里的 [（它不在行首）。
    """
    source = text or ""
    match = re.search(r"(?m)^\[", source)
    if not match:
        return source, ""
    return source[:match.start()], source[match.start():]


def _parse_top_keys(head):
    """解析顶层键区，返回 {key: 原始值文本}。跨行的值（数组）会被跳过。"""
    result = {}
    lines = head.splitlines()
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        stripped = line.strip()
        index += 1
        if not stripped or stripped.startswith("#") or stripped.startswith("["):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        depth = _bracket_delta(line)
        while depth > 0 and index < total:
            depth += _bracket_delta(lines[index])
            index += 1
        result[key.strip()] = value.strip()
    return result


def _strip_top_level_keys(head, keys):
    """删掉指定的顶层键，其余内容（注释、空行、别的键）原样保留。"""
    wanted = set(keys)
    out = []
    lines = head.splitlines()
    index = 0
    total = len(lines)
    depth = 0        # 当前正处在一个被保留的跨行值内部
    skip_depth = 0   # 当前正处在一个被删掉的跨行值内部
    while index < total:
        line = lines[index]
        stripped = line.strip()
        if skip_depth > 0:
            skip_depth += _bracket_delta(line)
            index += 1
            continue
        if depth > 0:
            depth += _bracket_delta(line)
            out.append(line)
            index += 1
            continue
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            delta = _bracket_delta(line)
            index += 1
            if key in wanted:
                skip_depth = delta if delta > 0 else 0
                continue
            if delta > 0:
                depth = delta
            out.append(line)
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


def _remove_section(sections, header):
    """整段删除一个 section（从 header 行到下一个行首 [ 之前）。"""
    if not sections:
        return ""
    pattern = re.compile(r"(?m)^" + re.escape(header) + r"[ \t]*$")
    match = pattern.search(sections)
    if not match:
        return sections
    start = match.start()
    following = re.search(r"(?m)^\[", sections[match.end():])
    end = len(sections) if not following else match.end() + following.start()
    return sections[:start] + sections[end:]


def count_section(sections, header):
    """统计某个 section header 出现的次数（用于自检）。"""
    if not sections:
        return 0
    pattern = re.compile(r"(?m)^" + re.escape(header) + r"[ \t]*$")
    return len(pattern.findall(sections))


def _render_provider_section(provider):
    """渲染 [model_providers.<id>] 段。"""
    pid = provider.get("id") or "custom"
    base_url = ensure_trailing_slash(provider.get("base_url") or "")
    wire_api = provider.get("wire_api") or DEFAULT_WIRE_API
    lines = [
        "[model_providers.%s]" % pid,
        "name = %s" % _toml_str(pid),
        "base_url = %s" % _toml_str(base_url),
        "wire_api = %s" % _toml_str(wire_api),
        "experimental_bearer_token = %s" % _toml_str(provider.get("api_key") or ""),
    ]
    return "\n".join(lines)


def build_config(base_text, provider, profiles_dir):
    """以现有 config.toml 为基底，增量改写成目标厂商的配置。

    关键点：绝不整文件覆盖模板。只动 6 个顶层键 + 一个 provider 段，
    这样用户后来加的 MCP、桌面设置、字体等等永远不会丢。
    """
    kind = provider.get("kind") or "custom"
    head, sections = split_config(base_text)

    body = _strip_top_level_keys(head, DROP_TOP_KEYS)
    body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")

    key_lines = ["model = %s" % _toml_str(provider.get("model") or "")]
    if kind != "official":
        pid = provider.get("id") or "custom"
        catalog_path = to_forward_slashes(
            os.path.join(profiles_dir, "models.%s.json" % pid))
        key_lines.append("model_provider = %s" % _toml_str(pid))
        # 不再写 preferred_auth_method / forced_login_method。
        # 这两个键是旧版 Codex 用来「强制走 API key 模式」的，新版本
        # （实测 26.1016.8120.0 / codex-cli 0.163.0-alpha.11.1）改了判定方式：
        # 看到 config 写着 apikey、而 auth.json 还是 ChatGPT 登录态时，
        # 会先执行一次登出再继续，整个启动流程就卡在策略读取超时上，
        # 表现为进程起来但一个窗口都不出。去掉之后 `codex exec` 能正常返回。
        key_lines.append('web_search = "disabled"')
        key_lines.append("model_catalog_json = %s" % _toml_str(catalog_path))

    blocks = []
    if body:
        blocks.append(body)
    blocks.append("\n".join(key_lines))
    new_head = "\n\n".join(blocks)

    tail = sections or ""
    if kind != "official":
        header = "[model_providers.%s]" % (provider.get("id") or "custom")
        tail = _remove_section(tail, header).strip("\n")
        section = _render_provider_section(provider)
        tail = tail + "\n\n" + section if tail else section

    if tail:
        return new_head + "\n\n" + tail + "\n"
    return new_head + "\n"


def validate_config(text, provider):
    """改写后的静态自检。失败抛 ConfigValidationError（调用方负责回滚）。

    真正的 TOML 解析用不了（Python 3.8 没有 tomllib），所以做这几项检查：
      1. 关键顶层键存在且值合理
      2. provider id 是合法裸键（否则生成出来的 TOML 直接解析失败，
         或者更糟：`a.b` 这种「合法但会变成嵌套表」的 id 静默失效）
      3. base_url 是 http(s) 地址
      4. wire_api 必须是 responses（Codex 0.163 已移除 chat）
      5. `[model_providers.<id>]` 只出现一次
      6. 括号配平、引号闭合

    这些校验必须拦住「文件能写出去但 Codex 读不了」的所有已知路径 ——
    因为写坏配置的代价是用户下次打开 Codex 直接报错。
    """
    problems = []
    head, sections = split_config(text)
    keys = _parse_top_keys(head)
    kind = provider.get("kind") or "custom"

    if "model" not in keys:
        problems.append("缺少顶层键 model")
    elif not keys["model"].strip("\"'"):
        problems.append("顶层键 model 为空")

    if kind != "official":
        pid = provider.get("id") or "custom"
        # ---- id 必须是 TOML 裸键。带空格会 SyntaxError；带点号会变成嵌套表
        #      （[model_providers.a.b]），model_provider = "a.b" 就找不到段；
        #      带斜杠则可能被当成路径，都是必须拦下的。
        if not PROVIDER_ID_RE.match(pid):
            problems.append("provider id 不合法：%r（只允许字母数字下划线连字符）"
                            % pid)

        for key in ("model_provider", "web_search", "model_catalog_json"):
            if key not in keys:
                problems.append("缺少顶层键 %s" % key)

        # 这两个键必须不出现：新版 Codex（26.1016.8120.0 起）见到
        # 「config 写 apikey、auth.json 仍是 ChatGPT 登录态」会先登出再继续，
        # 结果卡在 startup policy 读取超时——进程起来了但一个窗口都不显示。
        for key in ("preferred_auth_method", "forced_login_method"):
            if key in keys:
                problems.append(
                    "不应出现顶层键 %s（新版 Codex 会因此在启动时卡死，"
                    "请删掉这一行）" % key)

        provider_value = keys.get("model_provider", "").strip("\"'")
        if provider_value != pid:
            problems.append("model_provider = %s，期望 %s" % (provider_value, pid))

        header = "[model_providers.%s]" % pid
        found = count_section(sections, header)
        if found != 1:
            problems.append("%s 出现 %d 次（应为 1 次）" % (header, found))

        if "experimental_bearer_token" not in sections:
            problems.append("provider 段缺少 experimental_bearer_token")

        # ---- base_url：空串或 ftp:// 都能写出去，但 Codex 一定发不出请求
        base_url = provider.get("base_url") or ""
        if not base_url:
            problems.append("base_url 为空")
        elif not re.match(r"https?://", base_url, re.I):
            problems.append("base_url 必须以 http:// 或 https:// 开头：%s"
                            % base_url)

        # ---- wire_api：0.163 只认 responses，写 chat 会导致 Codex 拒绝加载
        wire_api = provider.get("wire_api") or DEFAULT_WIRE_API
        if wire_api != SUPPORTED_WIRE_API:
            problems.append('wire_api 必须是 "%s"（当前 %r）'
                            % (SUPPORTED_WIRE_API, wire_api))
    else:
        for key in ("model_provider", "model_catalog_json"):
            if key in keys:
                problems.append("官方模式不应出现顶层键 %s" % key)

    depth, quote = _bracket_balance(text)
    if depth != 0:
        problems.append("括号不配平（净深度 %d）" % depth)
    if quote:
        problems.append("存在未闭合的 %s 引号" % quote)

    if problems:
        raise ConfigValidationError("；".join(problems))


def detect_active_id(providers, config_text):
    """从当前 config.toml 反推正在使用的 provider id。"""
    head, _ = split_config(config_text or "")
    keys = _parse_top_keys(head)
    raw = keys.get("model_provider", "").strip().strip("\"'")
    if raw:
        return raw
    for item in providers or []:
        if (item.get("kind") or "custom") == "official":
            return item.get("id") or "openai"
    if providers:
        return providers[0].get("id") or ""
    return ""


def extract_provider_sections(config_text):
    """从 config.toml 里捞出已有的 [model_providers.*] 段。

    用于首次播种 providers.json 时继承用户已经填好的 key，
    免得升级到 v2 后还要重新粘贴一遍。
    """
    head, sections = split_config(config_text or "")
    result = {}
    if not sections:
        return result
    pattern = re.compile(r"(?m)^\[model_providers\.([A-Za-z0-9_\-]+)\][ \t]*$")
    matches = list(pattern.finditer(sections))
    for position, match in enumerate(matches):
        pid = match.group(1)
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) \
            else len(sections)
        chunk = sections[start:end]
        info = {}
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            info[key.strip()] = value.strip().strip("\"'")
        info["_header"] = "[model_providers.%s]" % pid
        key_value = info.get("experimental_bearer_token") or ""
        result[pid] = {
            "base_url": info.get("base_url") or "",
            "wire_api": info.get("wire_api") or DEFAULT_WIRE_API,
            "api_key": key_value,
        }
    return result


# ---------------------------------------------------------------- providers.json
def pick_color(index):
    """按序取调色板颜色。"""
    if not PALETTE:
        return "#4d6bfe"
    return PALETTE[index % len(PALETTE)]


def _slugify(text):
    """小写 + 非字母数字折叠成连字符，并清掉首尾/重复的连字符。"""
    raw = (text or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def make_provider_id(name, existing_ids, hint=""):
    """由名称生成合法 id（小写字母数字连字符），冲突时加 -2 / -3 后缀。

    id 会直接变成 [model_providers.<id>] 的裸键名，因此只允许
    A-Za-z0-9_ 与连字符（TOML 裸键允许这些字符）。

    纯中文名称 slug 化后会变成空串，这时退而用 hint（一般是 Base URL）
    里的域名做 id，例如 "阿里云百炼" + "https://dashscope.aliyuncs.com/..."
    → "dashscope-aliyuncs-com"。两条路都空才用 "provider"。
    """
    taken = set()
    for item in existing_ids or []:
        if item:
            taken.add(str(item).lower())

    base = _slugify(name)
    if not base and hint:
        base = _slugify(host_of(hint))
    if not base:
        base = "provider"
    if not base[0].isalpha():
        base = "p-" + base

    candidate = base
    counter = 2
    while candidate in taken:
        candidate = "%s-%d" % (base, counter)
        counter += 1
    return candidate


def normalize_provider(raw, index):
    """把外部读进来的 provider 补全成结构稳定的 dict。

    id 是唯一会被拼进 TOML 段的字段，所以非空但**不合法**的 id 也要净化：
    直接 strip 后放行会让 `"a b"` / `"a.b"` 这种 id 一路走到写盘才炸。
    """
    item = dict(raw or {})
    kind = item.get("kind") or "custom"
    if kind not in ("official", "custom"):
        kind = "custom"
    item["kind"] = kind

    raw_id = str(item.get("id") or "").strip()
    if not raw_id or not PROVIDER_ID_RE.match(raw_id):
        raw_id = make_provider_id(item.get("name") or "",
                                  [], item.get("base_url") or "")
    item["id"] = raw_id

    item["name"] = item.get("name") or item["id"]
    item["desc"] = item.get("desc") or ""
    item["model"] = item.get("model") or ""
    item["color"] = item.get("color") or pick_color(index)
    item["wire_api"] = item.get("wire_api") or DEFAULT_WIRE_API
    # base_url / api_key 无论什么 kind 都保留：官方模式虽然用不到，
    # 但把厂商在 official / custom 之间来回切换时不该把用户填过的值弄丢。
    if item.get("base_url"):
        item["base_url"] = ensure_trailing_slash(item.get("base_url"))
    else:
        item["base_url"] = ""
    item["api_key"] = item.get("api_key") or ""
    probe = item.get("last_probe")
    if not isinstance(probe, dict):
        item["last_probe"] = None
    return item


def normalize_providers(data):
    """把 providers.json 的整个结构补全。"""
    source = dict(data or {})
    raw_list = source.get("providers")
    if not isinstance(raw_list, list):
        raw_list = []
    providers = []
    seen = set()
    for index, raw in enumerate(raw_list):
        item = normalize_provider(raw, index)
        if item["id"] in seen:
            item["id"] = make_provider_id(item["id"], seen)
        seen.add(item["id"])
        providers.append(item)
    active = str(source.get("active") or "")
    if providers and active not in seen:
        active = providers[0]["id"]
    return {
        "version": PROVIDERS_VERSION,
        "active": active,
        "providers": providers,
    }


def load_providers(path):
    """读 providers.json；不存在或损坏时返回空结构（不抛异常）。"""
    if not os.path.isfile(path):
        return {"version": PROVIDERS_VERSION, "active": "", "providers": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {"version": PROVIDERS_VERSION, "active": "", "providers": []}
    return normalize_providers(data)


def save_providers(path, data):
    """原子保存 providers.json。"""
    payload = normalize_providers(data)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def find_provider(data, provider_id):
    """按 id 找 provider，找不到返回 None。"""
    for item in (data or {}).get("providers") or []:
        if item.get("id") == provider_id:
            return item
    return None


def upsert_provider(data, provider):
    """新增或替换一个 provider，返回 (新数据, 是否新增)。"""
    result = normalize_providers(data)
    target = normalize_provider(provider, len(result["providers"]))
    replaced = False
    for index, item in enumerate(result["providers"]):
        if item.get("id") == target["id"]:
            result["providers"][index] = target
            replaced = True
            break
    if not replaced:
        result["providers"].append(target)
    if not result.get("active"):
        result["active"] = target["id"]
    return result, not replaced


def remove_provider(data, provider_id):
    """删除一个 provider。官方 GPT 是兜底项，不允许删。"""
    result = normalize_providers(data)
    kept = []
    removed = None
    for item in result["providers"]:
        if item.get("id") == provider_id:
            if (item.get("kind") or "custom") == "official":
                raise ValueError("官方 GPT 是兜底配置，不能删除")
            removed = item
            continue
        kept.append(item)
    if removed is None:
        raise ValueError("找不到要删除的厂商：%s" % provider_id)
    result["providers"] = kept
    if result.get("active") == provider_id:
        official = None
        for item in kept:
            if (item.get("kind") or "custom") == "official":
                official = item
                break
        result["active"] = official["id"] if official else (kept[0]["id"] if kept else "")
    return result


def seed_providers(presets, config_text):
    """首次运行时播种 providers.json。

    先放入官方 GPT + DeepSeek 两个基础项（deepseek 的 base_url / key
    能从现有 config.toml 的 [model_providers.deepseek] 段继承）；
    再把 config 里其他 [model_providers.*] 段也一并认领进来，
    这样老用户升级后不会发现「我配过的厂商不见了」。
    """
    inherited = extract_provider_sections(config_text)
    existing_ids = []
    seeded = []
    wanted = ("openai", "deepseek")
    for preset in presets or []:
        pid = preset.get("id") or ""
        if pid not in wanted:
            continue
        item = {
            "id": pid,
            "name": preset.get("name") or pid,
            "kind": preset.get("kind") or "custom",
            "desc": preset.get("desc") or "",
            "model": preset.get("model") or "",
            "color": preset.get("color") or pick_color(len(seeded)),
            "wire_api": preset.get("wire_api") or DEFAULT_WIRE_API,
        }
        if item["kind"] == "custom":
            carried = inherited.get(pid) or {}
            item["base_url"] = ensure_trailing_slash(
                carried.get("base_url") or preset.get("base_url") or "")
            item["api_key"] = carried.get("api_key") or ""
        existing_ids.append(pid)
        seeded.append(item)

    # 把 config.toml 里已有、但不在上面这组基础清单里的 provider 段也认领进来。
    # 否则从旧版升级、或手工配过别家的用户，首次打开会发现「我配的厂商不见了」。
    preset_by_id = {}
    for preset in presets or []:
        preset_by_id[preset.get("id") or ""] = preset
    top_head, _ = split_config(config_text or "")
    top_keys = _parse_top_keys(top_head)
    top_model = top_keys.get("model", "").strip("\"'")
    top_provider = top_keys.get("model_provider", "").strip("\"'")

    for pid, carried in (inherited or {}).items():
        if pid in existing_ids:
            continue
        if not carried.get("base_url"):
            continue                      # 没有 base_url 的段没法用，跳过
        preset = preset_by_id.get(pid) or {}
        model = preset.get("model") or (top_model if top_provider == pid else "")
        item = {
            "id": pid,
            "name": carried.get("name") or preset.get("name") or pid,
            "kind": "custom",
            "desc": preset.get("desc") or carried.get("base_url") or "",
            "model": model,
            "color": preset.get("color") or pick_color(len(seeded)),
            "wire_api": carried.get("wire_api") or preset.get("wire_api") or DEFAULT_WIRE_API,
            "base_url": ensure_trailing_slash(carried.get("base_url") or ""),
            "api_key": carried.get("api_key") or "",
        }
        existing_ids.append(pid)
        seeded.append(item)

    if not seeded:
        # presets 文件缺失时的最低兜底，保证工具还能用
        seeded = [
            {
                "id": "openai",
                "name": "官方 GPT",
                "kind": "official",
                "desc": "ChatGPT 账号登录，用 auth.json 里的凭据",
                "model": "gpt-6-luna",
                "color": "#10a37f",
                "wire_api": DEFAULT_WIRE_API,
            },
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "kind": "custom",
                "desc": "api.deepseek.com",
                "base_url": "https://api.deepseek.com/",
                "api_key": "",
                "model": "deepseek-flash",
                "color": "#4d6bfe",
                "wire_api": DEFAULT_WIRE_API,
            },
        ]

    active = detect_active_id(seeded, config_text)
    if not active:
        active = seeded[0]["id"]
    return normalize_providers({
        "version": PROVIDERS_VERSION,
        "active": active,
        "providers": seeded,
    })


def _providers_json_is_broken(path):
    """判断 providers.json 是「JSON 真的坏了」还是「只是里面没有厂商」。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return True
    return not isinstance(raw, dict)


def load_or_seed_providers(providers_path, config_path, presets):
    """有就加载，没有就播种并落盘。

    JSON 损坏时**不能静默重置**：用户可能手工配了 5 家厂商，一旦无声重置成
    默认 2 家，数据和 Key 就全没了。所以先把坏文件改名留档，再播种，并把
    备份路径挂在返回值的 `recovered_from` 上，由调用方弹窗告知用户。
    """
    recovered_from = ""
    if os.path.isfile(providers_path):
        data = load_providers(providers_path)
        if data.get("providers"):
            return data
        if _providers_json_is_broken(providers_path):
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            corrupt_path = "%s.corrupt-%s" % (providers_path, stamp)
            try:
                shutil.move(providers_path, corrupt_path)
                recovered_from = corrupt_path
            except Exception:
                recovered_from = ""

    data = seed_providers(presets, read_text(config_path))
    try:
        save_providers(providers_path, data)
    except Exception:
        pass
    if recovered_from:
        data["recovered_from"] = recovered_from
    return data


def load_presets(path):
    """读 presets.json，返回 presets 列表；读不到就返回空列表。"""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    if isinstance(data, dict):
        items = data.get("presets")
    elif isinstance(data, list):
        items = data
    else:
        items = None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def next_color(data):
    """为新增厂商挑一个还没被用过的颜色，用完循环。"""
    used = []
    for item in (data or {}).get("providers") or []:
        color = (item.get("color") or "").lower()
        if color:
            used.append(color)
    for color in PALETTE:
        if color.lower() not in used:
            return color
    return pick_color(len(used))


# ---------------------------------------------------------------- models.json
FALLBACK_MODEL_ENTRY = {
    "slug": "unknown",
    "prefer_websockets": False,
    "support_verbosity": True,
    "default_verbosity": "low",
    "apply_patch_tool_type": "freeform",
    "web_search_tool_type": "text",
    "input_modalities": ["text"],
    "supports_image_detail_original": False,
    "truncation_policy": {"mode": "tokens", "limit": 10000},
    "supports_parallel_tool_calls": True,
    "tool_mode": None,
    "multi_agent_version": "v2",
    "use_responses_lite": False,
    "include_skills_usage_instructions": False,
    "auto_review_model_override": None,
    "context_window": 262144,
    "max_context_window": 262144,
    "effective_context_window_percent": 95,
    "auto_compact_token_limit": None,
    "comp_hash": "3000",
    "reasoning_summary_format": "experimental",
    "default_reasoning_summary": "none",
    "display_name": "unknown",
    "description": "",
    "default_reasoning_level": "high",
    "supported_reasoning_levels": [
        {"effort": "low",
         "description": "Fast responses with lighter reasoning"},
        {"effort": "high",
         "description": "Extra high reasoning depth for complex problems"},
        {"effort": "max",
         "description": "Maximum reasoning depth for the hardest problems"},
    ],
    "shell_type": "shell_command",
    "visibility": "list",
    "minimal_client_version": "0.144.0",
    "supported_in_api": True,
    "availability_nux": None,
    "upgrade": None,
    "priority": 1,
    "model_messages": {
        "instructions_template": "",
        "instructions_variables": {
            "personality_default": "",
            "personality_friendly": "",
            "personality_pragmatic": "",
        },
        "approvals": None,
    },
    "experimental_supported_tools": [],
    "supports_search_tool": False,
    "default_service_tier": None,
    "supports_reasoning_summaries": True,
}


def load_models_skeleton(models_json_path=None):
    """取 ~/.codex/models.json 的 models[0] 作为骨架，字段集原样保留。

    骨架不存在时退回内置最小骨架，保证功能可用。
    """
    path = models_json_path or MODELS_JSON
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        models = data.get("models") if isinstance(data, dict) else None
        if isinstance(models, list) and models and isinstance(models[0], dict):
            return copy.deepcopy(models[0])
    except Exception:
        pass
    return copy.deepcopy(FALLBACK_MODEL_ENTRY)


def build_models_doc(skeleton, provider):
    """为单个 custom provider 生成 models.<id>.json 的内容（只放一条模型）。"""
    entry = copy.deepcopy(skeleton) if skeleton else copy.deepcopy(FALLBACK_MODEL_ENTRY)
    model = provider.get("model") or ""
    label = "%s · %s" % (provider.get("name") or provider.get("id") or "", model)
    entry["slug"] = model
    if "display_name" in entry:
        entry["display_name"] = label
    if "description" in entry:
        entry["description"] = label
    if "priority" in entry:
        entry["priority"] = 1
    # 骨架是从 DeepSeek 的模型定义深拷贝来的，里面的能力声明属于它自己的
    # 服务端实现。第三方厂商的 Responses 端点未必实现了 Codex 的这些扩展，
    # 声明成支持会让 Codex 发出对方不认识的工具类型，直接把请求打回 400。
    # 实际踩到过：supports_search_tool=True 时 Kimi 返回
    #   invalid_request_error: tools.13: tool type "tool_search" is not supported
    # 所以这里统一按保守值收敛，宁可少用能力也不要请求失败。
    entry["supports_search_tool"] = False
    # 注意：不要动 web_search_tool_type。它只接受字符串或 map，写成 null 会让
    # Codex 直接报 “failed to parse model_catalog_json ... invalid type: null”，
    # 连应用都起不来。联网搜索本来就由 config.toml 的 web_search = "disabled" 关闭。
    return {"models": [entry]}


def write_models_json(path, skeleton, provider):
    """把 models.<id>.json 落到磁盘。"""
    doc = build_models_doc(skeleton, provider)
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2))
    return path


# ---------------------------------------------------------------- 切换动作
def _backup_sort_key(path):
    """备份文件的排序键：(文件名里的时间戳, 创建时间, 文件名)。

    不能直接用 mtime 排序 —— shutil.copy2 会把**源文件**的 mtime 复制过来，
    同一份 config.toml 连切多次得到的备份 mtime 全都一样，排序就失效了。
    文件名里的 %Y%m%d-%H%M%S-%f 是定宽数字，字典序即时间序，最可靠。
    """
    name = os.path.basename(path)
    match = re.search(r"(\d{8}-\d{6}(?:-\d{6})?)\.toml$", name)
    stamp = match.group(1) if match else ""
    try:
        created = os.path.getctime(path)
    except Exception:
        created = 0.0
    return (stamp, created, name)


def prune_backups(backup_dir, keep=BACKUP_KEEP):
    """只保留最近 keep 份 config-*.toml 备份，返回被删掉的路径列表。"""
    try:
        names = [name for name in os.listdir(backup_dir)
                 if name.startswith("config-") and name.endswith(".toml")]
    except Exception:
        return []
    paths = [os.path.join(backup_dir, name) for name in names]
    paths.sort(key=_backup_sort_key, reverse=True)
    removed = []
    for path in paths[keep:]:
        try:
            os.remove(path)
            removed.append(path)
        except Exception:
            pass
    return removed


def backup_config(config_path, backup_dir, from_id):
    """备份当前 config.toml，返回备份路径（无原文件则返回空串）。

    时间戳精确到微秒：同一秒内连切多次也不会互相覆盖（qa 实测旧版会只剩 1 份）。
    """
    if not os.path.isfile(config_path):
        return ""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = "config-%s-%s.toml" % (from_id or "unknown", stamp)
    target = os.path.join(backup_dir, name)
    shutil.copy2(config_path, target)
    prune_backups(backup_dir)
    return target


def apply_provider(config_path, provider, profiles_dir, backup_dir, from_id=""):
    """把某个厂商的配置写进 config.toml，返回操作日志列表。

    流程：读基底 → 改写 → 静态校验 → 备份 → (custom) 生成 models.<id>.json
          → 落盘 → 再校验一次 → 失败则回滚。

    关键顺序：**校验通过之后才备份**。厂商配置本身不合法（id 有空格、
    base_url 是 ftp://、wire_api 是 chat…）时，一个文件都不该落 ——
    否则用户反复点一个坏配置，30 个备份位会被同一份内容逐渐占满，
    把真正有用的历史备份挤掉。备份仍必须在 atomic_write 之前完成，
    这样写盘失败时才有东西可回滚。
    """
    logs = []
    base_text = read_text(config_path)

    # 先在内存里生成并自检。这里抛异常就抛，磁盘上不留任何痕迹。
    new_text = build_config(base_text, provider, profiles_dir)
    validate_config(new_text, provider)

    # ---- 以下才开始动磁盘
    backup_path = backup_config(config_path, backup_dir, from_id)
    if backup_path:
        logs.append("已备份原配置 → %s" % os.path.basename(backup_path))

    if (provider.get("kind") or "custom") == "custom":
        models_name = "models.%s.json" % (provider.get("id") or "custom")
        models_path = os.path.join(profiles_dir, models_name)
        skeleton = load_models_skeleton()
        write_models_json(models_path, skeleton, provider)
        logs.append("已生成模型目录 %s（slug = %s）"
                    % (models_name, provider.get("model") or ""))

    try:
        atomic_write_text(config_path, new_text)
        validate_config(read_text(config_path), provider)
    except Exception:
        if backup_path and os.path.isfile(backup_path):
            shutil.copy2(backup_path, config_path)
            logs.append("自检失败，已回滚到备份")
        raise

    tail = "（官方账号登录）" if (provider.get("kind") or "custom") == "official" \
        else "（协议 %s）" % (provider.get("wire_api") or DEFAULT_WIRE_API)
    logs.append("已写入「%s」配置，model = %s %s"
                % (provider.get("name") or provider.get("id"),
                   provider.get("model") or "", tail))
    return logs


# ---------------------------------------------------------------- 进程控制
def codex_running():
    """判断 Codex 桌面版是否在跑。

    注意：不能给 subprocess 传 text=True —— 中文系统下 tasklist 的输出
    表头是 GBK，text=True 会抛 UnicodeDecodeError，在后台线程里会静默失败。
    """
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ChatGPT.exe", "/NH"],
            capture_output=True, timeout=15, creationflags=NO_WIN,
        )
        return b"ChatGPT.exe" in completed.stdout
    except Exception:
        return False


def kill_codex():
    """无条件结束 Codex。进程不在时 taskkill 会静默失败，不影响结果。"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "ChatGPT.exe"],
                       capture_output=True, timeout=25, creationflags=NO_WIN)
        return True
    except Exception:
        return False


def launch_codex():
    """通过 explorer 的 shell:AppsFolder 拉起 Codex（不能直接 Popen 打包应用）。"""
    try:
        subprocess.Popen(["explorer.exe", APP_ID])
        return True
    except Exception:
        return False


def restart_codex(log=None):
    """关掉再拉起 Codex。log 是可选的回调，用来把进度写到 GUI。"""
    def emit(message):
        if callable(log):
            try:
                log(message)
            except Exception:
                pass

    was_running = codex_running()
    if was_running:
        emit("正在关闭 Codex …")
    kill_codex()
    if was_running:
        for _ in range(24):
            if not codex_running():
                break
            time.sleep(0.25)
        emit("Codex 已关闭")
    else:
        time.sleep(1.0)
    ok = launch_codex()
    if ok:
        emit("已重新拉起 Codex（窗口出现约需 3~4 秒）")
    else:
        emit("拉起 Codex 失败，请手动打开")
    return ok


# ---------------------------------------------------------------- HTTP 探测
def _describe_net_error(exc):
    """把网络异常翻译成一句人话。"""
    error = exc
    if isinstance(error, urllib.error.URLError) and \
            not isinstance(error, urllib.error.HTTPError):
        reason = getattr(error, "reason", None)
        if isinstance(reason, BaseException):
            error = reason
        elif reason is not None:
            return _describe_net_error_text(str(reason))
    if isinstance(error, socket.timeout):
        return "超时（服务端无响应）"
    if isinstance(error, ssl.SSLError):
        return "SSL 握手失败"
    return _describe_net_error_text("%s: %s" % (type(error).__name__, error))


def _describe_net_error_text(text):
    """按关键字把异常文本翻译成中文。"""
    low = (text or "").lower()
    if "timed out" in low or "timeout" in low:
        return "超时（服务端无响应）"
    if "getaddrinfo" in low or "name or service not known" in low or \
            "nodename nor servname" in low or "no address associated" in low:
        return "域名解析失败"
    if "connection refused" in low or "refused" in low:
        return "连接被拒绝"
    if "certificate" in low or "ssl" in low or "tlsv1" in low:
        return "SSL 证书校验失败"
    if "network is unreachable" in low or "not reachable" in low:
        return "网络不可达"
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned[:160] if cleaned else "未知错误"


def _looks_like_ssl_error(exc):
    """判断异常是否属于证书类问题，属于则允许降级重试一次。"""
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
        return True
    text = ("%s %s" % (exc, reason or "")).lower()
    return "certificate" in text or "ssl" in text or "tlsv1" in text


def _http_post(url, payload, api_key, timeout):
    """发一次 POST，返回 (状态码, 响应文本)。网络层失败抛 NetError。

    代理：urllib 默认读取 HTTP_PROXY / HTTPS_PROXY 环境变量，这里保持默认行为。
    SSL：先严格校验；若判定为证书类错误，再降级（部分校园网 / 公司网做中间人）
    重试一次，且降级只作用于本次请求的 context，不改全局。
    """
    body = json.dumps(payload).encode("utf-8")
    last_error = None
    for insecure in (False, True):
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "codex-model-switcher/2.0")
        if api_key:
            request.add_header("Authorization", "Bearer " + api_key)

        context = ssl.create_default_context()
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        try:
            response = urllib.request.urlopen(request, timeout=timeout, context=context)
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                text = ""
            return exc.code, text
        except Exception as exc:
            last_error = exc
            if not insecure and _looks_like_ssl_error(exc):
                continue
            raise NetError(_describe_net_error(exc))
        try:
            text = response.read(8192).decode("utf-8", "replace")
            return response.getcode(), text
        finally:
            try:
                response.close()
            except Exception:
                pass

    raise NetError(_describe_net_error(last_error))


# ------------------------------------------------- 模型名错误的识别
# 很多厂商在「模型名写错」时返回的不是 404，而是 400（也有 404/405），
# 错误体里通常写得很清楚：model not found / invalid model / 模型不存在…
# 只按状态码判断会把这情况误报成「端点存在、可用」，界面显示绿灯，
# 用户保存后每次真实请求都失败，而且界面上没有任何线索。所以判定
# 400 / 404 / 405 之前，必须先看一眼响应体。
#
# 但反过来误伤同样有害：把「参数错误」判成「模型名错误」会让一个完全
# 正常的厂商长期顶着红灯，用户跑去改一个本来正确的模型名。所以这里分两层：
#
#   strong —— 语义上无歧义地指向模型名本身（model not found / 模型不存在…）
#             可以直接判 fail，并且不必再去试 chat 端点；
#   weak   —— 需要推断（「模型」+ 否定词、model + does not exist），
#             有可能只是参数问题，因此只给 warn，且仍照常去试 chat 端点。
#
# 响应体可能很大（HTML 错误页、云厂商的 XML），先截断再转小写：避免性能
# 问题，也避免正文深处偶然出现的短语造成误匹配。截断长度必须与
# _http_post 里 exc.read(4096) 对齐 —— 大于读取上限是浪费，小于读取上限
# 则在 2000~4096 之间留出一段「真实可达却看不到」的盲区。
MODEL_ERROR_BODY_LIMIT = 4096

# strong：各家常见说法的英文 / 带下划线写法（一律按小写比较）。
# 注意这里没有 "invalid model" —— 它太宽（"invalid model parameter value"
# 是参数错误），改用下面的 _INVALID_MODEL_RE 带负向前瞻判断。
STRONG_MODEL_ERROR_MARKERS = (
    "model not found",
    "model does not exist",
    "model_not_found",
    "no such model",
    "unknown model",
    "model is not available",
    # 部分聚合平台的措辞："does not have access to model xxx"、
    # "you do not have access to model xxx"（do / does 两种都真实出现过），
    # 这里用去掉 s 的写法一次覆盖。
    "not have access to model",
    "模型不存在",
    "模型不可用",
    "模型未开通",
    "模型无权访问",
)

# "invalid model" 族：model 后面跟的不是参数/类型/字段这类词时才算模型名错误。
#   "invalid model name: gpt-9" / "invalid model id" / "invalid model: gpt-9" → 命中
#   "invalid model parameter value: ..." / "invalid model type"             → 不命中
_INVALID_MODEL_RE = re.compile(
    r"invalid model(?![:\s]*(?:parameter|param|value|type|schema|argument|field))")

# strong 的结构式：模型名夹在短语中间，定长 marker 匹配不到。
#   "The model `gpt-9` does not exist"
#   "The model `gpt-4.2` does not exist or you do not have access to it."
# 三个约束避免误伤：
#   1. 否定词必须出现在 model 之后（同一句内）——
#      "parameter temperature does not exist for model gpt-4o" 这种
#      否定词在前的参数错误就不会命中；
#   2. 句子边界是「点 + 空白」或「点 + 结尾」，**不是**所有点号 ——
#      现代模型名几乎都带点（gpt-4.2 / claude-3.5-sonnet / qwen2.5 /
#      llama-3.1），把点当句子边界会让这些全部漏判成绿灯；
#   3. 换行算边界（字符类里排除了 \n）。真实 HTTP 报文里，JSON 字符串内部的
#      换行会被转义成 \n 两个字符（反斜杠 + n），不在排除集里，能正常跨过；
#      字面换行只会出现在 JSON 字段之间，那里本来就该断开。
_EN_MODEL_ABSENT_RE = re.compile(
    r"\bmodels?\b(?:(?!\.\s|\.$)[^;\n]){0,40}?(?:does not exist|do not exist|not exist|not found)")

# weak 的结构式：只能说明「这个模型眼下用不了」，说不清是名字错还是不可用。
# 点号规则与上面的 _EN_MODEL_ABSENT_RE 完全一致（模型名带点是常态，
# gpt-4.2 / qwen2.5；两层用两套规则迟早出事）。
_EN_MODEL_WEAK_RE = re.compile(
    r"\bmodels?\b(?:(?!\.\s|\.$)[^;\n]){0,40}?"
    r"(?:is\s+not\s+available|not\s+available|is\s+not\s+supported|unavailable)")

# weak 的中文结构式：「模型」后 12 字符内紧跟否定词。
#   "模型 GPT-9 不存在"        → weak（间距 7）
#   "模型参数 temperature 不存在" → 间距 17，不再命中（这是参数错误）
_ZH_MODEL_WEAK_RE = re.compile(u"模型[^。；\n]{0,12}(?:不存在|不可用|未开通|无权访问)")

# 中文里「模型」后面接这些词时，说的是围绕模型的 API / 周边能力，不是模型名：
#   "模型列表接口暂时不可用，不影响推理" → 是接口挂了，不是模型名错
#   "模型目录不存在"                    → 是模型仓库/目录，不是模型名错
#   "模型参数不存在"                    → 是参数错
# 命中则不参与 weak 判定（与英文的 _EN_MODEL_OFFTOPIC_RE 同一个作用）。
_ZH_OFFTOPIC_RE = re.compile(
    u"模型(?:列表|接口|服务|文档|价格|计费|监控|管理|市场|广场|详情|用量|参数"
    u"|目录|库|网关)")

# 英文同理：model 后面紧跟这些词时，说的是 API 面而不是某个具体模型。
#   "model list endpoint is not available" → ok
#   "model gpt-4o is unavailable"          → 仍走 weak 兜底
_EN_MODEL_OFFTOPIC_RE = re.compile(
    r"\bmodels?\s+(?:list|listing|endpoint|docs?|documentation|catalog|catalogue"
    r"|api|pricing|prices|usage|dashboard|console)\b")

# 参数类词汇：出现在 model 附近时，说明讨论的是参数而不是模型名。
# 注意这里**不收 "value"** —— 厂商报文里 "valid values: gpt-4o"、
# "please check the value" 太常见，一收就会把真阳性漏判成绿灯。
_PARAM_CONTEXT_RE = re.compile(
    u"parameter|param|argument|field|attribute|schema|参数")

# model 词前后各看这么多字符来判断是否在讲参数
_PARAM_CONTEXT_WINDOW = 40

# 400 有时是 Key 的问题（多数厂商把 Key 错报成 400 而不是 401），
# 命中就判 auth_fail，别让一个坏 Key 顶着绿灯。
# 只对 400 生效：401/403 分支本来就判 auth_fail，无需重复。
KEY_ERROR_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "api key is invalid",
    "api key not valid",
    "api key has expired",
    "expired api key",
    "invalid_api_key",
    u"api 密钥无效",
    u"密钥无效",
)


def _normalized_body(body_text):
    """把响应体截断并转小写，返回供匹配用的文本（空则返回 ""）。"""
    if not body_text:
        return ""
    try:
        return str(body_text)[:MODEL_ERROR_BODY_LIMIT].lower()
    except Exception:
        return ""


def _has_param_context(text, start, end):
    """model 词附近是否出现参数类词汇。

    Args:
        text: 已转小写的响应体。
        start: 命中片段的起始下标。
        end: 命中片段的结束下标。

    Returns:
        bool: 附近有 parameter / param / schema / value / 参数 之类词汇时返回
        True —— 此时更可能是在说参数而不是模型名。
    """
    left = max(0, start - _PARAM_CONTEXT_WINDOW)
    right = min(len(text), end + _PARAM_CONTEXT_WINDOW)
    return bool(_PARAM_CONTEXT_RE.search(text[left:right]))


def _model_error_level(body_text):
    """判断响应体是否在说「模型名有问题」，并给出置信度。

    Args:
        body_text: 服务端返回的原始响应体，可能是 JSON / HTML / XML，
            也可能是空串或 None。

    Returns:
        str: "strong" 明确指向模型名本身；"weak" 疑似但可能误伤；
        空串表示不像模型名问题。
    """
    text = _normalized_body(body_text)
    if not text:
        return ""
    for marker in STRONG_MODEL_ERROR_MARKERS:
        if marker in text:
            return "strong"
    if _INVALID_MODEL_RE.search(text):
        return "strong"
    # 上下文里出现参数类词汇时不能判 strong（会误伤参数错误），但也**不能
    # 落回空串**（那等于绿灯，真阳性就漏了）—— 记下来，最后降级成 weak。
    downgraded = False
    for match in _EN_MODEL_ABSENT_RE.finditer(text):
        if _EN_MODEL_OFFTOPIC_RE.match(text, match.start()):
            continue
        if _has_param_context(text, match.start(), match.end()):
            downgraded = True
            continue
        return "strong"
    for match in _ZH_MODEL_WEAK_RE.finditer(text):
        # 逐个确认「模型」后面接的不是「列表/接口/目录/参数」这类周边名词
        if _ZH_OFFTOPIC_RE.match(text, match.start()):
            continue
        return "weak"
    for match in _EN_MODEL_WEAK_RE.finditer(text):
        if _EN_MODEL_OFFTOPIC_RE.match(text, match.start()):
            continue
        if _has_param_context(text, match.start(), match.end()):
            downgraded = True
            continue
        return "weak"
    if downgraded:
        return "weak"
    return ""


def _looks_like_model_error(body_text):
    """响应体是否「像」在说模型名有问题（strong / weak 都算）。

    只用于粗筛与测试；真正决定 status 的是 _model_error_level。
    """
    return bool(_model_error_level(body_text))


def _looks_like_key_error(body_text):
    """响应体是否在说 Key 无效（用于 400 —— 不少厂商把 Key 错报成 400）。"""
    text = _normalized_body(body_text)
    if not text:
        return False
    for marker in KEY_ERROR_MARKERS:
        if marker in text:
            return True
    return False


def _safe_model_name(model, secret):
    """把用户填的模型名安全地嵌进文案。

    model 是用户手填的内容，一律过 redact（与全模块「面向用户的文本
    绝不带密钥」的约定保持一致）。注意是用 api_key 当密钥去掩码，
    而不是 redact(model, model) —— 后者会把模型名本身掩成 gpt****-9，
    正好毁掉「文案要带上模型名方便对照」这个目的。
    """
    return redact((model or "").strip(), secret).strip() or "(空)"


def _model_error_detail(model, secret):
    """strong 命中的详情文案：可以确定是模型名本身的问题。"""
    return "模型名不存在或不可用：%s（检查是否拼错，或该 Key 无权访问这个模型）" \
        % _safe_model_name(model, secret)


def _model_error_warn_detail(model, secret):
    """weak 命中的详情文案：只能说疑似，端点本身是存在的。"""
    return "疑似模型名问题，请核对模型名 %s；若模型名无误可忽略此提示" \
        % _safe_model_name(model, secret)


def probe_provider(base_url, api_key, model, timeout=15):
    """真的发一次最小请求，判断 Key 是否可用、协议是否兼容。

    返回 (status, detail, wire_api)：
      status  ∈ ok / auth_fail / warn / fail
      wire_api 为 "responses" / "chat" / None

    背景：Codex CLI 0.163 只支持 wire_api = "responses"，
    wire_api = "chat" 已被移除，所以这里要如实告诉用户
    「这家只支持 chat，当前版本用不了」，不能给假承诺。
    """
    base = ensure_trailing_slash(base_url)
    if not base:
        return "fail", "Base URL 为空", None
    url = base + "responses"
    payload = {
        "model": model,
        "input": "ping",
        "max_output_tokens": 16,
        "stream": False,
    }

    secret = api_key or ""
    try:
        code, body = _http_post(url, payload, secret, timeout)
    except NetError as exc:
        return "fail", redact("无法连接：%s" % exc, secret), None
    except Exception as exc:
        return "fail", redact("无法连接：%s" % _describe_net_error(exc), secret), None

    if code == 200:
        return "ok", "responses 可用", "responses"
    if code in (401, 403):
        return "auth_fail", "Key 无效或无权限", None
    if code == 429:
        return "warn", "被限流，端点可用", "responses"
    if code == 400:
        # 400 有三种含义，必须先看 body 再下结论。顺序是 key → strong → weak：
        #   * Key 无效（不少厂商把 Key 错报成 400）→ auth_fail。放在最前面，
        #     因为 Key 坏了的时候报文里出现的「model not found」多半是假象，
        #     先报鉴权失败让用户去查 Key 才是对的
        #   * 模型名写错（多数厂商不用 404 表示 model not found）→ fail，
        #     否则界面绿灯、用户保存后每次请求都失败
        #   * 参数差异（max_output_tokens 太大之类）→ 端点确实可用
        if _looks_like_key_error(body):
            return "auth_fail", "Key 无效或无权限", None
        level = _model_error_level(body)
        if level == "strong":
            return "fail", _model_error_detail(model, secret), None
        if level == "weak":
            # 只是「疑似」：端点确实在，模型名不一定错。给 warn 而不是 fail，
            # 别让本该正常的厂商长期顶着红灯。
            return "warn", _model_error_warn_detail(model, secret), "responses"
        return "ok", "端点存在（HTTP 400，参数差异）", "responses"

    if code in (404, 405):
        # 同样先看 body，但只有 strong 才值得跳过 chat 探测。若其实是模型名
        # 的问题，去试 chat 只会把它误报成「Base URL 错」或「只支持 chat」。
        # weak 不能据此否定端点：一个 404 页面里提到 /models 很常见，
        # 那种情况必须继续试 chat，才能落到「该厂商只支持 chat」这个正确结论。
        if _model_error_level(body) == "strong":
            return "fail", _model_error_detail(model, secret), None
        chat_url = base + "chat/completions"
        chat_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
        try:
            chat_code, _chat_body = _http_post(chat_url, chat_payload, secret, timeout)
        except NetError as exc:
            return "fail", redact("无法连接：%s" % exc, secret), None
        except Exception as exc:
            return "fail", redact("无法连接：%s" % _describe_net_error(exc), secret), None
        if chat_code == 200:
            return "warn", "该厂商只支持 chat 协议，当前 Codex 版本不支持，无法使用", "chat"
        if chat_code in (400, 429):
            return "warn", "该厂商只支持 chat 协议，当前 Codex 版本不支持，无法使用", "chat"
        if chat_code in (401, 403):
            return "auth_fail", "Key 无效或无权限", None
        if chat_code in (404, 405):
            return "fail", "端点不存在，检查 Base URL", None
        return "fail", "服务端返回 HTTP %d" % chat_code, None

    if code in (500, 502, 503, 504):
        return "fail", "服务端错误 HTTP %d，稍后重试" % code, None
    return "fail", "未预期的 HTTP %d" % code, None


def probe_provider_dict(provider, timeout=15):
    """probe_provider 的 dict 版本，顺带生成可直接塞进 last_probe 的记录。"""
    status, detail, wire = probe_provider(
        provider.get("base_url") or "",
        provider.get("api_key") or "",
        provider.get("model") or "",
        timeout=timeout,
    )
    record = {
        "at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "ok": status == "ok",
        "detail": detail,
        "wire_api": wire,
        "status": status,
    }
    return status, detail, wire, record


def network_check(url, timeout=6):
    """网络体检：只看链路通不通，不看 Key。

    403 表示链路通但节点被拦（OpenAI 常见），判为 ok 并说明。
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "curl/8.0"})
    started = time.time()
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=context)
        try:
            response.read(64)
        finally:
            try:
                response.close()
            except Exception:
                pass
        return "ok", "链路通（%.0fms）" % ((time.time() - started) * 1000)
    except urllib.error.HTTPError as exc:
        elapsed = (time.time() - started) * 1000
        if exc.code in (401, 403):
            return "ok", "链路通 · HTTP %d（%.0fms）" % (exc.code, elapsed)
        if exc.code == 429:
            return "warn", "链路通 · HTTP 429（%.0fms）" % elapsed
        return "warn", "HTTP %d" % exc.code
    except Exception as exc:
        return "bad", _describe_net_error(exc)


def host_of(url):
    """从 URL 里取出主机名，用于网络体检的显示。"""
    text = (url or "").strip()
    match = re.match(r"^https?://([^/]+)", text, re.I)
    if match:
        return match.group(1)
    return text or "(空)"


# ---------------------------------------------------------------- 自检入口
if __name__ == "__main__":
    # 直接跑本文件时做一个无副作用的自检，方便排查环境
    print("python:", sys.version.replace("\n", " "))
    print("config:", CONFIG_PATH, "存在" if os.path.isfile(CONFIG_PATH) else "缺失")
    print("backup:", BACKUP_DIR)
    base = read_text(CONFIG_PATH)
    head, sections = split_config(base)
    print("顶层键:", sorted(_parse_top_keys(head).keys()))
    print("段落数:", len(re.findall(r"(?m)^\[", sections)))
    demo = {
        "id": "demo",
        "name": "Demo",
        "kind": "custom",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-1234567890abcdef",
        "model": "demo-model",
        "wire_api": DEFAULT_WIRE_API,
    }
    generated = build_config(base, demo, os.path.dirname(os.path.abspath(__file__)))
    validate_config(generated, demo)
    print("改写自检通过，行数:", len(generated.splitlines()))
    print("掩码示例:", mask_key(demo["api_key"]))

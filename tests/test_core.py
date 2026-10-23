# -*- coding: utf-8 -*-
"""core.py 的单元测试。

只依赖标准库（unittest + tempfile），不联网，不碰真实的 ~/.codex/config.toml。
运行方式：

    python -m unittest discover -s tests -v
    python tests\\test_core.py
"""

import os
import re
import sys
import shutil
import tempfile
import time
import unittest
from unittest import mock

# 让 tests/ 子目录能 import 到上一层的 core.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core  # noqa: E402


# 真实 C:\Users\<用户名>\.codex\config.toml 的**结构完整脱敏副本**。
# 键名、段落顺序、段的种类全部与真实文件一致（共 11 个 section），
# 只把所有路径和 token 换成假值。样本必须够「脏」才测得出问题：
#   * notify 是多行数组
#   * NODE_REPL_TRUSTED_SERVICES 的单引号字符串里含大括号
#   * SKY_CUA_NATIVE_PIPE_DIRECTORY 的字符串里含 \\.\pipe 反斜杠
#   * [mcp_servers.maker-lua-lsp] 的 args 是跨行数组
#   * 已存在 v1 写入的 [model_providers.deepseek] 段
# 这样任何「静默丢段」「括号误判」的回归都会被抓到。
SAMPLE_CONFIG = r"""check_for_update_on_startup = false
notify = [
    'C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\2fbcd108736f9c20\bin\node_modules\@oai\sky\bin\windows\codex-computer-use.exe',
    "turn-ended",
]
model = "deepseek-flash"
model_reasoning_effort = "high"
model_provider = "deepseek"
preferred_auth_method = "apikey"
forced_login_method = "api"
web_search = "disabled"
show_raw_agent_reasoning = true
model_catalog_json = "C:/Users/tester/.codex/models.json"

[features]
daemon_auto_start = false

[analytics]
enabled = false

[desktop]
enabled-reasoning-efforts = ["low", "medium", "high", "xhigh", "ultra", "max"]
followUpQueueMode = "steer"
external-agent-import-sync-item-types = "all"
external-agent-import-sync-enabled = true
conversationDetailMode = "STEPS_COMMANDS"
sansFontSize = 14
codeFontSize = 13
ambient-suggestions-enabled = true

[mcp_servers.node_repl]
args = []
command = 'C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\2fbcd108736f9c20\bin\node_repl.exe'
env_vars = ["BROWSER_USE_ACCESS_EXECUTOR_ID", "BROWSER_USE_ACCESS_PIPE_PATH", "BROWSER_USE_ACCESS_VERIFYING_IDENTITY", "CODEX_WINDOWS_REGISTERED_CORE"]
startup_timeout_sec = 120

[mcp_servers.node_repl.env]
NODE_REPL_NATIVE_PIPE_CONNECT_TIMEOUT_MS = "1000"
NODE_REPL_NODE_MODULE_DIRS = 'C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\2fbcd108736f9c20\bin\node_modules'
NODE_REPL_NODE_PATH = 'C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\2fbcd108736f9c20\bin\node.exe'
NODE_REPL_TRUSTED_CODE_PATHS = 'C:\Users\tester\.codex;C:\Users\tester\AppData\Local\OpenAI\Codex\runtimes\cua_node\2fbcd108736f9c20\bin\node_modules'
CODEX_HOME = 'C:\Users\tester\.codex'
BROWSER_USE_AVAILABLE_BACKENDS = "chrome,iab,mcpapps"
BROWSER_USE_TINYSKY_ENABLED = "1"
NODE_REPL_INSTRUCTIONS_USE_CASE_BROWSER = "Control the in-app browser in conjunction with the Browser Plugin."
NODE_REPL_INSTRUCTIONS_USE_CASE_CHROME = "Control the Chrome browser in conjunction with the Chrome Plugin. Prefer this method of controlling Chrome over alternatives (such as Computer Use) unless the user explicitly mentions an alternative."
BROWSER_USE_CODEX_APP_BUILD_FLAVOR = "prod"
BROWSER_USE_CODEX_APP_VERSION = "26.1016.51333"
NODE_REPL_TRUSTED_SERVICES = '{"browser":"C:/Users/tester/.codex/plugins/cache/openai-bundled/browser/26.1016.51333/scripts/browser-service.mjs","sky":"@oai/sky/service"}'
SKY_CUA_NATIVE_PIPE = "1"
SKY_CUA_NATIVE_PIPE_DIRECTORY = '\\.\pipe\codex-computer-use-00000000-1111-2222-3333-444444444444'
CODEX_CLI_PATH = 'C:\Users\tester\AppData\Local\OpenAI\Codex\bin\6257d9e8f63c0f2b\codex.exe'
NODE_REPL_UNTRUSTED_ENV_ALLOWLIST = "CODEX_WINDOWS_REGISTERED_CORE"

[mcp_servers.cua_repl]
command = 'C:\Program Files\WindowsApps\OpenAI.Codex_26.1016.6573.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe'
enabled = false

[mcp_servers.taptap-maker]
command = 'E:\Kimi\Kimi.exe'
args = ['C:\Users\tester\.taptap-maker\mcp-runtime\0.0.36\dist\maker.js']

# 让 Kimi 的 Electron 以无界面的 Node 运行时方式跑 maker.js（MCP server），
# 否则会直接拉起 Kimi 主程序窗口（就是"Kimi 老是跳出来"的原因）。
[mcp_servers.taptap-maker.env]
ELECTRON_RUN_AS_NODE = "1"

[mcp_servers.maker-lua-lsp]
command = 'c:\users\tester\.taptap-maker\lua-lsp-venv\scripts\python.exe'
args = [
    "-m",
    "maker_lua_lsp",
    "--mode",
    "watch",
    "--path",
    "./scripts",
    "--output-dir",
    "./logs",
]

[windows]
sandbox = "elevated"

[model_providers.deepseek]
name = "deepseek"
base_url = "https://api.deepseek.com/"
wire_api = "responses"
experimental_bearer_token = "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""

# 真实文件里的 section 总数（features/analytics/desktop/node_repl/node_repl.env/
# cua_repl/taptap-maker/taptap-maker.env/maker-lua-lsp/windows/model_providers）
SAMPLE_SECTION_COUNT = 11


def split_sections(text):
    """把文本切成 {header: 整段原文}，用来做「逐段全等」断言。

    比 assertIn 子串断言严格得多：段落被整段删掉、内容被悄悄改写、
    段的 body 被截断，都会直接暴露出来。
    """
    _head, sections = core.split_config(text)
    result = {}
    matches = list(re.finditer(r"(?m)^\[[^\[\]\n]+\]", sections))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) \
            else len(sections)
        result[match.group(0)] = sections[start:end].strip()
    return result


def section_headers(text):
    return list(split_sections(text).keys())


def custom_provider(**overrides):
    provider = {
        "id": "deepseek",
        "name": "DeepSeek",
        "kind": "custom",
        "desc": "api.deepseek.com",
        "base_url": "https://api.deepseek.com/",
        "api_key": "sk-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "model": "deepseek-flash",
        "wire_api": "responses",
    }
    provider.update(overrides)
    return provider


def official_provider(**overrides):
    provider = {
        "id": "openai",
        "name": "官方 GPT",
        "kind": "official",
        "desc": "ChatGPT 账号登录",
        "model": "gpt-6-luna",
        "wire_api": "responses",
    }
    provider.update(overrides)
    return provider


# ---------------------------------------------------------------- 探测用假 HTTP
PROBE_BASE = "https://api.example.com/v1/"
PROBE_KEY = "sk-test-0123456789abcdef"
PROBE_MODEL = "deepseek-flash"


class FakeHttp(object):
    """伪造 core._http_post，按 URL 后缀返回预设响应，并记录每次请求。

    探测相关的用例一律靠它驱动，**绝不发真实网络请求**：
    既保证测试离线可跑，也能用 calls 精确断言「有没有去试 chat 端点」。

    responses 的 key 是 URL 后缀（"responses" / "chat/completions"），
    值可以是 (状态码, 响应体) 二元组，也可以是抛异常的 callable。
    """

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def __call__(self, url, payload, api_key, timeout):
        self.calls.append(url)
        matched = ""
        for suffix in self.responses:
            if url.endswith(suffix) and len(suffix) > len(matched):
                matched = suffix
        if not matched:
            raise AssertionError("未预期的请求（测试没有为它准备响应）：%s" % url)
        result = self.responses[matched]
        if callable(result):
            return result(url)
        return result

    def count(self, suffix="chat/completions"):
        """统计命中某个后缀的请求次数（默认数 chat 探测次数）。"""
        return len([url for url in self.calls if url.endswith(suffix)])


def raise_net_error(url):
    raise core.NetError("连接被拒绝")


def raise_plain_error(url):
    raise OSError("socket 意外断开")


class TempDirTest(unittest.TestCase):
    """所有用例都在临时目录里跑，绝不碰真实配置。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cms-test-")
        self.profiles = os.path.join(self.tmp, "profiles")
        os.makedirs(self.profiles)
        self.backup = os.path.join(self.profiles, "backup")
        self.config_path = os.path.join(self.tmp, "config.toml")
        # 播种用的最小预设集（和 presets.json 里的前两条等价）
        self.presets = [
            {"id": "openai", "name": "官方 GPT", "kind": "official",
             "model": "gpt-6-luna", "color": "#10a37f"},
            {"id": "deepseek", "name": "DeepSeek", "kind": "custom",
             "base_url": "https://api.deepseek.com/",
             "model": "deepseek-flash", "color": "#4d6bfe"},
        ]
        # 让 load_models_skeleton 走内置兜底骨架，避免依赖本机 models.json
        self._saved_models_json = core.MODELS_JSON
        core.MODELS_JSON = os.path.join(self.tmp, "no-such-models.json")

    def tearDown(self):
        core.MODELS_JSON = self._saved_models_json
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------- 小工具
class TestMaskKey(unittest.TestCase):
    def test_mask_middle_of_long_key(self):
        self.assertEqual(core.mask_key("sk-test0000000000000000000000000abc"),
                         "sk-****abc")

    def test_empty_key(self):
        self.assertEqual(core.mask_key(""), "(未填写)")
        self.assertEqual(core.mask_key(None), "(未填写)")

    def test_short_key_fully_masked(self):
        self.assertEqual(core.mask_key("abcd1234"), "********")

    def test_redact_removes_secret_from_text(self):
        secret = "sk-0123456789abcdef"
        text = "请求失败 Authorization: Bearer %s" % secret
        cleaned = core.redact(text, secret)
        self.assertNotIn(secret, cleaned)
        self.assertIn("sk-****def", cleaned)

    def test_redact_ignores_empty_secret(self):
        self.assertEqual(core.redact("hello", ""), "hello")


class TestBaseUrl(unittest.TestCase):
    def test_appends_slash(self):
        self.assertEqual(core.ensure_trailing_slash("https://api.x.com/v1"),
                         "https://api.x.com/v1/")

    def test_keeps_existing_slash(self):
        self.assertEqual(core.ensure_trailing_slash("https://api.x.com/"),
                         "https://api.x.com/")

    def test_empty(self):
        self.assertEqual(core.ensure_trailing_slash(""), "")
        self.assertEqual(core.ensure_trailing_slash(None), "")

    def test_host_of(self):
        self.assertEqual(core.host_of("https://api.deepseek.com/v1/"),
                         "api.deepseek.com")


class TestProviderId(unittest.TestCase):
    def test_slugify_chinese_name(self):
        # 中文被剔除后剩下的字母要能成为合法 id
        self.assertEqual(core.make_provider_id("官方 GPT", []), "gpt")

    def test_slugify_spaces_and_symbols(self):
        self.assertEqual(core.make_provider_id("My Provider! (Beta)", []),
                         "my-provider-beta")

    def test_dedup_with_suffix(self):
        self.assertEqual(core.make_provider_id("DeepSeek", ["deepseek"]),
                         "deepseek-2")
        self.assertEqual(core.make_provider_id("DeepSeek", ["deepseek", "deepseek-2"]),
                         "deepseek-3")

    def test_empty_name_falls_back(self):
        self.assertEqual(core.make_provider_id("", []), "provider")
        self.assertEqual(core.make_provider_id("...", []), "provider")

    def test_never_starts_with_digit(self):
        # TOML 裸键不能以数字开头，需要加前缀
        self.assertTrue(core.make_provider_id("123ai", []).startswith("p-"))

    def test_falls_back_to_host_when_name_is_pure_chinese(self):
        # 纯中文名 slug 化后是空串，必须退到 Base URL 的域名，不能全都叫 provider
        self.assertEqual(
            core.make_provider_id(
                "阿里云百炼 / 通义千问", [],
                "https://dashscope.aliyuncs.com/compatible-mode/v1/"),
            "dashscope-aliyuncs-com")

    def test_host_fallback_also_dedups(self):
        self.assertEqual(
            core.make_provider_id("月之暗面 Kimi", ["moonshot-ai"],
                                  "https://api.moonshot.cn/v1/"),
            "kimi")


# ---------------------------------------------------------------- config 生成
class TestBuildConfigOfficial(TempDirTest):
    def test_official_has_model_but_no_provider(self):
        text = core.build_config(SAMPLE_CONFIG, official_provider(), self.profiles)
        head, sections = core.split_config(text)
        keys = core._parse_top_keys(head)
        self.assertEqual(keys.get("model"), '"gpt-6-luna"')
        self.assertNotIn("model_provider", keys)
        self.assertNotIn("model_catalog_json", keys)
        self.assertNotIn("preferred_auth_method", keys)
        self.assertNotIn("forced_login_method", keys)
        self.assertNotIn("web_search", keys)

    def test_official_keeps_other_keys(self):
        text = core.build_config(SAMPLE_CONFIG, official_provider(), self.profiles)
        self.assertIn('model_reasoning_effort = "high"', text)
        self.assertIn("show_raw_agent_reasoning = true", text)
        self.assertIn("check_for_update_on_startup = false", text)

    def test_official_keeps_notify_array_intact(self):
        text = core.build_config(SAMPLE_CONFIG, official_provider(), self.profiles)
        self.assertIn("notify = [", text)
        self.assertIn('"turn-ended",', text)
        self.assertIn("codex-computer-use.exe", text)

    def test_official_validates(self):
        provider = official_provider()
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        core.validate_config(text, provider)  # 不抛异常即通过


class TestBuildConfigCustom(TempDirTest):
    def setUp(self):
        TempDirTest.setUp(self)
        self.provider = custom_provider()

    def test_six_top_level_keys_written(self):
        text = core.build_config(SAMPLE_CONFIG, self.provider, self.profiles)
        head, _sections = core.split_config(text)
        keys = core._parse_top_keys(head)
        self.assertEqual(keys.get("model"), '"deepseek-flash"')
        self.assertEqual(keys.get("model_provider"), '"deepseek"')
        self.assertEqual(keys.get("preferred_auth_method"), '"apikey"')
        self.assertEqual(keys.get("forced_login_method"), '"api"')
        self.assertEqual(keys.get("web_search"), '"disabled"')
        self.assertIn("models.deepseek.json", keys.get("model_catalog_json", ""))

    def test_catalog_path_uses_forward_slashes(self):
        text = core.build_config(SAMPLE_CONFIG, self.provider, self.profiles)
        head, _sections = core.split_config(text)
        catalog = core._parse_top_keys(head)["model_catalog_json"]
        self.assertNotIn("\\\\", catalog)
        self.assertIn("/models.deepseek.json", catalog)

    def test_provider_section_written_once(self):
        text = core.build_config(SAMPLE_CONFIG, self.provider, self.profiles)
        _head, sections = core.split_config(text)
        self.assertEqual(core.count_section(sections, "[model_providers.deepseek]"), 1)
        self.assertIn('base_url = "https://api.deepseek.com/"', sections)
        self.assertIn('wire_api = "responses"', sections)
        self.assertIn('experimental_bearer_token = "sk-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
                      sections)

    def test_old_provider_section_replaced_not_appended(self):
        # 样本里本来就有一个 [model_providers.deepseek]，改写后段内 key 必须换成新的
        text = core.build_config(SAMPLE_CONFIG, self.provider, self.profiles)
        _head, sections = core.split_config(text)
        self.assertNotIn("sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", sections)
        self.assertEqual(text.count("[model_providers.deepseek]"), 1)

    def test_repeated_build_is_idempotent(self):
        first = core.build_config(SAMPLE_CONFIG, self.provider, self.profiles)
        second = core.build_config(first, self.provider, self.profiles)
        self.assertEqual(first, second)

    def test_validates(self):
        text = core.build_config(SAMPLE_CONFIG, self.provider, self.profiles)
        core.validate_config(text, self.provider)

    def test_base_url_auto_slash(self):
        provider = custom_provider(base_url="https://api.example.com/v1")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        _head, sections = core.split_config(text)
        self.assertIn('base_url = "https://api.example.com/v1/"', sections)

    def test_wire_api_chat_is_rejected(self):
        """wire_api = "chat" 必须被自检拦下，而不是被写进 config。

        注意断言方向：生成阶段不拦（build_config 只负责拼字符串），
        但 validate_config **必须**抛错 —— 因为 Codex 0.163 遇到 chat
        会直接拒绝加载，写进去等于让用户下次打不开 Codex。
        """
        provider = custom_provider(wire_api="chat")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        self.assertIn('wire_api = "chat"', text)
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_wire_api_defaults_to_responses(self):
        provider = custom_provider()
        provider.pop("wire_api")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        self.assertIn('wire_api = "responses"', text)
        core.validate_config(text, provider)


class TestSectionsPreserved(TempDirTest):
    """MCP、桌面设置等原有内容必须被完整保留 —— 这是 v2 最关键的设计点。

    这里一律用「逐段原文全等」而不是 assertIn 子串断言：qa 的变异实验证明，
    在 build_config 里注入「静默删掉 [desktop] 整段」的 bug，子串断言和
    validate_config 都不会报警，套件照样全绿。全等断言才能拦住这类回归。
    """

    def setUp(self):
        TempDirTest.setUp(self)
        self.before = split_sections(SAMPLE_CONFIG)

    def _assert_all_sections_preserved(self, text, expected_count):
        after = split_sections(text)
        self.assertEqual(len(after), expected_count,
                         "section 总数变了：%s" % sorted(after.keys()))
        for header, body in self.before.items():
            self.assertIn(header, after, "段落被删掉了：%s" % header)
            # 唯一允许变化的段是被重写的 provider 段
            if header == "[model_providers.deepseek]":
                continue
            self.assertEqual(after[header], body, "段落内容被改动了：%s" % header)

    def test_all_sections_byte_identical_when_custom(self):
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        self._assert_all_sections_preserved(text, SAMPLE_SECTION_COUNT)

    def test_all_sections_byte_identical_when_official(self):
        text = core.build_config(SAMPLE_CONFIG, official_provider(), self.profiles)
        self._assert_all_sections_preserved(text, SAMPLE_SECTION_COUNT)

    def test_desktop_section_present_and_identical(self):
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        after = split_sections(text)
        self.assertIn("[desktop]", after)
        self.assertEqual(after["[desktop]"], self.before["[desktop]"])
        # 逐键抽查，避免整段被替换成同名空段
        for line in ('enabled-reasoning-efforts = ["low", "medium", "high", "xhigh", "ultra", "max"]',
                     'followUpQueueMode = "steer"',
                     'conversationDetailMode = "STEPS_COMMANDS"',
                     'sansFontSize = 14',
                     'codeFontSize = 13',
                     'ambient-suggestions-enabled = true'):
            self.assertIn(line, after["[desktop]"])

    def test_section_count_unchanged(self):
        self.assertEqual(len(section_headers(SAMPLE_CONFIG)), SAMPLE_SECTION_COUNT,
                         "样本本身的 section 数就不对，后面的断言都会失效")
        for provider in (custom_provider(), official_provider()):
            text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
            self.assertEqual(len(section_headers(text)), SAMPLE_SECTION_COUNT)

    def test_multi_line_args_array_survives(self):
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        after = split_sections(text)
        lsp = after["[mcp_servers.maker-lua-lsp]"]
        for line in ('args = [', '"-m",', '"maker_lua_lsp",',
                     '"--output-dir",', '"./logs",'):
            self.assertIn(line, lsp)
        # 数组必须正常闭合：去掉 header 行后只剩 args 的那个 ]
        body = lsp.splitlines()[1:]
        self.assertEqual("\n".join(body).count("]"), 1)

    def test_braces_inside_quoted_string_survive(self):
        # NODE_REPL_TRUSTED_SERVICES 的值是单引号里的 JSON，含 {}，
        # 括号配平逻辑不能把它算进去，也不能把内容改坏
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        after = split_sections(text)
        env = after["[mcp_servers.node_repl.env]"]
        self.assertIn("NODE_REPL_TRUSTED_SERVICES = '{\"browser\":", env)
        self.assertIn('"sky":"@oai/sky/service"}', env)

    def test_pipe_path_backslashes_survive(self):
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        self.assertIn(r"'\\.\pipe\codex-computer-use-", text)

    def test_comments_survive(self):
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        self.assertIn("# 让 Kimi 的 Electron 以无界面的 Node 运行时方式跑 maker.js", text)
        self.assertIn("# 否则会直接拉起 Kimi 主程序窗口", text)

    def test_other_providers_survive_when_switching_to_official(self):
        # 切到官方时保留其它厂商的段，这样能一键切回去
        text = core.build_config(SAMPLE_CONFIG, official_provider(), self.profiles)
        self.assertIn("[model_providers.deepseek]", text)

    def test_section_order_not_broken(self):
        text = core.build_config(SAMPLE_CONFIG, custom_provider(), self.profiles)
        headers = section_headers(text)
        self.assertEqual(headers[0], "[features]")
        self.assertEqual(headers[-1], "[model_providers.deepseek]")
        # 中间的相对顺序也不能乱
        self.assertEqual(headers[:3],
                         ["[features]", "[analytics]", "[desktop]"])
        self.assertEqual(headers[-3:],
                         ["[mcp_servers.maker-lua-lsp]", "[windows]",
                          "[model_providers.deepseek]"])


class TestValidateConfig(TempDirTest):
    def test_rejects_missing_provider_section(self):
        broken = ('model = "x"\n'
                  'model_provider = "foo"\n'
                  'preferred_auth_method = "apikey"\n'
                  'forced_login_method = "api"\n'
                  'web_search = "disabled"\n'
                  'model_catalog_json = "c.json"\n')
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(broken, custom_provider(id="foo"))

    def test_rejects_unbalanced_brackets(self):
        text = 'model = "x"\n[features\ndaemon_auto_start = false\n'
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, official_provider())

    def test_rejects_duplicate_provider_section(self):
        text = ('model = "x"\n'
                'model_provider = "foo"\n'
                'preferred_auth_method = "apikey"\n'
                'forced_login_method = "api"\n'
                'web_search = "disabled"\n'
                'model_catalog_json = "c.json"\n\n'
                '[model_providers.foo]\nexperimental_bearer_token = "k"\n\n'
                '[model_providers.foo]\nexperimental_bearer_token = "k"\n')
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, custom_provider(id="foo"))

    def test_rejects_official_with_provider_key(self):
        text = 'model = "x"\nmodel_provider = "openai"\n'
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, official_provider())


# ---------------------------------------------------------------- 切换动作
class TestApplyProvider(TempDirTest):
    def setUp(self):
        TempDirTest.setUp(self)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(SAMPLE_CONFIG)

    def test_writes_config_and_models(self):
        provider = custom_provider()
        logs = core.apply_provider(self.config_path, provider,
                                   self.profiles, self.backup, "openai")
        self.assertTrue(os.path.isfile(self.config_path))
        self.assertTrue(os.path.isfile(
            os.path.join(self.profiles, "models.deepseek.json")))
        self.assertEqual(len(logs), 3)
        content = core.read_text(self.config_path)
        self.assertIn('model_provider = "deepseek"', content)
        core.validate_config(content, provider)

    def test_creates_backup(self):
        core.apply_provider(self.config_path, custom_provider(),
                            self.profiles, self.backup, "openai")
        backups = os.listdir(self.backup)
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0].startswith("config-openai-"))
        self.assertTrue(backups[0].endswith(".toml"))
        self.assertEqual(core.read_text(os.path.join(self.backup, backups[0])),
                         SAMPLE_CONFIG)

    def test_official_does_not_write_models(self):
        core.apply_provider(self.config_path, official_provider(),
                            self.profiles, self.backup, "deepseek")
        models = [name for name in os.listdir(self.profiles)
                  if name.startswith("models.")]
        self.assertEqual(models, [])

    def test_apply_from_missing_config_still_works(self):
        os.remove(self.config_path)
        logs = core.apply_provider(self.config_path, official_provider(),
                                   self.profiles, self.backup, "")
        # 原文件不存在 → 没有备份日志；official → 不生成 models；只有「已写入」一条
        self.assertEqual(len(logs), 1)
        self.assertFalse(os.path.isdir(self.backup) and os.listdir(self.backup))
        self.assertIn('model = "gpt-6-luna"', core.read_text(self.config_path))

    def test_models_file_contains_single_entry(self):
        import json
        core.apply_provider(self.config_path, custom_provider(),
                            self.profiles, self.backup, "openai")
        path = os.path.join(self.profiles, "models.deepseek.json")
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(len(doc["models"]), 1)
        self.assertEqual(doc["models"][0]["slug"], "deepseek-flash")
        self.assertIn("DeepSeek", doc["models"][0]["display_name"])

    def test_api_key_never_leaks_into_logs(self):
        provider = custom_provider()
        logs = core.apply_provider(self.config_path, provider,
                                   self.profiles, self.backup, "openai")
        secret = provider["api_key"]
        for line in logs:
            self.assertNotIn(secret, line)


# ---------------------------------------------------------------- 输入净化与校验
class TestProviderIdValidation(TempDirTest):
    """provider id 直接拼进 [model_providers.<id>]，非法 id 必须拦在写盘之前。

    qa 实测：手工把 id 改成 "a b" 时 core 自检通过、写出
    `[model_providers.a b]`，真实 tomllib 解析报 TOMLDecodeError。
    "a.b" 更阴 —— TOML 合法但会变成嵌套表，model_provider 找不到段，
    属于「配置写成功了但 Codex 静默失效」。
    """

    def test_valid_ids_pass(self):
        for pid in ("deepseek", "my-provider", "vendor_1", "A1", "p-123"):
            self.assertIsNotNone(core.PROVIDER_ID_RE.match(pid), pid)

    def test_rejects_space_in_id(self):
        provider = custom_provider(id="a b")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        self.assertIn("[model_providers.a b]", text)   # 生成阶段照拼
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_rejects_dotted_id(self):
        provider = custom_provider(id="a.b")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_rejects_slash_and_path_traversal(self):
        for bad in ("a/b", "../etc", "C:\\temp", "a\"b", "a]b"):
            provider = custom_provider(id=bad)
            text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
            with self.assertRaises(core.ConfigValidationError, msg=bad):
                core.validate_config(text, provider)

    def test_rejects_chinese_id(self):
        provider = custom_provider(id="百炼")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_normalize_provider_sanitizes_bad_id(self):
        # 非空但非法的 id 不能原样放行，要重新生成
        for bad in ("a b", "a.b", "a/b", "../etc"):
            fixed = core.normalize_provider(
                {"id": bad, "name": "My Vendor", "kind": "custom",
                 "base_url": "https://api.x.com/v1/"}, 0)
            self.assertIsNotNone(core.PROVIDER_ID_RE.match(fixed["id"]),
                                 "%r -> %r" % (bad, fixed["id"]))
            self.assertEqual(fixed["id"], "my-vendor")

    def test_normalize_provider_keeps_good_id(self):
        fixed = core.normalize_provider(
            {"id": "my-vendor-2", "name": "X", "kind": "custom"}, 0)
        self.assertEqual(fixed["id"], "my-vendor-2")

    def test_normalize_provider_falls_back_to_host(self):
        fixed = core.normalize_provider(
            {"id": "坏 id", "name": "百炼", "kind": "custom",
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/"}, 0)
        self.assertEqual(fixed["id"], "dashscope-aliyuncs-com")

    def test_apply_provider_refuses_bad_id(self):
        """写盘路径上的真实保护：非法 id 必须抛错且不留下半截文件。"""
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(SAMPLE_CONFIG)
        with self.assertRaises(core.ConfigValidationError):
            core.apply_provider(self.config_path, custom_provider(id="a b"),
                                self.profiles, self.backup, "deepseek")
        self.assertEqual(core.read_text(self.config_path), SAMPLE_CONFIG)


class TestBaseUrlValidation(TempDirTest):
    def test_rejects_empty_base_url(self):
        provider = custom_provider(base_url="")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_rejects_ftp_scheme(self):
        provider = custom_provider(base_url="ftp://api.x.com/v1/")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_rejects_schemeless_base_url(self):
        provider = custom_provider(base_url="api.x.com/v1/")
        text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
        with self.assertRaises(core.ConfigValidationError):
            core.validate_config(text, provider)

    def test_accepts_http_and_https(self):
        for url in ("https://api.x.com/v1/", "http://127.0.0.1:8080/v1/"):
            provider = custom_provider(base_url=url)
            text = core.build_config(SAMPLE_CONFIG, provider, self.profiles)
            core.validate_config(text, provider)


# ---------------------------------------------------------------- 备份
class TestBackup(TempDirTest):
    def setUp(self):
        TempDirTest.setUp(self)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(SAMPLE_CONFIG)

    def test_same_second_backups_do_not_overwrite(self):
        """同一秒内连切 5 次必须留下 5 份备份（旧版只留 1 份）。"""
        paths = []
        for _ in range(5):
            paths.append(core.backup_config(self.config_path, self.backup, "deepseek"))
        self.assertEqual(len(set(paths)), 5)
        self.assertEqual(len(os.listdir(self.backup)), 5)

    def test_backup_names_are_chronological(self):
        first = core.backup_config(self.config_path, self.backup, "openai")
        second = core.backup_config(self.config_path, self.backup, "deepseek")
        # 文件名里的时间戳字典序 == 时间序，prune 依赖这一点
        self.assertLess(core._backup_sort_key(first),
                        core._backup_sort_key(second))

    def test_prune_keeps_newest_n(self):
        made = [core.backup_config(self.config_path, self.backup, "openai")
                for _ in range(8)]
        removed = core.prune_backups(self.backup, keep=3)
        self.assertEqual(len(removed), 5)
        left = sorted(os.listdir(self.backup))
        self.assertEqual(len(left), 3)
        # 留下的必须是最新的 3 份
        self.assertEqual(sorted(os.path.basename(p) for p in made[-3:]), left)

    def test_default_keep_limit_applied_automatically(self):
        """apply_provider 每次备份都会自动 prune，不会无限堆积明文 token。"""
        self.assertEqual(core.BACKUP_KEEP, 30)
        for _ in range(core.BACKUP_KEEP + 5):
            core.backup_config(self.config_path, self.backup, "openai")
        self.assertEqual(len(os.listdir(self.backup)), core.BACKUP_KEEP)

    def test_prune_ignores_foreign_files(self):
        os.makedirs(self.backup, exist_ok=True)
        keep_me = os.path.join(self.backup, "notes.txt")
        with open(keep_me, "w", encoding="utf-8") as handle:
            handle.write("x")
        for _ in range(4):
            core.backup_config(self.config_path, self.backup, "openai")
        core.prune_backups(self.backup, keep=1)
        self.assertTrue(os.path.isfile(keep_me))

    def test_rejected_provider_does_not_consume_backup_slot(self):
        """被自检拒绝的切换，一个文件都不该落（qa 的 N1）。

        旧顺序是「先备份再校验」，于是每次点坏配置都会多一份备份并触发
        prune；反复点下来 30 个备份位会被同一份内容逐步占满，
        真正有用的历史备份被挤掉。
        """
        # 先做一次合法切换，产生且只产生 1 份备份
        core.apply_provider(self.config_path, official_provider(),
                            self.profiles, self.backup, "deepseek")
        before = sorted(os.listdir(self.backup))
        self.assertEqual(len(before), 1)

        bad_providers = [
            custom_provider(id="a b"),          # id 含空格
            custom_provider(id="a.b"),          # id 含点号
            custom_provider(id="a/b"),          # id 含斜杠
            custom_provider(id="../etc"),       # 路径穿越
            custom_provider(base_url=""),       # 空 base_url
            custom_provider(base_url="ftp://x/"),  # 错协议
            custom_provider(wire_api="chat"),   # 协议不被支持
        ]
        config_before = core.read_text(self.config_path)
        for provider in bad_providers:
            with self.assertRaises(core.ConfigValidationError):
                core.apply_provider(self.config_path, provider,
                                    self.profiles, self.backup, "openai")

        after = sorted(os.listdir(self.backup))
        self.assertEqual(after, before, "被拒绝的切换不该消耗备份位")
        # 顺手确认 config 一字未动、也没留下任何 models 文件
        self.assertEqual(core.read_text(self.config_path), config_before)
        self.assertEqual(
            [n for n in os.listdir(self.profiles) if n.startswith("models.")], [])


# ---------------------------------------------------------------- providers.json
class TestProvidersFile(TempDirTest):
    def test_roundtrip(self):
        path = os.path.join(self.tmp, "providers.json")
        data = {"version": 2, "active": "deepseek",
                "providers": [official_provider(), custom_provider()]}
        core.save_providers(path, data)
        loaded = core.load_providers(path)
        self.assertEqual(len(loaded["providers"]), 2)
        self.assertEqual(loaded["active"], "deepseek")
        self.assertEqual(loaded["providers"][1]["base_url"],
                         "https://api.deepseek.com/")

    def test_load_missing_returns_empty(self):
        loaded = core.load_providers(os.path.join(self.tmp, "nope.json"))
        self.assertEqual(loaded["providers"], [])

    def test_load_corrupted_returns_empty(self):
        path = os.path.join(self.tmp, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(core.load_providers(path)["providers"], [])

    def test_upsert_replaces_existing(self):
        data = {"providers": [custom_provider()]}
        updated, created = core.upsert_provider(
            data, custom_provider(model="deepseek-v4-pro"))
        self.assertFalse(created)
        self.assertEqual(len(updated["providers"]), 1)
        self.assertEqual(updated["providers"][0]["model"], "deepseek-v4-pro")

    def test_upsert_appends_new(self):
        data = {"providers": [official_provider()]}
        updated, created = core.upsert_provider(data, custom_provider())
        self.assertTrue(created)
        self.assertEqual(len(updated["providers"]), 2)

    def test_remove_provider_switches_active_to_official(self):
        data = {"active": "deepseek",
                "providers": [official_provider(), custom_provider()]}
        updated = core.remove_provider(data, "deepseek")
        self.assertEqual(len(updated["providers"]), 1)
        self.assertEqual(updated["active"], "openai")

    def test_remove_official_is_forbidden(self):
        data = {"providers": [official_provider()]}
        with self.assertRaises(ValueError):
            core.remove_provider(data, "openai")

    def test_duplicate_ids_are_deduplicated(self):
        data = {"providers": [custom_provider(), custom_provider()]}
        normalized = core.normalize_providers(data)
        ids = [item["id"] for item in normalized["providers"]]
        self.assertEqual(ids, ["deepseek", "deepseek-2"])

    def test_next_color_picks_unused(self):
        data = {"providers": [{"color": core.PALETTE[0]}]}
        self.assertEqual(core.next_color(data), core.PALETTE[1])


class TestSeedProviders(TempDirTest):
    def test_seed_inherits_key_from_existing_config(self):
        presets = [
            {"id": "openai", "name": "官方 GPT", "kind": "official",
             "model": "gpt-6-luna"},
            {"id": "deepseek", "name": "DeepSeek", "kind": "custom",
             "base_url": "https://api.deepseek.com/", "model": "deepseek-flash"},
        ]
        data = core.seed_providers(presets, SAMPLE_CONFIG)
        self.assertEqual(len(data["providers"]), 2)
        deepseek = core.find_provider(data, "deepseek")
        self.assertEqual(deepseek["api_key"], "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(deepseek["base_url"], "https://api.deepseek.com/")

    def test_seed_active_follows_config(self):
        presets = [
            {"id": "openai", "name": "官方 GPT", "kind": "official"},
            {"id": "deepseek", "name": "DeepSeek", "kind": "custom"},
        ]
        data = core.seed_providers(presets, SAMPLE_CONFIG)
        self.assertEqual(data["active"], "deepseek")

    def test_seed_without_config_defaults_to_official(self):
        presets = [
            {"id": "openai", "name": "官方 GPT", "kind": "official"},
            {"id": "deepseek", "name": "DeepSeek", "kind": "custom"},
        ]
        data = core.seed_providers(presets, "")
        self.assertEqual(data["active"], "openai")
        self.assertEqual(core.find_provider(data, "deepseek")["api_key"], "")

    def test_seed_also_inherits_other_providers_from_config(self):
        """config 里除 deepseek 之外的 provider 段也要被认领进来。

        否则从旧版升级、或手工配过别家的用户，首次打开会看不到自己配过的厂商。
        """
        presets = [
            {"id": "openai", "name": "官方 GPT", "kind": "official",
             "model": "gpt-6-luna"},
            {"id": "deepseek", "name": "DeepSeek", "kind": "custom",
             "base_url": "https://api.deepseek.com/", "model": "deepseek-flash"},
        ]
        config = SAMPLE_CONFIG + (
            '\n[model_providers.kimi-code]\n'
            'name = "kimi-code"\n'
            'base_url = "https://api.kimi.com/coding/v1/"\n'
            'wire_api = "responses"\n'
            'experimental_bearer_token = "sk-kimi-placeholder"\n'
        )
        data = core.seed_providers(presets, config)
        ids = [p["id"] for p in data["providers"]]
        self.assertIn("kimi-code", ids)
        kimi = core.find_provider(data, "kimi-code")
        self.assertEqual(kimi["api_key"], "sk-kimi-placeholder")
        self.assertEqual(kimi["base_url"], "https://api.kimi.com/coding/v1/")
        self.assertEqual(kimi["wire_api"], "responses")

    def test_seed_skips_provider_section_without_base_url(self):
        """没有 base_url 的段没法用，不应被认领进来。"""
        presets = [{"id": "openai", "name": "官方 GPT", "kind": "official"}]
        config = SAMPLE_CONFIG + '\n[model_providers.broken]\nname = "broken"\n'
        data = core.seed_providers(presets, config)
        ids = [p["id"] for p in data["providers"]]
        self.assertNotIn("broken", ids)

    def test_load_or_seed_creates_file(self):
        path = os.path.join(self.tmp, "providers.json")
        config = os.path.join(self.tmp, "config.toml")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write(SAMPLE_CONFIG)
        presets = [{"id": "openai", "name": "官方 GPT", "kind": "official"},
                   {"id": "deepseek", "name": "DeepSeek", "kind": "custom"}]
        data = core.load_or_seed_providers(path, config, presets)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(len(data["providers"]), 2)
        self.assertFalse(data.get("recovered_from"))

    def test_corrupt_file_is_backed_up_not_silently_reset(self):
        """JSON 坏掉时不能无声重置：原文件必须改名留档，并给出 recovered_from。

        否则用户手工配的 5 家厂商（含 Key）会在一瞬间全没了，且毫无提示。
        """
        path = os.path.join(self.tmp, "providers.json")
        broken = '{"version": 2, "providers": [ {"id": "a", '
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(broken)

        data = core.load_or_seed_providers(path, self.config_path, self.presets)
        recovered = data.get("recovered_from") or ""
        self.assertTrue(recovered, "必须返回 recovered_from 让上层能弹窗告知")
        self.assertTrue(os.path.isfile(recovered), "坏文件必须被改名保留")
        self.assertIn(".corrupt-", os.path.basename(recovered))
        with open(recovered, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), broken)
        # 新文件已正常生成
        self.assertTrue(os.path.isfile(path))
        self.assertGreaterEqual(len(data["providers"]), 2)

    def test_valid_but_empty_file_is_not_reported_as_corrupt(self):
        path = os.path.join(self.tmp, "providers.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"version": 2, "active": "", "providers": []}')
        data = core.load_or_seed_providers(path, self.config_path, self.presets)
        self.assertFalse(data.get("recovered_from"))
        self.assertGreaterEqual(len(data["providers"]), 2)

    def test_existing_good_file_is_untouched(self):
        path = os.path.join(self.tmp, "providers.json")
        core.save_providers(path, {"version": 2, "active": "deepseek",
                                   "providers": [official_provider(),
                                                 custom_provider()]})
        before = core.read_text(path)
        data = core.load_or_seed_providers(path, self.config_path, self.presets)
        self.assertFalse(data.get("recovered_from"))
        self.assertEqual(core.read_text(path), before)

    def test_corrupt_backup_name_has_microsecond_stamp(self):
        """时间戳必须到微秒，否则同一秒内连续两次损坏会撞名、move 失败。"""
        path = os.path.join(self.tmp, "providers.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        first = core.load_or_seed_providers(path, self.config_path, self.presets)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken again")
        second = core.load_or_seed_providers(path, self.config_path, self.presets)
        name_a = os.path.basename(first.get("recovered_from") or "")
        name_b = os.path.basename(second.get("recovered_from") or "")
        self.assertNotEqual(name_a, name_b)
        self.assertRegex(name_a, r"\.corrupt-\d{8}-\d{6}-\d{6}$")


# ---------------------------------------------------------------- 其它纯函数
class TestMisc(TempDirTest):
    def test_detect_active_id_from_config(self):
        providers = [official_provider(), custom_provider()]
        self.assertEqual(core.detect_active_id(providers, SAMPLE_CONFIG), "deepseek")

    def test_detect_active_id_official_when_no_provider_key(self):
        providers = [official_provider(), custom_provider()]
        text = 'model = "gpt-6-luna"\n'
        self.assertEqual(core.detect_active_id(providers, text), "openai")

    def test_extract_provider_sections(self):
        info = core.extract_provider_sections(SAMPLE_CONFIG)
        self.assertIn("deepseek", info)
        self.assertEqual(info["deepseek"]["wire_api"], "responses")
        self.assertEqual(info["deepseek"]["base_url"], "https://api.deepseek.com/")

    def test_build_models_doc_single_entry(self):
        skeleton = core.load_models_skeleton()
        doc = core.build_models_doc(skeleton, custom_provider(model="kimi-k2.6"))
        self.assertEqual(len(doc["models"]), 1)
        self.assertEqual(doc["models"][0]["slug"], "kimi-k2.6")
        self.assertEqual(doc["models"][0]["priority"], 1)

    def test_models_skeleton_keeps_original_fields(self):
        # 骨架字段集必须原样保留，不能只留少数几个 key
        skeleton = core.load_models_skeleton()
        self.assertIn("context_window", skeleton)
        self.assertIn("model_messages", skeleton)
        self.assertIn("supported_reasoning_levels", skeleton)

    def test_models_doc_disables_search_tool(self):
        """unsupported 的工具声明会把请求打回 400。

        实际踩到：supports_search_tool=True 时 Kimi 返回
        invalid_request_error: tools.13: tool type "tool_search" is not supported
        """
        doc = core.build_models_doc(core.load_models_skeleton(),
                                    custom_provider(model="kimi-k3"))
        self.assertIs(doc["models"][0]["supports_search_tool"], False)

    def test_models_doc_never_writes_null_web_search_tool_type(self):
        """web_search_tool_type 只接受 string 或 map，写成 null 会让 Codex 起不来。

        实际踩到：`failed to parse model_catalog_json ... invalid type: null`，
        应用直接无法启动。这里锁住「不能是 None」。
        """
        doc = core.build_models_doc(core.load_models_skeleton(),
                                    custom_provider(model="kimi-k3"))
        entry = doc["models"][0]
        self.assertIsNotNone(entry.get("web_search_tool_type"))
        self.assertIsInstance(entry["web_search_tool_type"], str)

    def test_split_config_ignores_array_brackets(self):
        head, sections = core.split_config(SAMPLE_CONFIG)
        self.assertTrue(head.startswith("check_for_update_on_startup"))
        self.assertTrue(sections.startswith("[features]"))
        self.assertIn("notify = [", head)

    def test_find_provider(self):
        data = {"providers": [official_provider(), custom_provider()]}
        self.assertEqual(core.find_provider(data, "deepseek")["name"], "DeepSeek")
        self.assertIsNone(core.find_provider(data, "nope"))

    def test_load_presets_from_shipped_file(self):
        presets = core.load_presets(os.path.join(ROOT, "presets.json"))
        self.assertGreaterEqual(len(presets), 8)
        ids = [item.get("id") for item in presets]
        self.assertIn("openai", ids)
        self.assertIn("deepseek", ids)
        for item in presets:
            self.assertTrue(item.get("name"))
            self.assertIn(item.get("kind"), ("official", "custom"))

    def test_probe_rejects_empty_base_url(self):
        status, detail, wire = core.probe_provider("", "sk-x", "m", timeout=1)
        self.assertEqual(status, "fail")
        self.assertIsNone(wire)


# ---------------------------------------------------------------- 探测：原有分支回归
class ProbeTestBase(unittest.TestCase):
    """探测用例的公共脚手架：全程伪造 _http_post，不联网。"""

    def probe(self, responses, model=PROBE_MODEL, base_url=PROBE_BASE,
              api_key=PROBE_KEY):
        fake = FakeHttp(responses)
        with mock.patch.object(core, "_http_post", fake):
            status, detail, wire = core.probe_provider(
                base_url, api_key, model, timeout=1)
        return status, detail, wire, fake


class TestProbeExistingBranches(ProbeTestBase):
    """12 条原有探测分支的回归锁 —— 修「模型名误报」时一条都不能动。

    这些分支以前没有用例覆盖（只在人工点界面时验过），这里补齐，
    保证本次改动不会顺手把「400 参数差异判 ok」「404 回落 chat 探测」
    之类的行为改坏。
    """

    def test_200_is_ok_responses(self):
        status, _detail, wire, fake = self.probe(
            {"responses": (200, '{"id":"resp_1","output":[]}')})
        self.assertEqual((status, wire), ("ok", "responses"))
        self.assertEqual(fake.count(), 0, "200 时不该再去试 chat")

    def test_400_non_model_error_is_ok(self):
        status, detail, wire, _fake = self.probe(
            {"responses": (400, '{"error":"max_output_tokens too large"}')})
        self.assertEqual((status, wire), ("ok", "responses"))
        self.assertIn("400", detail)

    def test_401_is_auth_fail(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (401, '{"error":"invalid api key"}')})
        self.assertEqual((status, wire), ("auth_fail", None))

    def test_403_is_auth_fail(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (403, '{"error":"forbidden"}')})
        self.assertEqual((status, wire), ("auth_fail", None))

    def test_429_is_warn(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (429, '{"error":"rate limited"}')})
        self.assertEqual((status, wire), ("warn", "responses"))

    def test_404_with_chat_200_warns_chat_only(self):
        status, detail, wire, fake = self.probe({
            "responses": (404, "not found"),
            "chat/completions": (200, '{"choices":[]}'),
        })
        self.assertEqual((status, wire), ("warn", "chat"))
        self.assertIn("chat", detail)
        self.assertEqual(fake.count(), 1)

    def test_405_with_chat_200_warns_chat_only(self):
        status, _detail, wire, _fake = self.probe({
            "responses": (405, "method not allowed"),
            "chat/completions": (200, '{"choices":[]}'),
        })
        self.assertEqual((status, wire), ("warn", "chat"))

    def test_404_with_chat_404_fails_endpoint_missing(self):
        status, detail, wire, fake = self.probe({
            "responses": (404, "not found"),
            "chat/completions": (404, "not found"),
        })
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("端点不存在", detail)
        self.assertEqual(fake.count(), 1)

    def test_404_with_chat_401_is_auth_fail(self):
        status, _detail, wire, _fake = self.probe({
            "responses": (404, "not found"),
            "chat/completions": (401, '{"error":"invalid api key"}'),
        })
        self.assertEqual((status, wire), ("auth_fail", None))

    def test_500_and_502_are_fail(self):
        for code in (500, 502, 503, 504):
            status, detail, wire, _fake = self.probe(
                {"responses": (code, "server error")})
            self.assertEqual((status, wire), ("fail", None), code)
            self.assertIn(str(code), detail)

    def test_unexpected_status_is_fail(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (418, "i am a teapot")})
        self.assertEqual((status, wire), ("fail", None))

    def test_connection_refused_is_fail(self):
        status, detail, wire, _fake = self.probe(
            {"responses": raise_net_error})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("无法连接", detail)

    def test_generic_exception_is_fail_not_crash(self):
        status, detail, wire, _fake = self.probe(
            {"responses": raise_plain_error})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("无法连接", detail)

    def test_empty_base_url_is_fail_without_any_request(self):
        fake = FakeHttp({"responses": (200, "{}")})
        with mock.patch.object(core, "_http_post", fake):
            status, _detail, wire = core.probe_provider("", PROBE_KEY, PROBE_MODEL)
        self.assertEqual((status, wire), ("fail", None))
        self.assertEqual(fake.calls, [], "空 base_url 不该发任何请求")


# ---------------------------------------------------------------- 探测：模型名错误
class TestProbeModelNameErrors(ProbeTestBase):
    """模型名写错时必须 fail，且不能继续去试 chat 端点。

    修复前：厂商用 400 回 model not found，被一律判成「端点存在、可用」，
    界面绿灯，用户保存后每次请求都失败且毫无线索。
    """

    def test_400_model_not_found_is_fail(self):
        status, detail, wire, fake = self.probe(
            {"responses": (400, '{"error":"model not found"}')})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("模型", detail)
        self.assertEqual(fake.count(), 0)

    def test_400_nested_invalid_model_message_is_fail(self):
        status, detail, wire, _fake = self.probe(
            {"responses": (400, '{"error":{"message":"invalid model: gpt-9"}}')})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("模型", detail)

    def test_400_model_does_not_exist_is_fail(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (400,
                           '{"error":{"message":"The model does not exist"}}')})
        self.assertEqual((status, wire), ("fail", None))

    def test_400_chinese_body_is_fail(self):
        status, detail, wire, _fake = self.probe(
            {"responses": (400, '{"msg":"模型不存在或不可用"}')})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("模型", detail)

    def test_400_openai_style_message_without_code_is_fail(self):
        """真实 OpenAI 措辞：模型名被反引号夹在中间，code 字段可能被裁剪掉。"""
        body = ('{"error":{"message":"The model `gpt-9` does not exist '
                'or you do not have access to it.","type":"invalid_request_error"}}')
        status, detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("模型", detail)

    def test_404_model_error_skips_chat_probe(self):
        """404 + 模型名错误：必须直接 fail，不能去试 chat。

        否则「模型名错」会被误报成「Base URL 错」或「只支持 chat」，
        用户会去改 Base URL，越改越偏。
        """
        status, detail, wire, fake = self.probe(
            {"responses": (404, '{"error":"model not found"}')})
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("模型", detail)
        self.assertEqual(fake.count(), 0,
                         "查明是模型名问题后不该再请求 chat 端点")
        self.assertEqual(len(fake.calls), 1)

    def test_405_model_error_skips_chat_probe(self):
        status, _detail, wire, fake = self.probe(
            {"responses": (405, '{"error":"unknown model"}')})
        self.assertEqual((status, wire), ("fail", None))
        self.assertEqual(fake.count(), 0)

    def test_404_non_model_error_still_probes_chat(self):
        status, _detail, wire, fake = self.probe({
            "responses": (404, "<html>404 page not found</html>"),
            "chat/completions": (200, '{"choices":[]}'),
        })
        self.assertEqual((status, wire), ("warn", "chat"))
        self.assertEqual(fake.count(), 1, "非模型错误的 404 仍要回落 chat 探测")

    def test_detail_carries_the_model_name_user_typed(self):
        status, detail, _wire, _fake = self.probe(
            {"responses": (400, '{"error":"model not found"}')},
            model="gpt-9-typo")
        self.assertEqual(status, "fail")
        self.assertIn("gpt-9-typo", detail, "文案要带上用户填的模型名")

    def test_model_name_goes_through_redact(self):
        """模型名是用户输入，拼进文案前要过 redact。

        真实场景：用户手滑把 Key 粘进了模型名输入框。这时详情文案里
        不能把 Key 原样显示出来（沿用全模块「面向用户的文本绝不带密钥」的约定）。
        """
        key = "sk-0123456789abcdef"
        status, detail, _wire, _fake = self.probe(
            {"responses": (400, '{"error":"model not found"}')},
            model=key, api_key=key)
        self.assertEqual(status, "fail")
        self.assertNotIn(key, detail)
        self.assertIn("sk-****def", detail)

    def test_empty_model_name_does_not_crash(self):
        status, detail, wire, _fake = self.probe(
            {"responses": (400, '{"error":"model not found"}')}, model="")
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("模型", detail)

    def test_huge_html_body_is_not_misjudged(self):
        """200KB 的 HTML 错误页：不崩溃、不误判、也不慢。"""
        body = "<html><body>" + ("internal error <br/>" * 10000) + "</body></html>"
        self.assertGreater(len(body), 200 * 1000)
        started = time.time()
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        elapsed = time.time() - started
        self.assertEqual((status, wire), ("ok", "responses"))
        self.assertLess(elapsed, 2.0, "超长响应体不应该拖慢判定")

    def test_huge_xml_body_with_model_error_is_fail(self):
        # 命中字段必须在截断窗口（前 2000 字符）内，否则按设计不判命中
        body = "<Error><Message>model not found</Message>" + \
               ("<Detail>invalid request</Detail>" * 5000) + "</Error>"
        self.assertGreater(len(body), 100 * 1000)
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))


class TestProbeDottedModelNames(ProbeTestBase):
    """带点的模型名：最容易漏判、也最容易被用户打错的一批（关键验收点）。

    gpt-4.2 / claude-3.5-sonnet / gemini-1.5-pro / qwen2.5 / llama-3.1 ——
    如果把点号当句子边界，这些报文会全部漏判成绿灯，而用户打错模型名时
    厂商返回的正是这种句子。
    """

    def test_dotted_gpt_with_access_clause_is_fail(self):
        body = ('The model `gpt-4.2` does not exist '
                'or you do not have access to it.')
        status, detail, wire, _fake = self.probe(
            {"responses": (400, body)}, model="gpt-4.2")
        self.assertEqual((status, wire), ("fail", None))
        self.assertIn("gpt-4.2", detail)

    def test_claude_dotted_version_is_fail(self):
        body = 'The model `claude-3.5-sonnet-20241022` does not exist'
        status, _detail, wire, _fake = self.probe(
            {"responses": (400, body)}, model="claude-3.5-sonnet-20241022")
        self.assertEqual((status, wire), ("fail", None))

    def test_plural_models_with_slash_is_fail(self):
        body = "models/gemini-1.5-pro is not found"
        status, _detail, wire, _fake = self.probe(
            {"responses": (400, body)}, model="gemini-1.5-pro")
        self.assertEqual((status, wire), ("fail", None))

    def test_valid_values_tail_is_fail(self):
        """"Valid values: ..." 里的 value 不能把 strong 降级。"""
        body = 'The model `gpt-9` does not exist. Valid values: gpt-4o, gpt-4o-mini.'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))

    def test_check_the_value_tail_is_fail(self):
        body = ("The model 'gpt-9' does not exist. "
                "Please check the value and try again.")
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))

    def test_model_value_prefix_is_fail(self):
        body = "The model value 'gpt-9' does not exist"
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))

    def test_invalid_model_name_is_fail(self):
        body = "invalid model name: gpt-9"
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))

    def test_invalid_model_id_is_fail(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (400, "Invalid model id")})
        self.assertEqual((status, wire), ("fail", None))

    def test_invalid_model_version_is_fail(self):
        status, _detail, wire, _fake = self.probe(
            {"responses": (400, "invalid model version 2")})
        self.assertEqual((status, wire), ("fail", None))

    def test_pretty_printed_json_with_newline_is_fail(self):
        """pretty-print 的 JSON 里有换行，换行不能当句子边界。"""
        body = '{\n  "error": {\n    "message": "The model `gpt-4.2` does not exist"\n  }\n}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))

    def test_chinese_model_directory_is_ok(self):
        """「模型目录」是模型仓库，不是模型名。"""
        body = '{"error":"模型目录不存在"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))


class TestProbeFalsePositiveGuards(ProbeTestBase):
    """反方向的红线：正常的参数错误 / 正常端点不能被误判成「模型名错误」。

    误伤的伤害和原 Bug 相反但同样重：probe 结果会落盘到 providers.json 的
    last_probe.ok，一个本来正确的厂商会长期顶着红灯，用户跑去改一个好端端
    的模型名。所以这一组全部断言**不**判 fail。
    """

    def test_param_negation_before_model_is_ok(self):
        """否定词在 model 前面 → 说的是参数，不是模型名。"""
        body = '{"error":"parameter temperature does not exist for model gpt-4o"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_azure_style_max_tokens_is_ok(self):
        body = ('{"error":"The value for \'max_tokens\' does not exist '
                'in the model schema"}')
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_chinese_param_error_is_ok(self):
        """「模型参数 temperature 不存在」：模型 → 不存在 隔了 17 字，不算模型名错。"""
        body = '{"error":"模型参数 temperature 不存在"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_invalid_model_parameter_value_is_ok(self):
        """`invalid model` 后面跟的是 parameter value，不是模型名。"""
        body = '{"error":"invalid model parameter value: temperature must be <= 2"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_chinese_model_list_endpoint_unavailable_is_ok(self):
        """「模型列表接口不可用」是接口挂了，不影响推理，不能判模型名错。"""
        body = '{"error":"模型列表接口暂时不可用，不影响推理"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_short_chinese_model_param_error_is_ok(self):
        body = '{"error":"模型参数不存在"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_404_page_mentioning_models_still_probes_chat(self):
        """404 页面里的 /models 链接不能被当成模型名错误。

        这类页面必须继续去试 chat，才能落到「该厂商只支持 chat」这个
        正确结论；跳过探测会把用户引到改模型名的死路上。
        """
        body = ('The requested URL does not exist. '
                'Docs: <a href="/models">/models</a>')
        status, _detail, wire, fake = self.probe({
            "responses": (404, body),
            "chat/completions": (200, '{"choices":[]}'),
        })
        self.assertEqual((status, wire), ("warn", "chat"))
        self.assertEqual(fake.count(), 1, "404 页面提到 models 时仍必须试 chat")
        self.assertTrue(any(u.endswith("chat/completions") for u in fake.calls))

    def test_weak_signal_gives_warn_not_fail(self):
        """只能推断的情况给 warn，绝不能判 fail、也不能声称端点有问题。"""
        body = '{"error":"模型 GPT-9 不存在"}'
        status, detail, wire, _fake = self.probe(
            {"responses": (400, body)}, model="GPT-9")
        self.assertEqual((status, wire), ("warn", "responses"))
        self.assertIn("疑似", detail)
        self.assertIn("GPT-9", detail, "文案要带上用户填的模型名")

    def test_weak_signal_on_404_still_probes_chat(self):
        body = '{"error":"模型 GPT-9 不存在"}'
        status, _detail, wire, fake = self.probe({
            "responses": (404, body),
            "chat/completions": (200, '{"choices":[]}'),
        })
        self.assertEqual((status, wire), ("warn", "chat"))
        self.assertEqual(fake.count(), 1, "weak 不能跳过 chat 探测")

    def test_400_invalid_api_key_is_auth_fail(self):
        """400 + Key 错：不该显示绿灯（这是改前就有的老毛病）。"""
        for body in ('{"error":"invalid api key"}',
                     '{"error":"Incorrect API key provided"}',
                     '{"error":{"code":"invalid_api_key"}}',
                     '{"msg":"API 密钥无效"}'):
            status, _detail, wire, _fake = self.probe({"responses": (400, body)})
            self.assertEqual((status, wire), ("auth_fail", None), body)

    def test_marker_between_2000_and_4096_is_detected(self):
        """截断窗口必须与 _http_post 的 exc.read(4096) 对齐。

        2152 字符左右的冗长错误体（云厂商把字段清单排在 message 前面）
        以前正好落在 2000~4096 的盲区里，又被判成绿灯。
        """
        body = ("x" * 2134) + '{"error":"model not found"}'
        position = body.index("model not found")
        self.assertGreater(position, 2000)
        self.assertLess(position, 4096)
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("fail", None))

    def test_marker_beyond_read_limit_is_ignored(self):
        """4096 之后不再看：_http_post 本来也只读 4096，不会再有这种 body。"""
        body = ("x" * 5000) + '{"error":"model not found"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("ok", "responses"))

    def test_en_model_offtopic_is_ok(self):
        """model 后面紧跟的是 list / endpoint 这类词 → 说的是 API 面，不是模型名。"""
        for body in ('model list endpoint is not available',
                     'the model docs page does not exist',
                     'model catalog is not available'):
            status, _detail, wire, _fake = self.probe({"responses": (400, body)})
            self.assertEqual((status, wire), ("ok", "responses"), body)

    def test_plain_model_unavailable_still_warns(self):
        """真正的「模型不可用」不能被离题排除顺手放过，仍要 warn 兜底。"""
        status, _detail, wire, _fake = self.probe(
            {"responses": (400, "model gpt-4o is currently unavailable")})
        self.assertEqual((status, wire), ("warn", "responses"))

    def test_dotted_model_unavailable_warns(self):
        """weak 层与 strong 层必须用同一套点号规则：带点模型名也要 warn。"""
        for body in ("model gpt-4.2 is not available",
                     "model qwen2.5 is not supported",
                     "model llama-3.1 is unavailable"):
            status, detail, wire, _fake = self.probe({"responses": (400, body)})
            self.assertEqual((status, wire), ("warn", "responses"), body)
            self.assertIn("疑似", detail)

    def test_plain_model_unavailable_without_dot_warns(self):
        """无点的写法不能因为换正则而回归。"""
        status, _detail, wire, _fake = self.probe(
            {"responses": (400, "model gpt-4o is unavailable")})
        self.assertEqual((status, wire), ("warn", "responses"))

    def test_key_error_wins_over_model_error(self):
        """同时出现 Key 错与 model not found 时先报 auth_fail（Key 坏时模型报错是假象）。"""
        body = '{"error":"invalid api key","detail":"model not found"}'
        status, _detail, wire, _fake = self.probe({"responses": (400, body)})
        self.assertEqual((status, wire), ("auth_fail", None))


class TestLooksLikeModelError(unittest.TestCase):
    """_looks_like_model_error 的白色接线测试（纯函数，不涉及网络）。"""

    def test_recognises_common_phrasings(self):
        samples = [
            '{"error":"model not found"}',
            '{"error":{"message":"Model Does Not Exist"}}',
            '{"code":"model_not_found"}',
            '{"error":"no such model: foo"}',
            '{"error":"unknown model gpt-9"}',
            '{"error":"invalid model: gpt-9"}',
            '{"error":"the model is not available"}',
            '{"error":"you do not have access to model gpt-5"}',
            '{"error":"does not have access to model `claude`"}',
            '{"error":"you do not have access to model gpt-5"}',
            '{"error":{"message":"The model `gpt-9` does not exist"}}',
            '{"msg":"模型不存在"}',
            '{"msg":"模型不存在或不可用"}',
            '{"msg":"该模型不可用"}',
        ]
        for text in samples:
            self.assertTrue(core._looks_like_model_error(text), text)

    def test_rejects_unrelated_bodies(self):
        samples = [
            "",
            None,
            '{"error":"max_output_tokens too large"}',
            '{"error":"invalid api key"}',
            '{"error":"messages: field required"}',
            "<html><body>502 Bad Gateway</body></html>",
            '{"error":"invalid_request_error: unknown parameter"}',
            # 以下 5 条是「参数错误」而不是「模型名错误」，必须一条都不命中
            '{"error":"parameter temperature does not exist for model gpt-4o"}',
            '{"error":"The value for \'max_tokens\' does not exist in the model schema"}',
            '{"error":"模型参数 temperature 不存在"}',
            '{"error":"invalid model parameter value: temperature must be <= 2"}',
            '{"error":"模型列表接口暂时不可用，不影响推理"}',
            'The requested URL does not exist. Docs: <a href="/models">/models</a>',
        ]
        for text in samples:
            self.assertFalse(core._looks_like_model_error(text), repr(text))

    def test_level_separates_strong_from_weak(self):
        """明确指向模型名的判 strong，需要推断的判 weak，参数错误判空。"""
        self.assertEqual(core._model_error_level('{"error":"model not found"}'), "strong")
        self.assertEqual(core._model_error_level('{"msg":"模型不存在"}'), "strong")
        self.assertEqual(core._model_error_level('{"msg":"模型 GPT-9 不存在"}'), "weak")
        self.assertEqual(core._model_error_level('{"error":"模型列表不可用"}'), "")
        self.assertEqual(
            core._model_error_level('{"error":"model gpt-9 is currently unavailable"}'),
            "weak")
        self.assertEqual(core._model_error_level(""), "")
        self.assertEqual(core._model_error_level(None), "")

    def test_truncates_before_matching(self):
        """先截断再匹配：正文深处偶然出现的短语不该被当真。"""
        body = "<html>" + ("x" * 200000) + "model not found</html>"
        started = time.time()
        self.assertFalse(core._looks_like_model_error(body))
        self.assertLess(time.time() - started, 1.0)
        # 命中短文案时（在截断窗口内）依然要判 True
        self.assertTrue(core._looks_like_model_error("model not found" + "x" * 200000))


# ---------------------------------------------------------------- 源码级回归守卫
class TestUiSourceGuards(unittest.TestCase):
    """不需要起 tkinter 的静态检查：防止「密钥泄露到界面」的回归。

    qa 发现 main.pyw 里有两处 `"探测异常：%s" % exc` 没过 redact（另一处过了），
    属于「同一个错误在两处复制粘贴、只改了一处」。这种回归用运行时测试很难覆盖，
    用源码扫描反而最直接。
    """

    def setUp(self):
        with open(os.path.join(ROOT, "main.pyw"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_probe_error_text_is_always_redacted(self):
        positions = [m.start() for m in re.finditer("探测异常", self.source)]
        self.assertTrue(positions, "main.pyw 里应该能找到「探测异常」")
        for pos in positions:
            window = self.source[max(0, pos - 160):pos + 260]
            self.assertIn("redact", window,
                          "main.pyw 中「探测异常」附近没有 redact，可能把密钥写进界面：\n"
                          + self.source[max(0, pos - 120):pos + 180])

    def test_every_probe_job_has_exception_guard(self):
        """两个探测入口（对话框内 / 主界面）都必须有 except 兜底，
        否则线程静默崩掉，用户看到按钮永远停在「测试中…」。"""
        self.assertEqual(
            len(re.findall(r"except Exception as exc:", self.source)) >= 2, True)
        for marker in ("def _on_test_done", "def _on_probe_done"):
            self.assertIn(marker, self.source)

    def test_delete_active_provider_syncs_config(self):
        """删除「当前生效」的厂商时，必须同步改写 config.toml。

        remove_provider 只会把 providers.json 的 active 切到官方 GPT，
        config.toml 仍指着刚删掉的厂商。不同步的话会出现
        「界面显示官方 GPT，Codex 实际还在用已删除的配置」，
        用户完全看不出两边已经不一致。
        """
        start = self.source.index("def delete_provider")
        seg = self.source[start:start + 3200]
        self.assertIn("was_active", seg, "delete_provider 里没有判断删的是不是当前生效的厂商")
        self.assertIn("apply_provider", seg,
                      "删除当前生效的厂商后没有同步 config.toml，界面与 Codex 会不一致")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""
Codex 模型切换器 v2 · 入口与全部界面
============================================================
把 ~/.codex/config.toml 在各家模型厂商之间切换，配置自动改写并备份。

与 v1 的区别：
  * 不再写死「官方 GPT / DeepSeek」两个模板，改为读 providers.json，
    清单里有多少家就能切多少家
  * 支持在界面里新增 / 编辑 / 删除厂商
  * 内置常见厂商预设，只需填 Key
  * 能「测试连接」，真的发一次最小请求，判断 Key 是否可用、
    以及该厂商到底支不支持 /responses 协议
  * 保留 v1 的能力：自动备份、切换后重启 Codex、网络体检、DPI 适配、错误日志

依赖：Python 3.8 标准库（tkinter），零第三方包。
"""

import os
import sys
import threading
import datetime
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

# 把本文件所在目录放进 sys.path，保证 pythonw 启动时也能 import core
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

ERROR_LOG = os.path.join(BASE_DIR, "last_error.log")


def fatal(message):
    """pythonw 下没有控制台，出错时写日志并弹原生对话框。"""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as handle:
            handle.write("%s\n%s\n%s\n" % ("=" * 60,
                                           datetime.datetime.now().isoformat(),
                                           message))
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, str(message)[-800:], "Codex 模型切换器 · 出错了", 0x10)
    except Exception:
        pass


try:
    import core
except Exception:
    fatal("无法加载 core.py，请确认它与 main.pyw 在同一个目录。\n\n%s"
          % traceback.format_exc())
    raise


# ---------------------------------------------------------------- 视觉常量
BG = "#1b1d23"
CARD = "#252832"
CARD_HI = "#2f3442"
CARD_LINE = "#2e323f"
FIELD_BG = "#15171c"
FG = "#e8eaf0"
FG_DIM = "#9aa0b0"
FG_FAINT = "#5a6072"
OK = "#3ddc97"
WARN = "#ffb454"
BAD = "#ff6b6b"
ACCENT = "#4d6bfe"
LINK = "#6ea8ff"
LINK_HI = "#9cc4ff"

WIN_W = 760
WIN_H = 600
LIST_W = 232
MAX_LOG_LINES = 300

# 这些预设的 id 太笼统，不适合直接当某个具体厂商的 id，
# 遇到它们就改从 Base URL 的域名生成。
GENERIC_PRESET_IDS = ("custom", "custom-template")

_S = 1.0


def init_dpi():
    """让界面在 125% / 150% 缩放下保持清晰且尺寸合适。必须在建窗口之前调。"""
    global _S
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
            ctypes.windll.user32.ReleaseDC(0, hdc)
        _S = max(1.0, float(dpi) / 96.0)
    except Exception:
        _S = 1.0


def S(value):
    """逻辑像素 → 物理像素。"""
    return int(round(value * _S))


# ---------------------------------------------------------------- 自绘控件
class FlatButton(tk.Label):
    """用 Label 自绘的扁平按钮，方便完全控制深色配色与禁用态。"""

    def __init__(self, master, text, command, bg=CARD, fg=FG, hover=CARD_HI,
                 font=None, padx=14, pady=7):
        tk.Label.__init__(self, master, text=text, bg=bg, fg=fg, font=font,
                          cursor="hand2", padx=S(padx), pady=S(pady), bd=0)
        self._command = command
        self._bg = bg
        self._hover = hover
        self._fg = fg
        self._enabled = True
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_click(self, _event):
        if self._enabled and callable(self._command):
            self._command()

    def _on_enter(self, _event):
        if self._enabled:
            self.configure(bg=self._hover)

    def _on_leave(self, _event):
        if self._enabled:
            self.configure(bg=self._bg)

    def set_enabled(self, enabled, text=None):
        self._enabled = bool(enabled)
        self.configure(bg=self._bg if enabled else CARD,
                       fg=self._fg if enabled else FG_FAINT,
                       cursor="hand2" if enabled else "arrow")
        if text is not None:
            self.configure(text=text)

    def set_text(self, text):
        self.configure(text=text)


class ScrollFrame(tk.Frame):
    """Canvas + 内嵌 Frame 的可滚动容器。

    Listbox 无法给每一项定制颜色和两行文字，所以列表必须自己画。
    """

    def __init__(self, master, bg=BG, width=LIST_W):
        tk.Frame.__init__(self, master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                width=S(width), takefocus=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = tk.Scrollbar(self, orient="vertical",
                                      command=self.canvas.yview, width=S(10))
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self._on_scroll)

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # Windows 上 <MouseWheel> 只发给指针底下的那个控件，所以滚动必须
        # 逐个控件绑定；用 Enter/Leave + bind_all 会在指针移进子控件时失效。
        self.attach(self.canvas)
        self.attach(self.inner)
        self._syncing = False

    def attach(self, widget):
        """给某个控件绑定滚轮滚动。列表每次重建后都要对新控件再调一次。"""
        widget.bind("<MouseWheel>", self._on_wheel)

    def refresh(self):
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        self._sync_scrollbar()

    def _sync_scrollbar(self):
        """内容装得下就把滚动条收起来。加锁避免 pack/unpack 引发回调重入。"""
        if self._syncing:
            return
        self._syncing = True
        try:
            first, last = self.canvas.yview()
            visible = not (first <= 0.0 and last >= 1.0)
            mapped = bool(self.scrollbar.winfo_ismapped())
            if visible and not mapped:
                self.scrollbar.pack(side="right", fill="y")
            elif not visible and mapped:
                self.scrollbar.pack_forget()
        except Exception:
            pass
        finally:
            self._syncing = False

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event):
        try:
            first, last = self.canvas.yview()
        except Exception:
            return
        if first <= 0.0 and last >= 1.0:
            return
        steps = int(-1 * (event.delta / 120.0))
        if steps == 0:
            steps = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(steps, "units")


# ---------------------------------------------------------------- 字体
def build_fonts():
    """按用途准备好字体元组，统一交给 tk 的 scaling 去缩放。"""
    return {
        "title": ("Microsoft YaHei UI", 16, "bold"),
        "sub": ("Microsoft YaHei UI", 9),
        "name": ("Microsoft YaHei UI", 10, "bold"),
        "body": ("Microsoft YaHei UI", 10),
        "small": ("Microsoft YaHei UI", 8),
        "tiny": ("Microsoft YaHei UI", 8),
        "mono": ("Consolas", 8),
        "dot": ("Segoe UI", 9),
        "detail": ("Microsoft YaHei UI", 14, "bold"),
        "tag": ("Microsoft YaHei UI", 8),
    }


# ---------------------------------------------------------------- 新增/编辑对话框
class ProviderDialog(tk.Toplevel):
    """新增或编辑一个厂商的模态对话框。保存成功后 self.result 是 provider dict。"""

    def __init__(self, master, app, provider=None, presets=None,
                 existing_ids=None, default_color=ACCENT):
        tk.Toplevel.__init__(self, master, bg=BG)
        self.app = app
        self.presets = presets or []
        self.existing_ids = existing_ids or []
        self.provider = dict(provider) if provider else None
        self.default_color = default_color
        self.result = None
        self._testing = False
        self._preset_guard = False
        self._preset_id = ""
        self.wire_api_note = ""

        editing = self.provider is not None
        self.title("编辑厂商" if editing else "新增厂商")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.f = build_fonts()
        self._build()
        self._load_values()
        self._center(master)
        self.grab_set()
        try:
            self.name_entry.focus_set()
        except Exception:
            pass

    # ------------------------------------------------------------ 布局
    def _style_ttk(self):
        """把 ttk 的下拉框调成深色，否则在深色对话框里会白得刺眼。"""
        try:
            style = ttk.Style(self)
            if "clam" in style.theme_names():
                style.theme_use("clam")
            style.configure(
                "Dark.TCombobox",
                fieldbackground=FIELD_BG, background=CARD, foreground=FG,
                arrowcolor=FG, bordercolor=CARD_LINE, lightcolor=CARD_LINE,
                darkcolor=CARD_LINE, selectbackground=CARD_HI,
                selectforeground=FG, padding=S(3),
            )
            style.map(
                "Dark.TCombobox",
                fieldbackground=[("readonly", FIELD_BG), ("active", FIELD_BG)],
                foreground=[("readonly", FG), ("active", FG)],
                background=[("active", CARD_HI), ("readonly", CARD)],
                selectbackground=[("readonly", FIELD_BG)],
                selectforeground=[("readonly", FG)],
            )
            self.option_add("*TCombobox*Listbox.background", FIELD_BG)
            self.option_add("*TCombobox*Listbox.foreground", FG)
            self.option_add("*TCombobox*Listbox.selectBackground", CARD_HI)
            self.option_add("*TCombobox*Listbox.selectForeground", FG)
        except Exception:
            pass

    def _build(self):
        f = self.f
        wrap_pad = S(20)
        self._style_ttk()

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=wrap_pad, pady=(S(16), S(10)))
        tk.Label(head, text="编辑厂商" if self.provider else "新增厂商",
                 font=("Microsoft YaHei UI", 13, "bold"), fg=FG, bg=BG).pack(anchor="w")
        tk.Label(head, text="填好 Base URL、API Key 与模型名，保存后可立即测试连接",
                 font=f["small"], fg=FG_DIM, bg=BG).pack(anchor="w", pady=(S(2), 0))

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=wrap_pad)

        # 类型
        kind_row = tk.Frame(form, bg=BG)
        kind_row.pack(fill="x", pady=(0, S(8)))
        tk.Label(kind_row, text="类型", font=f["small"], fg=FG_DIM, bg=BG,
                 width=10, anchor="w").pack(side="left")
        self.kind_var = tk.StringVar(value="custom")
        for value, label in (("official", "官方 GPT（账号登录）"),
                             ("custom", "第三方（OpenAI 兼容）")):
            tk.Radiobutton(
                kind_row, text=label, value=value, variable=self.kind_var,
                command=self._sync_kind, font=f["small"], fg=FG, bg=BG,
                activebackground=BG, activeforeground=FG, selectcolor=CARD,
                bd=0, highlightthickness=0, cursor="hand2",
            ).pack(side="left", padx=(0, S(12)))

        # 名称
        self.name_var = tk.StringVar()
        self.name_entry = self._field_row(form, "名称", self.name_var,
                                          "显示在列表里的名字，例如「DeepSeek」")

        # 预设
        preset_row = tk.Frame(form, bg=BG)
        preset_row.pack(fill="x", pady=(0, S(8)))
        tk.Label(preset_row, text="从预设填充", font=f["small"], fg=FG_DIM,
                 bg=BG, width=10, anchor="w").pack(side="left")
        names = [item.get("name") or item.get("id") or "" for item in self.presets]
        self.preset_box = ttk.Combobox(preset_row, values=names, state="readonly",
                                       font=f["small"], width=30,
                                       style="Dark.TCombobox")
        self.preset_box.pack(side="left")
        self.preset_box.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.hint_var = tk.StringVar(value="选一个内置厂商，自动填好 Base URL 与模型名")
        tk.Label(form, textvariable=self.hint_var, font=f["tiny"], fg=FG_FAINT,
                 bg=BG, justify="left", anchor="w", wraplength=S(470)).pack(
            fill="x", padx=(S(76), 0), pady=(0, S(6)))

        # Base URL
        self.url_var = tk.StringVar()
        self.url_entry = self._field_row(
            form, "Base URL", self.url_var,
            "必须以 / 结尾；例如 https://api.xxx.com/v1/")

        # API Key
        key_row = tk.Frame(form, bg=BG)
        key_row.pack(fill="x", pady=(0, S(8)))
        tk.Label(key_row, text="API Key", font=f["small"], fg=FG_DIM, bg=BG,
                 width=10, anchor="w").pack(side="left")
        self.key_var = tk.StringVar()
        self.key_entry = self._entry(key_row, self.key_var, show="\u2022")
        self.key_entry.pack(side="left", fill="x", expand=True)
        self._key_visible = False
        self.eye = tk.Label(key_row, text="显示", font=f["small"], fg=LINK, bg=BG,
                            cursor="hand2", padx=S(8))
        self.eye.pack(side="left")
        self.eye.bind("<Button-1>", lambda e: self._toggle_key())

        # 模型名
        self.model_var = tk.StringVar()
        self.model_entry = self._field_row(form, "模型名", self.model_var,
                                          "填厂商文档里的模型 ID，例如 deepseek-flash")

        # 测试结果
        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(self, textvariable=self.status_var,
                                     font=f["small"], fg=FG_DIM, bg=BG,
                                     justify="left", anchor="w",
                                     wraplength=S(480))
        self.status_label.pack(fill="x", padx=wrap_pad, pady=(S(10), S(4)))

        # 按钮
        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=wrap_pad, pady=(S(4), S(18)))
        self.test_btn = FlatButton(buttons, "测试连接", self._on_test,
                                   bg=CARD, fg=FG, font=f["body"])
        self.test_btn.pack(side="left")
        FlatButton(buttons, "取消", self._on_cancel, bg=CARD, fg=FG_DIM,
                   font=f["body"]).pack(side="right")
        FlatButton(buttons, "保存", self._on_save, bg=ACCENT, fg="#ffffff",
                   hover="#5f7bff", font=f["body"]).pack(side="right",
                                                         padx=(0, S(8)))

    def _entry(self, parent, variable, show=None):
        entry = tk.Entry(parent, textvariable=variable, font=self.f["body"],
                         bg=FIELD_BG, fg=FG, insertbackground=FG, relief="flat",
                         highlightthickness=1, highlightbackground=CARD_LINE,
                         highlightcolor=ACCENT, bd=0)
        if show:
            entry.configure(show=show)
        return entry

    def _field_row(self, parent, label, variable, hint):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, S(8)))
        tk.Label(row, text=label, font=self.f["small"], fg=FG_DIM, bg=BG,
                 width=10, anchor="w").pack(side="left")
        entry = self._entry(row, variable)
        entry.pack(side="left", fill="x", expand=True)
        tk.Label(row, text=hint, font=self.f["tiny"], fg=FG_FAINT, bg=BG,
                 anchor="w").pack(side="left", padx=(S(8), 0))
        return entry

    def _center(self, master):
        self.update_idletasks()
        width = max(self.winfo_reqwidth(), S(560))
        height = self.winfo_reqheight()
        try:
            root_x = master.winfo_rootx()
            root_y = master.winfo_rooty()
            root_w = master.winfo_width()
            root_h = master.winfo_height()
        except Exception:
            root_x, root_y, root_w, root_h = 200, 200, S(WIN_W), S(WIN_H)
        x = root_x + max(0, (root_w - width) // 2)
        y = root_y + max(0, (root_h - height) // 3)
        self.geometry("%dx%d+%d+%d" % (width, height, x, y))

    # ------------------------------------------------------------ 数据装载
    def _load_values(self):
        provider = self.provider or {}
        self.kind_var.set(provider.get("kind") or "custom")
        self.name_var.set(provider.get("name") or "")
        self.url_var.set(provider.get("base_url") or "")
        self.key_var.set(provider.get("api_key") or "")
        self.model_var.set(provider.get("model") or "")
        self._wire_api = provider.get("wire_api") or core.DEFAULT_WIRE_API
        self._sync_kind()

        # 编辑已有 DeepSeek 时，预选框自动定位到同名预设，方便替换
        name = provider.get("name")
        if name:
            for index, item in enumerate(self.presets):
                if (item.get("name") or "") == name:
                    self._preset_guard = True
                    self.preset_box.current(index)
                    self._preset_guard = False
                    break

    def _sync_kind(self):
        official = self.kind_var.get() == "official"
        state = "disabled" if official else "normal"
        for entry in (self.url_entry, self.key_entry):
            entry.configure(state=state,
                            bg=FIELD_BG if not official else "#1d1f26",
                            disabledbackground="#1d1f26",
                            disabledforeground=FG_FAINT)
        self.eye.configure(fg=FG_FAINT if official else LINK)
        if official:
            self.hint_var.set("官方模式使用 auth.json 里的 ChatGPT 凭据，不需要 Base URL 和 API Key。")

    def _toggle_key(self):
        if self.kind_var.get() == "official":
            return
        self._key_visible = not self._key_visible
        self.key_entry.configure(show="" if self._key_visible else "\u2022")
        self.eye.configure(text="隐藏" if self._key_visible else "显示")

    def _on_preset_selected(self, _event=None):
        if self._preset_guard:
            return
        index = self.preset_box.current()
        if index < 0 or index >= len(self.presets):
            return
        preset = self.presets[index]
        self._preset_id = preset.get("id") or ""
        self.kind_var.set(preset.get("kind") or "custom")
        self.name_var.set(preset.get("name") or "")
        self.url_var.set(preset.get("base_url") or "")
        self.model_var.set(preset.get("model") or "")
        self._wire_api = preset.get("wire_api") or core.DEFAULT_WIRE_API
        self.hint_var.set(preset.get("hint") or "")
        self._sync_kind()
        if self.kind_var.get() == "custom":
            try:
                self.url_entry.focus_set()
                self.url_entry.icursor("end")
            except Exception:
                pass

    # ------------------------------------------------------------ 校验 / 保存
    def _collect(self, for_save):
        """收集并校验表单。for_save=False 时宽松一些（测试连接用）。"""
        name = self.name_var.get().strip()
        kind = self.kind_var.get()
        model = self.model_var.get().strip()
        base_url = self.url_var.get().strip()
        api_key = self.key_var.get().strip()

        if for_save and not name:
            return None, "名称不能为空"
        if for_save and not model:
            return None, "模型名不能为空"

        if kind == "official":
            # 官方模式不校验也不使用 base_url / api_key，但要保留用户已经填过的值，
            # 免得在 official / custom 之间来回切换时把 Key 弄丢。
            pass
        else:
            if for_save and not base_url:
                return None, "Base URL 不能为空"
            if base_url:
                low = base_url.lower()
                if not (low.startswith("http://") or low.startswith("https://")):
                    return None, "Base URL 必须以 http:// 或 https:// 开头"
                fixed = core.ensure_trailing_slash(base_url)
                if fixed != base_url:
                    self.url_var.set(fixed)
                base_url = fixed

        # ---- wire_api 没有界面入口，统一固定成 responses。
        # Codex 0.163 已移除 wire_api = "chat"，任何非 responses 的值都会让
        # 切换被自检拒绝。之前这里是「把旧值原样带出去」，结果用户存完
        # 发现切不过去、而且界面上完全看不出原因。
        previous_wire = self._wire_api or core.DEFAULT_WIRE_API
        if for_save and previous_wire != core.SUPPORTED_WIRE_API:
            self.wire_api_note = (
                "已把 wire_api 固定为 responses（Codex 0.163 已不支持 chat 协议，"
                "原值 %r 会被拒绝）" % previous_wire)
        self._wire_api = core.SUPPORTED_WIRE_API

        provider = {
            "id": (self.provider or {}).get("id") or "",
            "name": name or model or "未命名",
            "kind": kind,
            "desc": (self.provider or {}).get("desc") or "",
            "model": model,
            "color": (self.provider or {}).get("color") or self.default_color,
            "wire_api": core.SUPPORTED_WIRE_API,
        }
        if kind == "custom":
            provider["base_url"] = base_url
            provider["api_key"] = api_key
        else:
            provider["base_url"] = ""
            provider["api_key"] = api_key
        return provider, ""

    def _set_status(self, text, color=FG_DIM):
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    def _on_save(self):
        provider, error = self._collect(for_save=True)
        if error:
            self._set_status(error, BAD)
            self.bell()
            return
        if not provider["id"]:
            # id 就是 config.toml 里的键名，优先用预设自带的（bailian / moonshot…），
            # 这样比从中文名硬转出来的 id 干净得多
            preset_id = (self._preset_id or "").strip()
            if preset_id and preset_id not in GENERIC_PRESET_IDS and \
                    preset_id not in self.existing_ids:
                provider["id"] = preset_id
            else:
                provider["id"] = core.make_provider_id(
                    provider["name"], self.existing_ids,
                    provider.get("base_url") or "")
        if provider["kind"] == "custom" and not provider.get("api_key"):
            if not messagebox.askyesno(
                    "还没填 API Key",
                    "「%s」还没有填 API Key，保存后切换过去会无法使用。\n\n仍要保存吗？"
                    % provider["name"], parent=self):
                return
        provider["desc"] = self._make_desc(provider)
        self.result = provider
        self.destroy()

    def _make_desc(self, provider):
        if provider["kind"] == "official":
            return "ChatGPT 账号登录，用 auth.json 里的凭据"
        host = core.host_of(provider.get("base_url") or "")
        return "%s · %s" % (host, provider.get("model") or "")

    def _on_cancel(self):
        self.result = None
        self.destroy()

    # ------------------------------------------------------------ 测试连接
    def _on_test(self):
        if self._testing:
            return
        provider, error = self._collect(for_save=False)
        if error:
            self._set_status(error, BAD)
            return
        if provider["kind"] == "official":
            self._set_status("官方 GPT 走 ChatGPT 账号登录，不需要测试 API 连接。", WARN)
            return
        if not provider.get("base_url"):
            self._set_status("请先填 Base URL", BAD)
            return
        if not provider.get("model"):
            self._set_status("请先填模型名", BAD)
            return
        if not provider.get("api_key"):
            self._set_status("请先填 API Key", BAD)
            return

        self._testing = True
        self.test_btn.set_enabled(False, text="测试中…")
        self._set_status("正在 POST %sresponses …"
                         % core.ensure_trailing_slash(provider["base_url"]), FG_DIM)
        snapshot = dict(provider)

        def job():
            try:
                status, detail, wire = core.probe_provider(
                    snapshot.get("base_url"), snapshot.get("api_key"),
                    snapshot.get("model"))
            except Exception as exc:  # 兜底，绝不让线程静默崩掉
                # 异常文本可能带出请求细节，统一过一遍 redact 再显示
                status, detail, wire = "fail", core.redact(
                    "探测异常：%s" % exc, snapshot.get("api_key") or ""), None
            self._safe_after(lambda: self._on_test_done(status, detail, wire))

        threading.Thread(target=job, daemon=True).start()

    def _safe_after(self, callback):
        try:
            self.after(0, callback)
        except Exception:
            pass

    def _on_test_done(self, status, detail, wire):
        self._testing = False
        if not self.winfo_exists():
            return
        self.test_btn.set_enabled(True, text="测试连接")
        text = "%s — %s" % ({"ok": "可用", "warn": "警告",
                             "auth_fail": "鉴权失败",
                             "fail": "失败"}.get(status, status), detail)
        if wire:
            text += "（协议 %s）" % wire
        if status == "warn" and wire == "chat":
            text += "\n当前 Codex 版本只支持 responses 协议，该厂商暂时无法使用。"
        color = {"ok": OK, "warn": WARN, "auth_fail": BAD, "fail": BAD}.get(status, FG_DIM)
        self._set_status(text, color)


# ---------------------------------------------------------------- 主界面
class App:
    def __init__(self, root, base_dir):
        self.root = root
        self.base_dir = base_dir
        self.providers_path = os.path.join(base_dir, "providers.json")
        self.presets = core.load_presets(os.path.join(base_dir, "presets.json"))
        self.data = core.load_or_seed_providers(
            self.providers_path, core.CONFIG_PATH, self.presets)
        # 损坏恢复标记：core 会把坏文件改名为 providers.json.corrupt-xxx 留档
        self.recovered_from = self.data.pop("recovered_from", "") or ""
        self.f = build_fonts()
        self.auto_restart = tk.BooleanVar(value=True)
        self.selected = ""
        self._rows = {}
        self._busy = False

        self._build_window()
        self._build_header()
        self._build_body()
        self._build_footer()

        self.selected = self._detect_active()
        self._refresh_all()
        self.log("就绪。配置文件：%s" % core.CONFIG_PATH)
        self.log("厂商清单：%s（%d 家）"
                 % (self.providers_path, len(self.data.get("providers") or [])))
        if self.recovered_from:
            # 等窗口先画出来再弹，否则对话框会孤零零地飘在空屏上
            self.root.after(150, self._warn_recovered)

    def _warn_recovered(self):
        """providers.json 损坏被重置时，必须明确告知用户旧文件还在哪。"""
        name = os.path.basename(self.recovered_from)
        self.log("providers.json 损坏，已备份为 %s 并重置为默认清单" % name)
        messagebox.showwarning(
            "配置文件损坏，已重置",
            "providers.json 无法解析，已自动重置为默认厂商清单。\n\n"
            "原文件没有被删掉，已备份为：\n%s\n\n"
            "如果需要恢复里面的厂商配置，可以打开配置目录手工改回来。"
            % self.recovered_from)

    # ------------------------------------------------------------ 窗口
    def _build_window(self):
        self.root.title("Codex 模型切换器")
        self.root.configure(bg=BG)
        self.root.geometry("%dx%d" % (S(WIN_W), S(WIN_H)))
        self.root.minsize(S(680), S(520))
        try:
            self.root.tk.call("tk", "scaling", _S * 96.0 / 72.0)
        except Exception:
            pass

    def _build_header(self):
        f = self.f
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=S(20), pady=(S(16), S(10)))

        left = tk.Frame(head, bg=BG)
        left.pack(side="left", anchor="w")
        tk.Label(left, text="Codex 模型切换器", font=f["title"], fg=FG,
                 bg=BG).pack(anchor="w")
        tk.Label(left, text="点一下换供应商，配置自动增量改写并备份",
                 font=f["sub"], fg=FG_DIM, bg=BG).pack(anchor="w", pady=(S(2), 0))

        right = tk.Frame(head, bg=BG)
        right.pack(side="right", anchor="e")
        tk.Label(right, text="当前生效", font=f["tiny"], fg=FG_DIM,
                 bg=BG).pack(anchor="e")
        line = tk.Frame(right, bg=BG)
        line.pack(anchor="e", pady=(S(2), 0))
        self.active_dot = tk.Label(line, text="●", font=f["dot"], fg=OK, bg=BG)
        self.active_dot.pack(side="left", padx=(0, S(5)))
        self.active_name = tk.Label(line, text="—",
                                    font=("Microsoft YaHei UI", 12, "bold"),
                                    fg=FG, bg=BG)
        self.active_name.pack(side="left")

    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=S(20))

        # ---- 左：厂商列表
        left = tk.Frame(body, bg=BG, width=S(LIST_W))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="厂商列表", font=self.f["tiny"], fg=FG_DIM,
                 bg=BG, anchor="w").pack(fill="x", pady=(0, S(6)))
        self.list_scroll = ScrollFrame(left, bg=BG, width=LIST_W)
        self.list_scroll.pack(fill="both", expand=True)
        self.list_inner = self.list_scroll.inner

        # ---- 右：详情
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(S(14), 0))
        self._build_detail(right)

    def _build_detail(self, parent):
        f = self.f

        card = tk.Frame(parent, bg=CARD)
        card.pack(fill="x")

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=S(16), pady=(S(14), S(4)))
        self.detail_name = tk.Label(top, text="—", font=f["detail"], fg=FG,
                                    bg=CARD, anchor="w")
        self.detail_name.pack(side="left")
        self.detail_tag = tk.Label(top, text="", font=f["tag"], fg=FG_DIM,
                                   bg=CARD_LINE, padx=S(7), pady=S(2))
        self.detail_tag.pack(side="left", padx=(S(10), 0))
        self.detail_state = tk.Label(top, text="", font=f["small"], fg=OK,
                                     bg=CARD)
        self.detail_state.pack(side="right")

        self.detail_desc = tk.Label(card, text="", font=f["small"], fg=FG_DIM,
                                    bg=CARD, anchor="w", justify="left",
                                    wraplength=S(430))
        self.detail_desc.pack(fill="x", padx=S(16))

        info = tk.Frame(card, bg=CARD)
        info.pack(fill="x", padx=S(16), pady=(S(10), S(14)))
        self.info = {}
        for key, label in (("model", "模型"),
                           ("base_url", "Base URL"),
                           ("wire_api", "协议"),
                           ("api_key", "API Key"),
                           ("probe", "最近测试")):
            row = tk.Frame(info, bg=CARD)
            row.pack(fill="x", pady=(0, S(3)))
            tk.Label(row, text=label, font=f["small"], fg=FG_DIM, bg=CARD,
                     width=10, anchor="w").pack(side="left")
            value = tk.Label(row, text="—", font=f["small"], fg=FG, bg=CARD,
                             anchor="w", justify="left", wraplength=S(390))
            value.pack(side="left", fill="x", expand=True)
            self.info[key] = value

        # ---- 按钮组
        actions = tk.Frame(parent, bg=BG)
        actions.pack(fill="x", pady=(S(12), S(8)))
        self.switch_btn = FlatButton(actions, "切换到此厂商", self.do_switch,
                                     bg=ACCENT, fg="#ffffff", hover="#5f7bff",
                                     font=f["body"], padx=18, pady=8)
        self.switch_btn.pack(side="left")
        self.test_btn = FlatButton(actions, "测试连接", self.do_probe,
                                    bg=CARD, fg=FG, font=f["body"],
                                    padx=14, pady=8)
        self.test_btn.pack(side="left", padx=(S(8), 0))
        self.edit_btn = FlatButton(actions, "编辑", self.edit_provider,
                                    bg=CARD, fg=FG, font=f["body"],
                                    padx=14, pady=8)
        self.edit_btn.pack(side="left", padx=(S(8), 0))
        self.delete_btn = FlatButton(actions, "删除", self.delete_provider,
                                      bg=CARD, fg=BAD, font=f["body"],
                                      padx=14, pady=8)
        self.delete_btn.pack(side="left", padx=(S(8), 0))

        # ---- 日志
        tk.Label(parent, text="运行日志", font=f["tiny"], fg=FG_DIM, bg=BG,
                 anchor="w").pack(fill="x", pady=(S(4), S(4)))
        self.log_box = tk.Text(parent, height=8, bg=FIELD_BG, fg=FG_DIM,
                               font=f["mono"], bd=0, highlightthickness=1,
                               highlightbackground=CARD_LINE, padx=S(8),
                               pady=S(6), wrap="word", takefocus=0)
        self.log_box.pack(fill="both", expand=True, pady=(0, S(4)))
        self.log_box.configure(state="disabled")

    def _build_footer(self):
        f = self.f
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=S(20), pady=(S(6), S(14)))

        FlatButton(bar, "+ 新增厂商", self.add_provider, bg=CARD, fg=FG,
                   font=f["small"], padx=12, pady=6).pack(side="left")
        FlatButton(bar, "打开配置目录", self.open_config_dir, bg=CARD, fg=FG,
                   font=f["small"], padx=12, pady=6).pack(side="left", padx=(S(8), 0))
        FlatButton(bar, "网络体检", self.do_network_check, bg=CARD, fg=FG,
                   font=f["small"], padx=12, pady=6).pack(side="left", padx=(S(8), 0))

        refresh = tk.Label(bar, text="重新读取", font=f["small"], fg=LINK, bg=BG,
                           cursor="hand2")
        refresh.pack(side="right")
        refresh.bind("<Button-1>", lambda e: self.reload_from_disk())
        refresh.bind("<Enter>", lambda e: refresh.configure(fg=LINK_HI))
        refresh.bind("<Leave>", lambda e: refresh.configure(fg=LINK))

        tk.Checkbutton(
            bar, text="切换后自动重启 Codex",
            variable=self.auto_restart, font=f["small"],
            fg=FG_DIM, bg=BG, activebackground=BG, activeforeground=FG,
            selectcolor=CARD, bd=0, highlightthickness=0, cursor="hand2",
        ).pack(side="right", padx=(0, S(14)))

    # ------------------------------------------------------------ 数据辅助
    def selected_provider(self):
        return core.find_provider(self.data, self.selected)

    def _detect_active(self):
        config_text = core.read_text(core.CONFIG_PATH)
        detected = core.detect_active_id(self.data.get("providers") or [], config_text)
        if core.find_provider(self.data, detected):
            return detected
        fallback = self.data.get("active") or ""
        if core.find_provider(self.data, fallback):
            return fallback
        providers = self.data.get("providers") or []
        return providers[0]["id"] if providers else ""

    def _persist(self):
        try:
            self.data = core.save_providers(self.providers_path, self.data)
        except Exception as exc:
            self.log("保存 providers.json 失败：%s" % exc)

    def reload_from_disk(self):
        """重新读 providers.json 与 config.toml，用于外部改文件后同步。"""
        self.data = core.load_or_seed_providers(
            self.providers_path, core.CONFIG_PATH, self.presets)
        recovered = self.data.pop("recovered_from", "") or ""
        self.selected = self._detect_active()
        self._refresh_all()
        if recovered:
            self.recovered_from = recovered
            self._warn_recovered()
        else:
            self.log("已重新读取 providers.json 与 config.toml")

    # ------------------------------------------------------------ 列表
    def _refresh_all(self):
        if not core.find_provider(self.data, self.selected):
            self.selected = self._detect_active()
        self._rebuild_list()
        self._render_detail()
        self._render_header()

    def _rebuild_list(self):
        for child in self.list_inner.winfo_children():
            child.destroy()
        self._rows = {}
        providers = self.data.get("providers") or []
        if not providers:
            tk.Label(self.list_inner, text="还没有厂商，\n点下面「+ 新增厂商」",
                     font=self.f["small"], fg=FG_DIM, bg=BG,
                     justify="left").pack(anchor="w", pady=S(8))
        active_id = self._detect_active()
        for provider in providers:
            self._make_row(provider, active_id)
        self.list_inner.update_idletasks()
        self.list_scroll.refresh()

    def _make_row(self, provider, active_id):
        f = self.f
        pid = provider.get("id") or ""
        color = provider.get("color") or ACCENT
        chosen = pid == self.selected
        bg = CARD_HI if chosen else CARD

        row = tk.Frame(self.list_inner, bg=bg, cursor="hand2", bd=0,
                       highlightthickness=1,
                       highlightbackground=color if chosen else bg,
                       highlightcolor=color)
        row.pack(fill="x", pady=(0, S(5)), padx=(0, S(2)))

        bar = tk.Frame(row, bg=color, width=S(4))
        bar.pack(side="left", fill="y")

        body = tk.Frame(row, bg=bg, cursor="hand2")
        body.pack(side="left", fill="both", expand=True,
                  padx=S(9), pady=S(7))

        line1 = tk.Frame(body, bg=bg)
        line1.pack(fill="x")
        name = tk.Label(line1, text=provider.get("name") or pid, font=f["name"],
                        fg=FG, bg=bg, anchor="w", cursor="hand2")
        name.pack(side="left")

        probe = provider.get("last_probe") or {}
        if probe.get("ok"):
            dot_color, dot_text = OK, "\u2713"
        elif probe:
            dot_color, dot_text = BAD, "\u2717"
        else:
            dot_color, dot_text = FG_FAINT, "\u25cb"
        dot = tk.Label(line1, text=dot_text, font=f["dot"], fg=dot_color,
                       bg=bg, cursor="hand2")
        dot.pack(side="right")

        meta_text = provider.get("model") or "（未设置模型）"
        if pid == active_id:
            meta_text = "使用中 · " + meta_text
        meta = tk.Label(body, text=meta_text, font=f["tiny"], fg=FG_DIM,
                        bg=bg, anchor="w", cursor="hand2")
        meta.pack(fill="x", pady=(S(2), 0))

        widgets = (row, bar, body, line1, name, dot, meta)
        for widget in widgets:
            widget.bind("<Button-1>", lambda e, key=pid: self._select(key))
            self.list_scroll.attach(widget)

        self._rows[pid] = {"row": row, "bar": bar, "body": body,
                           "name": name, "meta": meta, "dot": dot}

    def _select(self, provider_id):
        self.selected = provider_id
        self._refresh_all()

    def _render_header(self):
        active_id = self._detect_active()
        provider = core.find_provider(self.data, active_id)
        if provider is None:
            self.active_name.configure(text="未识别的配置")
            self.active_dot.configure(fg=WARN)
            return
        self.active_name.configure(text=provider.get("name") or active_id)
        self.active_dot.configure(fg=provider.get("color") or OK)

    def _render_detail(self):
        provider = self.selected_provider()
        if provider is None:
            self.detail_name.configure(text="—")
            self.detail_tag.configure(text="")
            self.detail_desc.configure(text="")
            for key in self.info:
                self.info[key].configure(text="—")
            self.switch_btn.set_enabled(False, text="切换到此厂商")
            self.edit_btn.set_enabled(False)
            self.delete_btn.set_enabled(False)
            self.test_btn.set_enabled(False)
            return

        kind = provider.get("kind") or "custom"
        official = kind == "official"
        active_id = self._detect_active()
        is_active = provider.get("id") == active_id

        self.detail_name.configure(text=provider.get("name") or provider.get("id"))
        self.detail_tag.configure(
            text="官方" if official else "第三方",
            fg=OK if official else FG_DIM,
            bg=CARD_LINE)
        self.detail_desc.configure(text=provider.get("desc") or "")
        self.detail_state.configure(text="● 使用中" if is_active else "",
                                    fg=provider.get("color") or OK)

        self.info["model"].configure(text=provider.get("model") or "（未设置）")
        if official:
            self.info["base_url"].configure(text="—（走 ChatGPT 账号登录）")
            self.info["api_key"].configure(text="—（使用 auth.json 凭据）")
        else:
            self.info["base_url"].configure(
                text=provider.get("base_url") or "（未设置）")
            self.info["api_key"].configure(
                text=core.mask_key(provider.get("api_key")))
        self.info["wire_api"].configure(
            text=provider.get("wire_api") or core.DEFAULT_WIRE_API)

        probe = provider.get("last_probe") or {}
        if probe:
            mark = "可用" if probe.get("ok") else "不可用"
            color = OK if probe.get("ok") else BAD
            self.info["probe"].configure(
                text="%s · %s · %s" % (probe.get("at") or "?", mark,
                                       probe.get("detail") or ""),
                fg=color)
        else:
            self.info["probe"].configure(text="尚未测试", fg=FG_FAINT)

        self.switch_btn.set_enabled(not is_active,
                                    text="使用中" if is_active else "切换到此厂商")
        self.edit_btn.set_enabled(True)
        self.delete_btn.set_enabled(not official)
        # 显式传 text，否则测试连接结束后按钮会一直停在「测试中…」
        self.test_btn.set_enabled(not official, text="测试连接")

    # ------------------------------------------------------------ 日志
    def log(self, message):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", "[%s] %s\n" % (stamp, message))
            total = int(self.log_box.index("end-1c").split(".")[0])
            if total > MAX_LOG_LINES:
                self.log_box.delete("1.0", "%d.0" % (total - MAX_LOG_LINES + 1))
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def log_threadsafe(self, message):
        try:
            self.root.after(0, self.log, message)
        except Exception:
            pass

    # ------------------------------------------------------------ 切换
    def _ensure_wire_api(self, provider):
        """把 wire_api 纠正成 responses 并落盘，返回用于切换的 provider 副本。

        Codex 0.163 只认 responses，界面上又没有这个字段，所以任何非
        responses 的存量值都必须自愈，否则用户会撞上一个无法理解的失败。
        """
        wire = provider.get("wire_api") or core.DEFAULT_WIRE_API
        if wire == core.SUPPORTED_WIRE_API:
            return provider
        self.log("「%s」的 wire_api = %r 不被当前 Codex 支持，已自动改为 responses"
                 % (provider.get("name") or provider.get("id"), wire))
        corrected = dict(provider)
        corrected["wire_api"] = core.SUPPORTED_WIRE_API
        self.data, _created = core.upsert_provider(self.data, corrected)
        self._persist()
        self._refresh_all()
        return corrected

    def do_switch(self):
        provider = self.selected_provider()
        if provider is None or self._busy:
            return
        active_id = self._detect_active()
        if provider.get("id") == active_id:
            self.log("当前已经是「%s」，无需切换" % (provider.get("name") or ""))
            return
        if (provider.get("kind") or "custom") == "custom" and not provider.get("api_key"):
            if not messagebox.askyesno(
                    "缺少 API Key",
                    "「%s」还没有填 API Key，切过去 Codex 会无法使用。\n\n仍要切换吗？"
                    % (provider.get("name") or "")):
                return

        secret = provider.get("api_key") or ""
        # 兜底：providers.json 被手工改成 wire_api = "chat" 时（对话框已经堵住，
        # 只剩手改这条路径），先就地纠正并落盘，否则切换会被自检拒绝、
        # 而用户完全看不出原因。
        provider = self._ensure_wire_api(provider)
        self._busy = True
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            logs = core.apply_provider(core.CONFIG_PATH, provider,
                                      self.base_dir, core.BACKUP_DIR,
                                      active_id)
        except Exception as exc:
            text = core.redact(str(exc), secret)
            self.log("切换失败：%s" % text)
            messagebox.showerror("切换失败", text)
            return
        finally:
            self._busy = False
            self.root.configure(cursor="")

        for line in logs:
            self.log(core.redact(line, secret))
        self.data["active"] = provider.get("id")
        self._persist()
        self._refresh_all()
        self.log("完成 → 现在使用「%s」" % (provider.get("name") or ""))

        if self.auto_restart.get():
            self.log("正在重启 Codex …")
            threading.Thread(target=self._restart_job, daemon=True).start()
        else:
            self.log("提醒：Codex 开着的话要手动退出再打开才生效")

    def _restart_job(self):
        try:
            core.restart_codex(self.log_threadsafe)
        except Exception as exc:
            self.log_threadsafe("重启 Codex 失败：%s" % exc)

    # ------------------------------------------------------------ 增删改
    def _ids(self):
        return [item.get("id") for item in (self.data.get("providers") or [])]

    def add_provider(self):
        dialog = ProviderDialog(self.root, self, provider=None,
                                presets=self.presets, existing_ids=self._ids(),
                                default_color=core.next_color(self.data))
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        self.data, _created = core.upsert_provider(self.data, dialog.result)
        self._persist()
        self.selected = dialog.result.get("id") or self.selected
        self._refresh_all()
        self.log("已新增厂商「%s」（id = %s）"
                 % (dialog.result.get("name"), dialog.result.get("id")))
        if getattr(dialog, "wire_api_note", ""):
            self.log(dialog.wire_api_note)
        if (dialog.result.get("kind") or "custom") == "custom":
            self.log("提示：点「测试连接」确认该厂商是否支持 /responses 协议")

    def edit_provider(self):
        provider = self.selected_provider()
        if provider is None:
            return
        dialog = ProviderDialog(self.root, self, provider=provider,
                                presets=self.presets, existing_ids=self._ids(),
                                default_color=provider.get("color") or ACCENT)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        self.data, _created = core.upsert_provider(self.data, dialog.result)
        self._persist()
        self.selected = dialog.result.get("id") or self.selected
        self._refresh_all()
        self.log("已更新厂商「%s」" % (dialog.result.get("name") or ""))
        if getattr(dialog, "wire_api_note", ""):
            self.log(dialog.wire_api_note)
        if dialog.result.get("id") == self._detect_active():
            self.log("提醒：这是当前生效的厂商，需重新点一次「切换到此厂商」配置才会刷新")

    def delete_provider(self):
        provider = self.selected_provider()
        if provider is None:
            return
        if (provider.get("kind") or "custom") == "official":
            messagebox.showinfo("无法删除", "官方 GPT 是兜底配置，不能删除。")
            return
        pid = provider.get("id")
        if not messagebox.askyesno(
                "删除厂商",
                "确定从清单里删除「%s」？\n\n"
                "只会删掉 providers.json 里的条目；config.toml 里已存在的 "
                "[model_providers.%s] 段不会被自动清理。" % (provider.get("name"), pid)):
            return
        try:
            self.data = core.remove_provider(self.data, pid)
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc))
            return
        self._persist()
        self.selected = self._detect_active()
        self._refresh_all()
        self.log("已删除厂商「%s」" % (provider.get("name") or pid))

    # ------------------------------------------------------------ 测试连接
    def do_probe(self):
        provider = self.selected_provider()
        if provider is None:
            return
        if (provider.get("kind") or "custom") == "official":
            self.log("官方 GPT 使用 ChatGPT 账号登录，跳过 API 协议测试")
            return
        if not provider.get("api_key"):
            messagebox.showwarning("缺少 API Key",
                                   "请先「编辑」该厂商并填入 API Key。")
            return
        pid = provider.get("id")
        snapshot = dict(provider)
        self.test_btn.set_enabled(False, text="测试中…")
        self.log("正在测试「%s」→ POST %sresponses"
                 % (provider.get("name"),
                    core.ensure_trailing_slash(provider.get("base_url") or "")))

        def job():
            try:
                status, detail, wire, record = core.probe_provider_dict(snapshot)
            except Exception as exc:
                # 和对话框里的分支保持一致：异常文本一律先掩码
                detail = core.redact("探测异常：%s" % exc,
                                     snapshot.get("api_key") or "")
                status, wire = "fail", None
                record = {"at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                          "ok": False, "detail": detail, "wire_api": None,
                          "status": "fail"}
            self.root.after(0, self._on_probe_done, pid, status, detail, wire, record)

        threading.Thread(target=job, daemon=True).start()

    def _on_probe_done(self, pid, status, detail, wire, record):
        provider = core.find_provider(self.data, pid)
        if provider is not None:
            provider["last_probe"] = record
            self._persist()
        mark = {"ok": "OK", "warn": "!!", "auth_fail": "XX", "fail": "XX"}.get(status, "--")
        self.log("%s %s" % (mark, detail))
        if status == "warn" and wire == "chat":
            self.log("提示：该厂商只支持 chat 协议，Codex 0.163 已移除 wire_api=\"chat\"，"
                     "这家暂时无法在 Codex 里使用。")
        elif status == "ok" and wire == "responses":
            self.log("结论：该厂商支持 /responses，可以放心切换")
        elif status == "auth_fail":
            self.log("结论：端点存在但 Key 被拒，请检查 API Key 或账户权限")
        self._refresh_all()

    # ------------------------------------------------------------ 网络体检
    def do_network_check(self):
        provider = self.selected_provider()
        targets = [("api.openai.com", "https://api.openai.com/v1/models"),
                   ("auth.openai.com", "https://auth.openai.com/")]
        if provider and provider.get("base_url"):
            targets.append((core.host_of(provider.get("base_url")),
                            provider.get("base_url")))
        self.log("开始网络体检（走系统代理）…")

        def job():
            for name, url in targets:
                state, detail = core.network_check(url)
                mark = {"ok": "OK", "warn": "!!", "bad": "XX"}.get(state, "--")
                self.root.after(0, self.log, "%s %-24s %s" % (mark, name, detail))
            self.root.after(0, self.log,
                            "体检完毕。403 表示链路通但节点被拦，换个节点即可。")
        threading.Thread(target=job, daemon=True).start()

    def open_config_dir(self):
        try:
            os.startfile(core.CODEX_HOME)
        except Exception as exc:
            self.log("打开配置目录失败：%s" % exc)


# ---------------------------------------------------------------- 启动
def main():
    if not os.path.isfile(core.CONFIG_PATH):
        fatal("未找到 Codex 配置文件：\n%s\n\n请先安装并启动一次 Codex，再运行本工具。"
              % core.CONFIG_PATH)
        return
    init_dpi()
    root = tk.Tk()
    try:
        App(root, BASE_DIR)
    except Exception:
        try:
            root.destroy()
        except Exception:
            pass
        raise
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        fatal(traceback.format_exc())

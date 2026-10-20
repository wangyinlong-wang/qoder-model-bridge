"""Portable GUI and diagnostic CLI. The API key is never written to a config file."""
import argparse
import getpass
import json
import os
import platform
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET
from qoder_control import attach_proxy, openrouter_models, restore_proxy, rpc
from idea_bridge import discover_bridges
from qoder_clients import CLIENT_CHOICES, IdeClient, app_info, installed_clients, get_client, attach_ide_proxy, retain_ide_proxy

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE = "http://127.0.0.1:3000/v1"
NOTICE = "在 Qoder 中选择 OpenRouter 和对应模型。本工具将模型校验与聊天转发到你的内网网关。"
REFERENCE_NAMES = {
    "efficient": "Efficient（档位）", "qfmodel": "Qwen3.8-Flash", "qmodel_latest": "Qwen3.7-Max",
    "qmodel": "Qwen3.7-Plus", "kmodel_latest": "Kimi-K3", "kmodel": "Kimi-K2.8-Preview",
    "gmodel": "GLM-5.3", "gfmodel": "GLM-5.3-Flash", "dmodel": "DeepSeek-V4-Pro", "mmodel": "MiniMax-M3",
}
REFERENCE_SOURCE = "内置参考名称（JetBrains 2026.924.75731882，2026-10-16）"
CONFIG_FIELDS = {"base_url", "listen_port", "no_auth", "model_map", "qoder_enabled", "manage_qoder_proxy",
                 "integration_mode", "client_type", "client_edition", "client_path"}


def get_integration_mode(config):
    mode = config.get("integration_mode", "manual")
    if mode not in ("manual", "bridge"):
        raise ValueError("integration_mode 必须是 manual 或 bridge。")
    return mode


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def normalize_base(value):
    p = urlsplit(value.strip())
    if (p.scheme not in ("http", "https") or not p.hostname or p.username or p.password
            or p.query or p.fragment or p.hostname.lower().rstrip(".") == "openrouter.ai"):
        raise ValueError("请填写不含凭据、查询参数的上游 HTTP(S) Base URL。")
    try:
        p.port
    except ValueError:
        raise ValueError("Base URL 端口无效。") from None
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") or "/v1", "", ""))


def fetch_models(base, key, no_auth=False):
    if no_auth:
        key = ""
    if not key and not no_auth:
        raise ValueError("请输入 API Key。")
    if any(c in key for c in "\r\n") or not key.isascii():
        raise ValueError("API Key 必须是单行 ASCII 字符。")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        # Do not accidentally send internal credentials through an unrelated environment proxy.
        with build_opener(ProxyHandler({}), NoRedirect()).open(Request(base + "/models", headers=headers), timeout=15) as r:
            payload = json.loads(r.read(4 * 1024 * 1024))
    except HTTPError as e:
        if 300 <= e.code < 400:
            raise ValueError(f"上游返回重定向 HTTP {e.code}；为了保护 Key，未跟随。请填写最终授权地址。") from None
        raise ValueError(f"模型列表请求失败：HTTP {e.code}。请检查地址、Key 和上游权限。") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("无法连接模型列表接口。请检查本机到上游的网络和证书。") from None
    except (ValueError, UnicodeError):
        raise ValueError("上游没有返回有效的模型列表 JSON。") from None
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("模型列表必须包含非空 data 数组。")
    result = {}
    for model in models:
        mid = model.get("id") if isinstance(model, dict) else None
        if not isinstance(mid, str) or not mid or len(mid) > 200 or not mid.isprintable():
            raise ValueError("模型列表包含无效的 id。")
        result[mid] = model
    return result


def local_catalog():
    """Read only model names; never read Qoder credential stores or account identifiers."""
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/JetBrains"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", "")) / "JetBrains"
    else:
        base = Path.home() / ".config/JetBrains"
    names, sources = {}, []
    for path in sorted(base.glob("*/options/qoder_model_classes.xml"), key=lambda p: p.stat().st_mtime):
        try:
            root = ET.parse(path).getroot()
            for node in root.iter("option"):
                if node.get("name") == "modelsJson":
                    for item in json.loads(node.get("value", "[]")):
                        if not isinstance(item, dict):
                            continue
                        name, display = item.get("name"), item.get("displayName")
                        if isinstance(name, str) and isinstance(display, str):
                            names[name] = display
            sources.append({"ide": path.parents[1].name, "mtime": int(path.stat().st_mtime)})
        except (OSError, ValueError, ET.ParseError):
            continue
    return names, sources


def display_name(mid, model, catalog, client_type="idea", mapping=None):
    version = next((str(model[field]).strip() for field in ("version", "model_version")
                    if type(model.get(field)) in (str, int, float) and str(model[field]).strip()), "")
    version_source = (f"上游声明版本：{version}（未独立核验）" if version
                      else "上游未声明版本；实际版本未核实")
    # qd/ is a label lookup only; neither that alias nor a reference name proves a backend version.
    alias = mid[3:] if mid.startswith("qd/") else mid
    direct = next((catalog[key] for key in (mid, alias)
                   if isinstance(catalog.get(key), str) and catalog[key]), "")
    mapped_sources = [source for source, target in (mapping or {}).items()
                      if target == mid and isinstance(catalog.get(source), str) and catalog[source]]
    if isinstance(model.get("name"), str) and model["name"]:
        name, source = model["name"], "上游声明名称"
    elif direct:
        name = direct
        source = "当前 Qoder IDE 目录参考" if client_type == "qoder_ide" else "本机 IDEA/Qoder 目录参考"
    elif client_type == "qoder_ide" and len(mapped_sources) == 1:
        name, source = catalog[mapped_sources[0]], "当前 Qoder IDE 目录参考（按已有映射）"
    elif client_type == "qoder_ide":
        name, source = mid, "当前 IDE 未提供参考名称"
    elif mid == "cn-auto":
        name, source = "auto", "用户指定名称（智能路由）"
    elif alias in REFERENCE_NAMES:
        name, source = REFERENCE_NAMES[alias], REFERENCE_SOURCE
    else:
        name, source = mid, "上游未提供名称；本机目录无参考"
    return name, f"{source}；{version_source}"


def validate_mapping(mapping, models):
    if not isinstance(mapping, dict):
        raise ValueError("model_map 必须是源请求 ID 到上游 ID 的对象。")
    for source, target in mapping.items():
        if not isinstance(source, str) or not source.strip() or not source.isprintable() or len(source) > 200:
            raise ValueError("源请求 model 字段无效。")
        if not isinstance(target, str) or target not in models:
            raise ValueError("映射目标不在本次上游模型列表中，请重新选择。")


def suggest_source_id(mid, model, catalog, source_models, client_type="idea", mapping=None):
    name, _ = display_name(mid, model, catalog, client_type, mapping)
    matches = [key for key, label in source_models.items() if label.casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return mid
    if mid == "qd/efficient":
        return "efficient"
    suggestion = name.lower()
    return suggestion if re.fullmatch(r"[a-z0-9_./:-]+", suggestion) else mid


def selected_mappings(selected_ids, models, catalog, source_models, existing, client_type="idea"):
    """Resolve the whole selection first, so collisions never depend on row order."""
    groups = {}
    for mid in dict.fromkeys(selected_ids):
        if mid not in models:
            raise ValueError("选中的模型已不在当前列表中，请重新拉取并选择。")
        source = suggest_source_id(mid, models[mid], catalog, source_models, client_type, existing)
        groups.setdefault(source, set()).add(mid)
    additions, conflicts, unchanged = {}, [], 0
    for source, targets in groups.items():
        if len(targets) != 1:
            conflicts.append(source)
            continue
        target = next(iter(targets))
        if source in existing:
            if existing[source] == target:
                unchanged += 1
            else:
                conflicts.append(source)
        else:
            additions[source] = target
    validate_mapping(additions, models)
    return additions, sorted(conflicts), unchanged


def idea_import_models(selected_ids, models, mapping, catalog, source_models, client_type="idea"):
    result = []
    for mid in selected_ids:
        if mid not in models:
            raise ValueError("选中的内网模型已失效，请重新拉取。")
        sources = [source for source, target in mapping.items() if target == mid]
        preferred = suggest_source_id(mid, models[mid], catalog, source_models, client_type, mapping)
        if preferred in sources:
            source = preferred
        elif len(sources) == 1:
            source = sources[0]
        elif not sources:
            raise ValueError(f"{mid} 尚未加入映射。请先停止转发、批量加入映射，再启动转发后导入。")
        else:
            raise ValueError(f"{mid} 对应多个 Qoder ID，请先明确要导入的映射。")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", source):
            raise ValueError("IDEA 导入的模型 ID 只能包含字母、数字和 . _ : / -。")
        item = {"source_id": source, "target_id": mid,
                "display_name": display_name(mid, models[mid], catalog, client_type, mapping)[0]}
        caps = models[mid].get("capabilities")
        caps = caps if isinstance(caps, dict) else {}
        context = models[mid].get("context_length", caps.get("contextWindow"))
        if type(context) is int and 0 < context <= 10_000_000:
            item["context_length"] = context
        for key in ("vision", "reasoning"):
            if type(caps.get(key)) is bool:
                item[key] = caps[key]
        result.append(item)
    return result


def mitmdump_path():
    path = ROOT / ".venv" / ("Scripts/mitmdump.exe" if os.name == "nt" else "bin/mitmdump")
    if not path.is_file():
        raise ValueError("未找到独立 mitmdump，请先用本目录的 macOS / Windows 启动入口准备依赖。")
    return path


def proxy_command():
    executable = os.environ.get("QODER_BUNDLE_EXECUTABLE")
    if executable:
        if not Path(executable).is_file():
            raise ValueError("应用内置的代理启动器不完整，请重新安装完整应用。")
        return ["/usr/bin/arch", "-" + platform.machine(), executable, "--proxy-worker"]
    return [str(mitmdump_path())]


def proxy_state_directory():
    return Path(os.environ.get("QODER_PROXY_STATE_DIR", str(ROOT / ".runtime")))


def default_config_path():
    return proxy_state_directory().parent / "proxy-config.json"


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("配置必须是 JSON 对象。")
    if set(config) - CONFIG_FIELDS:
        raise ValueError("配置含有不支持的字段，文件已保留；只允许本工具的非秘密配置，不允许保存 Key。")
    get_client(config)
    get_integration_mode(config)
    if "client_path" in config and not isinstance(config["client_path"], str):
        raise ValueError("client_path 必须是应用安装路径。")
    if "base_url" in config:
        if not isinstance(config["base_url"], str):
            raise ValueError("base_url 必须是 HTTP(S) 地址。")
        normalize_base(config["base_url"])
    for field in ("no_auth", "qoder_enabled", "manage_qoder_proxy"):
        if field in config and type(config[field]) is not bool:
            raise ValueError(f"{field} 必须是 true 或 false。")
    if "listen_port" in config:
        try:
            valid_port = type(config["listen_port"]) in (int, str) and 1 <= int(config["listen_port"]) <= 65535
        except ValueError:
            valid_port = False
        if not valid_port:
            raise ValueError("listen_port 必须是 1–65535 的整数。")
    mapping = config.get("model_map", {})
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v
                                                for k, v in mapping.items()):
        raise ValueError("model_map 必须是非空源模型 ID 到目标 ID 的对象。")
    return config


def read_config(path):
    try:
        return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        raise ValueError("配置不是有效的 JSON，原文件未改动。") from None


def write_config(path, config):
    path = Path(path)
    validate_config(config)
    if path.exists():
        read_config(path)  # Refuse to overwrite unknown/sensitive fields or an invalid existing file.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def start_proxy(config, key):
    port = int(config.get("listen_port", 8899))
    if not 1 <= port <= 65535:
        raise ValueError("监听端口必须是 1–65535。")
    env = os.environ.copy()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    env["INTERNAL_API_KEY"] = key
    env["QODER_PROXY_SETTINGS"] = json.dumps(config, ensure_ascii=False)
    state = proxy_state_directory()
    state.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        state.chmod(0o700)
    hosts = r"^openrouter\.ai\.?(?::[0-9]+)?$"
    if config.get("qoder_enabled"):
        hosts = r"^(?:openrouter\.ai|(?:center|api\d+)\.qoder\.sh|gateway\.qoder\.com\.cn)\.?(?::[0-9]+)?$"
    args = proxy_command() + ["--quiet", "--listen-host", "127.0.0.1", "--listen-port", str(port),
            "--set", "confdir=" + str(state / "certs"), "--set", "connection_strategy=lazy",
            "--set", "upstream_cert=false", "--set", "allow_hosts=" + hosts,
            "--set", "flow_detail=0", "-s", str(ROOT / "qoder_to_internal.py")]
    process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    return process, state / "certs/mitmproxy-ca-cert.pem"


def stop_proxy(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class App:
    def __init__(self, root, initial):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.events = queue.Queue()
        self.process = None
        self.models, self.mapping = {}, dict(initial.get("model_map", {}))
        self.checked_models = set()
        self.idea_endpoints = {}
        self.importing = False
        self.fetching = False
        self.active_client, self.client_edition = get_client(initial)
        self.ide_client = None
        self.client_connecting = False
        self.client_supported = False
        self.proxy_connecting = False
        self.client_choice = tk.StringVar(value=next(label for label, value in CLIENT_CHOICES.items()
                                                    if value == (self.active_client, self.client_edition)))
        self.client_path = tk.StringVar(value=initial.get("client_path", ""))
        self.active_mode = get_integration_mode(initial)
        self.integration_mode = tk.StringVar(value=self.active_mode)
        self.key = ""
        self.auth_base = None
        self.proxy_state = proxy_state_directory()
        self.config_path = Path(initial.get("_config_path", default_config_path()))
        self.proxy_owner = uuid.uuid4().hex
        self.owns_proxy = False
        self.manage_qoder = tk.BooleanVar(value=initial.get("manage_qoder_proxy", True))
        self.manage_proxy_text = tk.StringVar()
        self.source_models = {}
        self.auto_source = ""
        self.auto_start = initial.get("_connect_on_start", False)
        self.auth_no_auth = False
        self.catalog = {}
        root.title("Qoder 内网模型接入")
        root.geometry("1080x880")
        root.minsize(850, 760)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=NOTICE, wraplength=1000, foreground="#9c4b00").pack(anchor="w", pady=(0, 12))
        client_controls = ttk.Frame(frame)
        client_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(client_controls, text="目标客户端").pack(side="left")
        self.client_choices = ttk.Combobox(client_controls, textvariable=self.client_choice,
                                          values=list(CLIENT_CHOICES), width=43, state="readonly")
        self.client_choices.pack(side="left", padx=10)
        self.client_choices.bind("<<ComboboxSelected>>", lambda _: self.change_client())
        self.ide_connection_controls = ttk.Frame(frame)
        ttk.Label(self.ide_connection_controls, text="IDE 安装位置").pack(side="left")
        self.client_path_entry = ttk.Entry(self.ide_connection_controls, textvariable=self.client_path)
        self.client_path_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.client_browse_button = ttk.Button(self.ide_connection_controls, text="选择程序", command=self.choose_client)
        self.client_browse_button.pack(side="left")
        self.client_connect_button = ttk.Button(self.ide_connection_controls, text="连接 / 启动 Qoder IDE", command=self.connect_ide)
        self.client_connect_button.pack(side="left", padx=(8, 0))
        mode_controls = self.mode_controls = ttk.Frame(frame)
        mode_controls.pack(fill="x", pady=(0, 4))
        ttk.Label(mode_controls, text="模型添加方式").pack(side="left")
        self.mode_buttons = []
        for mode, label in (("manual", "手动添加（无需 IDEA 桥接插件）"),
                            ("bridge", "桥接导入（检查 IDEA / Qoder 兼容性）")):
            button = ttk.Radiobutton(mode_controls, text=label, value=mode,
                                     variable=self.integration_mode, command=self.change_mode)
            button.pack(side="left", padx=(12, 0))
            self.mode_buttons.append(button)
        self.mode_hint = tk.StringVar()
        self.mode_hint_label = ttk.Label(frame, textvariable=self.mode_hint, wraplength=1000)
        self.mode_hint_label.pack(anchor="w", pady=(0, 8))
        fields = ttk.Frame(frame)
        fields.pack(fill="x")
        self.base = tk.StringVar(value=initial.get("base_url", DEFAULT_BASE))
        self.port = tk.StringVar(value=str(initial.get("listen_port", 8899)))
        self.password = tk.StringVar(value=os.environ.pop("INTERNAL_API_KEY", ""))
        self.no_auth = tk.BooleanVar(value=initial.get("no_auth", False))
        for row, (label, var, show) in enumerate([("上游 Base URL", self.base, ""), ("API Key（本次使用，不保存）", self.password, "*"), ("本地端口", self.port, "")]):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(fields, textvariable=var, show=show).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
        fields.columnconfigure(1, weight=1)
        ttk.Checkbutton(fields, text="上游明确无需鉴权", variable=self.no_auth).grid(row=3, column=1, sticky="w", padx=12)
        ttk.Checkbutton(fields, textvariable=self.manage_proxy_text, variable=self.manage_qoder).grid(row=4, column=1, sticky="w", padx=12)
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=10)
        self.fetch_button = ttk.Button(controls, text="1. 验证 Key 并拉取模型", command=self.fetch)
        self.fetch_button.pack(side="left")
        self.start_button = ttk.Button(controls, text="2. 启动 Qoder 转发", command=self.start, state="disabled")
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Button(controls, text="保存非秘密配置", command=self.save).pack(side="right")
        batch = ttk.LabelFrame(frame, text="模型多选 · 一键加入映射", padding=8)
        batch.pack(fill="both", expand=True)
        batch_controls = ttk.Frame(batch)
        batch_controls.pack(fill="x", pady=(0, 6))
        ttk.Button(batch_controls, text="全选", command=lambda: self.check_all_models(True)).pack(side="left")
        ttk.Button(batch_controls, text="取消全选", command=lambda: self.check_all_models(False)).pack(side="left", padx=6)
        self.batch_button = ttk.Button(batch_controls, text="一键加入映射", command=self.add_checked_mappings, state="disabled")
        self.batch_button.pack(side="left")
        self.batch_count = tk.StringVar(value="已勾选 0 / 0")
        ttk.Label(batch_controls, textvariable=self.batch_count).pack(side="left", padx=12)
        idea_controls = self.idea_controls = ttk.Frame(batch)
        ttk.Label(idea_controls, text="目标 IDEA").pack(side="left")
        self.idea_target = tk.StringVar()
        self.idea_targets = ttk.Combobox(idea_controls, textvariable=self.idea_target, state="readonly", width=37)
        self.idea_targets.pack(side="left", padx=8, fill="x", expand=True)
        self.idea_targets.bind("<<ComboboxSelected>>", lambda _: self.update_batch_controls())
        self.idea_refresh_button = ttk.Button(idea_controls, text="刷新桥接", command=self.refresh_idea_bridges)
        self.idea_refresh_button.pack(side="left", padx=(0, 8))
        self.idea_import_button = ttk.Button(idea_controls, text="一键添加到 IDEA / Qoder", command=self.import_to_idea, state="disabled")
        self.idea_import_button.pack(side="left")
        self.ide_import_controls = ttk.Frame(batch)
        self.ide_import_button = ttk.Button(self.ide_import_controls, text="一键添加到所选 Qoder IDE",
                                            command=self.import_to_client, state="disabled")
        self.ide_import_button.pack(side="left")
        ttk.Label(self.ide_import_controls, text="使用原生校验和安全存储；已存在模型跳过，上游 Key 不写入客户端。").pack(side="left", padx=12)
        self.catalog_status = tk.StringVar(value="尚未拉取模型；参考名称不代表实际上游型号或版本。")
        ttk.Label(batch, textvariable=self.catalog_status, wraplength=1000).pack(anchor="w", pady=(4, 6))
        columns = ("checked", "name", "remote", "capability", "source")
        table_frame = self.table_frame = ttk.Frame(batch)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=5, selectmode="browse")
        for col, title, width in zip(columns, ["选择", "显示名称（声明 / 参考）", "上游模型 ID / 别名", "上游声明能力", "名称来源 / 版本说明"], [48, 180, 170, 210, 300]):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, stretch=col != "checked", anchor="center" if col == "checked" else "w")
        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=table_scroll.set)
        table_scroll.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.select_remote)
        self.table.bind("<Button-1>", self.check_model_click)
        self.table.bind("<space>", self.check_focused_model)
        self.model_details = tk.StringVar(value="选中模型行，可在此查看完整名称来源与版本说明。")
        self.model_details_label = ttk.Label(batch, textvariable=self.model_details, wraplength=1000, justify="left")
        self.model_details_label.pack(fill="x", pady=(4, 0))
        self.model_details_label.bind("<Configure>", lambda event: self.model_details_label.configure(wraplength=max(200, event.width - 4)))
        ttk.Label(frame, text="勾选后批量生成下方映射，同名冲突保留原映射。运行中请先停止转发再改映射；下方仍可单条编辑。", wraplength=1000).pack(anchor="w", pady=(10, 4))
        editor = ttk.Frame(frame)
        editor.pack(fill="x")
        self.source_id, self.target_id = tk.StringVar(), tk.StringVar()
        ttk.Label(editor, text="Qoder 模型 ID").grid(row=0, column=0, sticky="w")
        self.sources = ttk.Combobox(editor, textvariable=self.source_id, width=26)
        self.sources.grid(row=0, column=1, padx=8, sticky="ew")
        ttk.Label(editor, text="转发到").grid(row=0, column=2)
        self.targets = ttk.Combobox(editor, textvariable=self.target_id, state="readonly", width=25)
        self.targets.grid(row=0, column=3, padx=8, sticky="ew")
        self.map_button = ttk.Button(editor, text="确认加入映射", command=self.add_mapping)
        self.map_button.grid(row=0, column=4)
        ttk.Button(editor, text="移除选中映射", command=self.remove_mapping).grid(row=0, column=5, padx=(8, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        maps_frame = ttk.Frame(frame)
        maps_frame.pack(fill="x", pady=8)
        self.maps = ttk.Treeview(maps_frame, columns=("source", "target"), show="headings", height=3)
        self.maps.heading("source", text="已明确确认的源 model 字段")
        self.maps.heading("target", text="实际转发模型 ID")
        maps_scroll = ttk.Scrollbar(maps_frame, orient="vertical", command=self.maps.yview)
        self.maps.configure(yscrollcommand=maps_scroll.set)
        maps_scroll.pack(side="right", fill="y")
        self.maps.pack(side="left", fill="x", expand=True)
        self.status = tk.StringVar(value="请输入当前机器的内网 Key；Key 不保存。Qoder 的 API 密钥框可填写 local-proxy。")
        ttk.Label(frame, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=4)
        self.logs = tk.Text(frame, height=6, state="disabled", font=("TkFixedFont", 10))
        self.logs.pack(fill="x")
        self.refresh_maps()
        self.change_client()
        root.protocol("WM_DELETE_WINDOW", self.close)
        if sys.platform == "darwin":
            root.createcommand("tk::mac::Quit", self.close)
        root.after(100, self.poll)
        if self.auto_start:
            root.after(150, self.connect_ide if self.active_client == "qoder_ide" else self.fetch)

    def error(self, text):
        from tkinter import messagebox
        messagebox.showerror("无法继续", text, parent=self.root)

    def change_client(self):
        requested = CLIENT_CHOICES.get(self.client_choice.get())
        current = (self.active_client, self.client_edition)
        if requested is None:
            return
        if requested != current and (self.importing or self.fetching or self.client_connecting
                                     or self.process is not None and self.process.poll() is None or self.owns_proxy):
            self.client_choice.set(next(label for label, value in CLIENT_CHOICES.items() if value == current))
            self.error("请先完成导入并停止转发、恢复原代理，再切换客户端。")
            return
        if requested != current:
            if self.ide_client:
                self.ide_client.detach()
            self.ide_client = None
            self.client_supported = False
            self.client_path.set("")
            self.source_models = {}
            self.catalog = {}
            self.sources.configure(values=[])
        self.active_client, self.client_edition = requested
        if requested != current:
            self.catalog_status.set("已切换客户端；下方为上次上游结果，请重新拉取当前客户端的参考目录。")
            self.refresh_model_rows()
        self.change_mode()

    def choose_client(self):
        from tkinter import filedialog
        if sys.platform == "darwin":
            path = filedialog.askopenfilename(parent=self.root, title="选择 Qoder IDE 应用", initialdir="/Applications")
        else:
            path = filedialog.askopenfilename(parent=self.root, title="选择 Qoder IDE 主程序", filetypes=[("应用程序", "*.exe")])
        if path:
            self.client_path.set(path)

    def connect_ide(self):
        if self.active_client != "qoder_ide" or self.client_connecting or self.importing or self.fetching:
            return
        try:
            if self.client_path.get().strip():
                info = app_info(self.client_path.get().strip())
                if info["edition"] != self.client_edition:
                    raise ValueError("安装清单的国际版/国内版标识与所选客户端不符，请重新选择。")
            else:
                candidates = installed_clients(self.client_edition)
                if len(candidates) != 1:
                    raise ValueError("未找到唯一的对应版本安装位置，请点击“选择程序”指定 Qoder IDE。")
                info = candidates[0]
            if self.owns_proxy and self.ide_client and info["path"] != self.ide_client.info["path"]:
                raise ValueError("请先恢复当前客户端代理，再连接其他安装位置。")
        except ValueError as error:
            self.error(str(error))
            return
        self.client_path.set(info["path"])
        self.client_connecting = True
        self.update_batch_controls()
        self.status.set("正在连接所选 Qoder IDE 的原生接口；如果客户端已打开，请先正常退出后再连接。")
        def run():
            client = self.ide_client if self.ide_client and self.ide_client.ready and self.ide_client.info == info else IdeClient(info)
            try:
                health = client.request("health") if client.ready else client.connect()
                # The IDE can expose its UI before the saved account has hydrated.
                # Give that local state time to load before asking the user to sign in.
                deadline = time.monotonic() + 8
                while health.get("byok_supported") is not True and time.monotonic() < deadline:
                    time.sleep(0.25)
                    health = client.request("health")
                catalog_error, sources = "", {}
                try:
                    sources = client.request("catalog").get("sources", {})
                except ValueError as error:
                    catalog_error = str(error)
                self.events.put(("client_connected", (client, health, sources, catalog_error)))
            except (ValueError, OSError) as error:
                client.detach()
                self.events.put(("client_error", str(error) if isinstance(error, ValueError) else "无法启动所选 Qoder IDE。"))
        threading.Thread(target=run, daemon=True).start()

    def import_to_client(self):
        if self.active_client == "idea":
            self.import_to_idea()
            return
        try:
            if self.importing or not self.ide_client or not self.ide_client.ready or not self.client_supported:
                raise ValueError("请先连接所选 Qoder IDE，并确认账号允许自定义模型。")
            if self.process is None or self.process.poll() is not None:
                raise ValueError("请先启动转发，再一键添加模型。")
            selected = [mid for mid in self.models if mid in self.checked_models]
            models = idea_import_models(selected, self.models, self.mapping, self.catalog, self.source_models,
                                        self.active_client)
            if not models:
                raise ValueError("请先勾选要添加的模型。")
            port = int(self.port.get())
        except (ValueError, TypeError) as error:
            self.error(str(error))
            return
        self.importing = True
        self.stop_button.configure(state="disabled")
        self.update_batch_controls()
        self.status.set("Qoder IDE 正在通过原生接口校验并添加模型；客户端只保存 local-proxy 占位值。")
        client = self.ide_client
        def run():
            try:
                self.events.put(("client_import_result", client.request("import", models=models, proxy_port=port)))
            except Exception as error:
                self.events.put(("client_import_error", str(error) if isinstance(error, ValueError)
                                 else "客户端连接中断，请先检查模型列表；未自动重复提交。"))
        threading.Thread(target=run, daemon=True).start()

    def finish_proxy_settings(self):
        if self.active_client == "qoder_ide":
            info = self.ide_client.info if self.ide_client else {"edition": self.client_edition,
                                                                "path": self.client_path.get().strip()}
            return retain_ide_proxy(info, self.proxy_state, self.proxy_owner)
        return restore_proxy(self.proxy_state, owner=self.proxy_owner)

    def change_mode(self):
        self.manage_proxy_text.set("启动时设置 Qoder IDE 代理，停止后保留" if self.active_client == "qoder_ide"
                                  else "启动转发时设置所选客户端代理，停止时恢复")
        if self.importing:
            self.integration_mode.set(self.active_mode)
            self.error("请先在 IDEA 完成或取消当前导入，再切换添加方式。")
            return
        self.active_mode = get_integration_mode({"integration_mode": self.integration_mode.get()})
        self.idea_endpoints = {}
        self.idea_target.set("")
        self.idea_targets.configure(values=[])
        if self.active_client == "qoder_ide":
            self.mode_controls.pack_forget()
            self.idea_controls.pack_forget()
            self.ide_connection_controls.pack(fill="x", pady=(0, 8), before=self.mode_hint_label)
            self.ide_import_controls.pack(fill="x", pady=(0, 6), before=self.table_frame)
            self.mode_hint.set("Qoder IDE：已有代理可直接启动同端口转发；首次配置、读取 IDE 目录或添加模型时需连接客户端。")
            self.update_batch_controls()
            return
        self.ide_connection_controls.pack_forget()
        self.ide_import_controls.pack_forget()
        self.mode_controls.pack(fill="x", pady=(0, 4), before=self.mode_hint_label)
        if self.active_mode == "bridge":
            self.idea_controls.pack(fill="x", pady=(0, 6), before=self.table_frame)
            self.mode_hint.set("桥接导入：正在查找 IDEA；桥接插件按 IDEA Build 和 Qoder 版本检查兼容性。")
            self.refresh_idea_bridges()
        else:
            self.idea_controls.pack_forget()
            self.mode_hint.set("手动添加：不检查 IDEA 版本。启动转发后，在 Qoder 原生设置中添加相同的模型 ID，密钥填 local-proxy。")
        self.update_batch_controls()

    def fetch(self):
        if self.client_connecting or self.fetching or self.importing:
            self.error("客户端连接、模型读取或导入尚未完成，请稍候再拉取模型。")
            return
        if self.process and self.process.poll() is None:
            self.error("请先停止代理，再更新上游或凭据。")
            return
        try:
            base = normalize_base(self.base.get())
            no_auth = bool(self.no_auth.get())
            entered_key = self.password.get().strip()
            if no_auth:
                key = ""
            elif entered_key:
                key = entered_key
            elif base == self.auth_base and not self.auth_no_auth:
                key = self.key
            else:
                raise ValueError("请输入这个上游地址的 API Key；更换地址时不会复用之前的凭据。")
        except ValueError as e:
            self.error(str(e))
            return
        self.fetch_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.fetching = True
        self.update_batch_controls()
        self.status.set("正在认证并读取 /models…")
        client = self.ide_client if self.active_client == "qoder_ide" else None
        use_idea = self.active_client == "idea"
        context = (self.active_client, self.client_edition, client)
        def run():
            try:
                models = fetch_models(base, key, no_auth)
                catalog, sources = {}, {}
                read_at = time.strftime("%Y-%m-%d %H:%M:%S")
                if use_idea:
                    try:
                        catalog, _ = local_catalog()
                        note = f"本机 IDEA/Qoder 参考目录读取时间：{read_at}。参考名称不代表实际上游型号或版本。"
                    except OSError:
                        note = f"本机 IDEA/Qoder 目录读取失败（{read_at}）；仅使用标明的内置参考名称或上游 ID。"
                else:
                    note = f"当前 IDE 目录不可读（{read_at}）；未采用旧参考名称，上游未声明名称时显示原始 ID。"
                try:
                    if use_idea:
                        sources = openrouter_models()
                    elif client and client.ready:
                        result = client.request("catalog")
                        catalog = sources = result.get("sources", {})
                        read_at = result.get("catalog_read_at") or time.strftime("%Y-%m-%d %H:%M:%S")
                        note = (f"当前 Qoder IDE 参考目录读取时间：{read_at}；"
                                "原生目录可能缓存（1.34.0 为 30 秒）或失败沿用旧值，联网更新时间未知。")
                except Exception:
                    sources = {}
                    if not use_idea:
                        catalog = {}
                self.events.put(("models", {"context": context, "base": base, "key": key,
                                           "no_auth": no_auth, "models": models, "sources": sources,
                                           "catalog": catalog, "catalog_note": note}))
            except ValueError as e:
                self.events.put(("fetch_error", str(e)))
            except Exception:
                self.events.put(("fetch_error", "模型列表读取失败；未记录响应正文或凭据。"))
        threading.Thread(target=run, daemon=True).start()

    def refresh_model_rows(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for mid, model in self.models.items():
            name, source = display_name(mid, model, self.catalog, self.active_client, self.mapping)
            caps = model.get("capabilities")
            caps = caps if isinstance(caps, dict) else {}
            context = model.get("context_length", caps.get("contextWindow", "未知"))
            tools, vision = caps.get("tools", "未知"), caps.get("vision", "未知")
            checked = "☑" if mid in self.checked_models else "☐"
            self.table.insert("", "end", iid=mid, values=(checked, name, mid,
                              f"上下文 {context} / 工具 {tools} / 视觉 {vision}", source))
        retained = [mid for mid in selected if mid in self.models]
        if retained:
            self.table.selection_set(retained)
        self.select_remote()

    def select_remote(self, _event=None):
        selected = self.table.selection()
        if selected:
            target = selected[0]
            row = self.table.item(target, "values")
            self.model_details.set(f"所选模型 {target}：{row[1]}\n{row[4]}")
            self.target_id.set(target)
            if not self.source_id.get() or self.source_id.get() == self.auto_source:
                self.auto_source = suggest_source_id(target, self.models[target], self.catalog, self.source_models,
                                                     self.active_client, self.mapping)
                self.source_id.set(self.auto_source)
        else:
            self.model_details.set("选中模型行，可在此查看完整名称来源与版本说明。")

    def update_batch_controls(self):
        self.batch_count.set(f"已勾选 {len(self.checked_models)} / {len(self.models)}")
        running = self.process is not None and self.process.poll() is None
        self.fetch_button.configure(state="disabled" if self.client_connecting or self.fetching or self.importing or running else "normal")
        self.batch_button.configure(state="normal" if self.checked_models and not running else "disabled")
        for button in self.mode_buttons:
            button.configure(state="disabled" if self.importing else "normal")
        locked = self.importing or self.fetching or self.client_connecting or running or self.owns_proxy
        self.client_choices.configure(state="disabled" if locked else "readonly")
        self.client_path_entry.configure(state="disabled" if locked else "normal")
        self.client_browse_button.configure(state="disabled" if locked else "normal")
        self.client_connect_button.configure(state="disabled" if self.importing or self.fetching or self.client_connecting else "normal")
        self.ide_import_button.configure(state="normal" if self.active_client == "qoder_ide" and self.checked_models and running
                                         and self.ide_client and self.ide_client.ready and self.client_supported
                                         and not self.importing and not self.proxy_connecting else "disabled")
        can_import = (self.active_client == "idea" and self.active_mode == "bridge" and self.checked_models and running
                      and self.idea_target.get() in self.idea_endpoints and not self.importing)
        self.idea_import_button.configure(state="normal" if can_import else "disabled")

    def refresh_idea_bridges(self):
        if self.active_client != "idea" or self.active_mode != "bridge":
            return
        self.idea_refresh_button.configure(state="disabled")
        def run():
            if self.active_client == "idea" and self.active_mode == "bridge":
                self.events.put(("idea_bridges", discover_bridges()))
        threading.Thread(target=run, daemon=True).start()

    def import_to_idea(self):
        try:
            if self.active_client != "idea" or self.active_mode != "bridge":
                raise ValueError("当前是手动添加模式；如需一键导入，请先选择桥接导入。")
            if self.importing or self.process is None or self.process.poll() is not None:
                raise ValueError("请先启动 Qoder 转发，再导入模型。")
            endpoint = self.idea_endpoints.get(self.idea_target.get())
            if endpoint is None:
                raise ValueError("请先安装并启用 IDEA 桥接插件，再点击刷新桥接。")
            selected = [mid for mid in self.models if mid in self.checked_models]
            models = idea_import_models(selected, self.models, self.mapping, self.catalog, self.source_models)
            if not models:
                raise ValueError("请先勾选要添加的模型。")
            request = {"request_id": str(uuid.uuid4()), "proxy_port": int(self.port.get()), "models": models}
        except (ValueError, TypeError) as error:
            self.error(str(error))
            return
        self.importing = True
        self.stop_button.configure(state="disabled")
        self.update_batch_controls()
        self.status.set("正在连接 IDEA；Qoder 原生窗口会预选这些模型。请在 IDEA 输入 local-proxy 并确认，内网 Key 不会发送给桥接。")
        def run():
            try:
                config = rpc("config/getGlobal")
                if config.get("proxyMode") != "manual" or config.get("httpProxy") != f"http://127.0.0.1:{request['proxy_port']}":
                    raise ValueError("Qoder 当前代理与本窗口不一致，请先连接本窗口的转发代理。")
                value = endpoint.request("/v1/imports", request)
                deadline = time.monotonic() + 900
                last_status = None
                while value.get("status") not in ("completed", "partial", "failed", "cancelled"):
                    if time.monotonic() > deadline:
                        raise ValueError("导入仍未结束，请先在 IDEA 完成或取消当前窗口；没有自动重复提交。")
                    if value.get("status") != last_status:
                        self.events.put(("idea_progress", value.get("status")))
                        last_status = value.get("status")
                    time.sleep(0.6)
                    value = endpoint.request("/v1/imports/" + request["request_id"])
                self.events.put(("idea_result", value))
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) else "IDEA 桥接连接中断，请在 IDEA 检查是否已完成；未重复提交。"
                self.events.put(("idea_error", message))
        threading.Thread(target=run, daemon=True).start()

    def toggle_model_check(self, mid):
        if mid not in self.models:
            return
        if mid in self.checked_models:
            self.checked_models.remove(mid)
        else:
            self.checked_models.add(mid)
        self.table.set(mid, "checked", "☑" if mid in self.checked_models else "☐")
        self.update_batch_controls()

    def check_model_click(self, event):
        mid = self.table.identify_row(event.y)
        if mid and self.table.identify_column(event.x) == "#1":
            self.table.selection_set(mid)
            self.table.focus(mid)
            self.table.focus_set()
            self.toggle_model_check(mid)
            return "break"

    def check_focused_model(self, _event):
        self.toggle_model_check(self.table.focus())
        return "break"

    def check_all_models(self, checked):
        self.checked_models = set(self.models) if checked else set()
        for mid in self.table.get_children():
            self.table.set(mid, "checked", "☑" if checked else "☐")
        self.update_batch_controls()

    def add_checked_mappings(self):
        if self.process and self.process.poll() is None:
            self.error("请先停止代理，再修改映射。")
            return
        selected = [mid for mid in self.models if mid in self.checked_models]
        if not selected:
            return
        try:
            additions, conflicts, unchanged = selected_mappings(selected, self.models, self.catalog,
                                                               self.source_models, self.mapping, self.active_client)
        except ValueError as error:
            self.error(str(error))
            return
        self.mapping.update(additions)
        self.refresh_maps()
        message = f"已加入 {len(additions)} 条映射；{unchanged} 条已存在。"
        if conflicts:
            message += "以下 ID 存在冲突，未覆盖：" + "、".join(conflicts) + "。请在单条编辑区确认。"
            from tkinter import messagebox
            messagebox.showwarning("部分模型存在映射冲突", message, parent=self.root)
        self.status.set(message)

    def refresh_maps(self):
        self.maps.delete(*self.maps.get_children())
        for source, target in self.mapping.items():
            self.maps.insert("", "end", values=(source, target))
        self.start_button.configure(text="2. 启动 Qoder 转发")

    def add_mapping(self):
        if self.process and self.process.poll() is None:
            self.error("请先停止代理，再修改映射。")
            return
        source, target = self.source_id.get().strip(), self.target_id.get()
        try:
            validate_mapping({source: target}, self.models)
        except ValueError as e:
            self.error(str(e))
            return
        self.mapping[source] = target
        self.refresh_maps()

    def remove_mapping(self):
        if self.process and self.process.poll() is None:
            self.error("请先停止代理，再修改映射。")
            return
        for row in self.maps.selection():
            self.mapping.pop(self.maps.item(row, "values")[0], None)
        self.refresh_maps()

    def config(self):
        base = normalize_base(self.base.get())
        try:
            port = int(self.port.get())
        except ValueError:
            raise ValueError("本地端口必须是整数。") from None
        if not 1 <= port <= 65535:
            raise ValueError("本地端口必须是 1–65535。")
        return {"base_url": base, "listen_port": port, "no_auth": bool(self.no_auth.get()),
                "model_map": dict(self.mapping), "qoder_enabled": True,
                "manage_qoder_proxy": bool(self.manage_qoder.get()),
                "integration_mode": self.active_mode, "client_type": self.active_client,
                "client_edition": self.client_edition, "client_path": self.client_path.get().strip()}

    def start(self):
        try:
            config = self.config()
            if config["base_url"] != self.auth_base or self.password.get().strip() or config["no_auth"] != self.auth_no_auth:
                raise ValueError("地址或凭据有变化，请先重新验证并拉取模型。")
            validate_mapping(self.mapping, self.models)
            if not self.mapping:
                raise ValueError("请先确认至少一个 Qoder 模型到内网模型的映射。")
            self.process, ca = start_proxy(config, self.key)
        except (ValueError, OSError) as e:
            self.error(str(e))
            return
        self.start_button.configure(state="disabled")
        self.fetch_button.configure(state="disabled")
        client = self.ide_client
        ide_can_configure = bool(client and client.ready and self.client_supported)
        self.proxy_connecting = config["manage_qoder_proxy"] and (self.active_client == "idea" or ide_can_configure)
        self.stop_button.configure(state="disabled" if self.proxy_connecting else "normal")
        self.update_batch_controls()
        self.status.set(f"正在启动 127.0.0.1:{config['listen_port']}；公开 CA：{ca}")
        process = self.process
        config_path = self.config_path
        def read():
            for line in process.stdout:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        allowed = {k: event[k] for k in ["event", "method", "path", "target", "status", "code", "model", "chat_enabled", "incoming_auth_present", "internal_auth_present", "success", "checked", "upstream_status"] if k in event}
                        self.events.put(("log", json.dumps(allowed, ensure_ascii=False)))
                        if event.get("event") == "ready":
                            try:
                                write_config(config_path, config)
                                saved = "非秘密配置已保存；API Key 未保存。"
                            except (ValueError, OSError) as error:
                                saved = f"本次配置未记住：{error}"
                            if not config["manage_qoder_proxy"] or self.active_client == "qoder_ide" and not ide_can_configure:
                                self.events.put(("qoder_unverified", f"本地转发已启动；未重新核验客户端代理，请确保指向 127.0.0.1:{config['listen_port']}。{saved}"))
                                continue
                            try:
                                if self.active_client == "qoder_ide":
                                    self.owns_proxy = True
                                    address = attach_ide_proxy(client, config["listen_port"], self.proxy_state, self.proxy_owner)
                                else:
                                    address = attach_proxy(config["listen_port"], self.proxy_state, owner=self.proxy_owner)
                                self.owns_proxy = True
                                self.events.put(("qoder_attached", address + "。" + saved))
                            except Exception as error:
                                self.events.put(("qoder_attach_error", f"本地转发已启动；未重新核验客户端代理，请确保指向 127.0.0.1:{config['listen_port']}。自动设置未完成：{error}。{saved}"))
                except ValueError:
                    self.events.put(("log", "代理有非结构化诊断输出，未展示原文；请检查端口或依赖。"))
            self.events.put(("exit", process.wait()))
        threading.Thread(target=read, daemon=True).start()

    def stop(self):
        if self.proxy_connecting or self.client_connecting:
            self.error("客户端连接操作尚未完成，请稍候再停止或关闭。")
            return False
        if self.importing:
            self.error("请先完成或取消当前模型导入，再停止转发或关闭窗口。")
            return False
        warning = ""
        try:
            if self.owns_proxy or self.active_client == "qoder_ide":
                message = self.finish_proxy_settings()
                self.events.put(("log", message))
                self.owns_proxy = False
        except Exception:
            if self.active_client == "qoder_ide":
                warning = "Qoder IDE 设置保持现状；活动记录未归档、仍待处理，请检查工具数据目录。"
                self.events.put(("log", warning))
            else:
                self.error("暂时无法恢复客户端原代理；本地代理保持运行。请重新连接所选客户端后重试停止。")
                return False
        stop_proxy(self.process)
        if warning:
            self.status.set("本地转发已停止；" + warning)
            self.error("本地转发已停止；" + warning)
        return True

    def save(self):
        from tkinter import filedialog
        try:
            config = self.config()
        except ValueError as e:
            self.error(str(e))
            return
        target = filedialog.asksaveasfilename(parent=self.root, initialdir=str(self.config_path.parent),
                                             initialfile=self.config_path.name, defaultextension=".json", filetypes=[("JSON", "*.json")])
        if target:
            try:
                write_config(target, config)
                self.config_path = Path(target)
                self.status.set("已保存非秘密配置；API Key 未保存。")
            except (ValueError, OSError) as error:
                self.error(f"本次配置未记住：{error}")

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "models":
                current = (self.active_client, self.client_edition,
                           self.ide_client if self.active_client == "qoder_ide" else None)
                if value["context"] != current:
                    continue
                self.fetching = False
                self.auth_base, self.key, self.auth_no_auth = value["base"], value["key"], value["no_auth"]
                self.models, self.source_models, self.catalog = value["models"], value["sources"], value["catalog"]
                self.catalog_status.set(value["catalog_note"])
                self.base.set(self.auth_base)
                self.password.set("")
                self.checked_models.intersection_update(self.models)
                self.refresh_model_rows()
                self.targets.configure(values=list(self.models))
                self.sources.configure(values=list(self.source_models))
                self.fetch_button.configure(state="normal")
                self.start_button.configure(state="normal")
                self.update_batch_controls()
                self.refresh_idea_bridges()
                self.status.set(f"认证成功，读取 {len(self.models)} 个模型。确认映射后启动，在 IDEA / Qoder 中添加相同模型 ID。")
                if self.auto_start:
                    self.auto_start = False
                    self.start()
            elif kind == "fetch_error":
                self.fetching = False
                self.auth_base = None
                self.key = ""
                self.fetch_button.configure(state="normal")
                self.update_batch_controls()
                self.error(value)
                self.status.set("本次认证或模型发现失败；下方保留上次成功结果，可修改后重试。")
                self.catalog_status.set("本次拉取失败，下方仍为上次成功结果；参考名称与实际上游版本不能等同。")
            elif kind == "idea_bridges":
                if self.active_client != "idea" or self.active_mode != "bridge":
                    continue
                self.idea_endpoints = {endpoint.title: endpoint for endpoint in value}
                self.idea_targets.configure(values=list(self.idea_endpoints))
                if self.idea_target.get() not in self.idea_endpoints:
                    self.idea_target.set(next(iter(self.idea_endpoints)) if len(self.idea_endpoints) == 1 else "")
                self.idea_refresh_button.configure(state="normal")
                self.mode_hint.set("桥接已识别；可将勾选模型导入所选 IDEA。"
                                   if self.idea_endpoints else
                                   "未发现可用桥接：请确认插件已启用且 IDEA / Qoder 版本兼容。也可切换为手动添加。")
                self.update_batch_controls()
            elif kind == "idea_progress":
                self.status.set("请在 IDEA 的 Qoder 原生窗口输入 local-proxy 并确认。" if value == "awaiting_user"
                                else "IDEA 正在验证并保存新模型，已有模型保持原配置。")
            elif kind in ("idea_result", "idea_error", "client_import_result", "client_import_error"):
                self.importing = False
                if self.process and self.process.poll() is None:
                    self.stop_button.configure(state="normal")
                self.update_batch_controls()
                if kind in ("idea_error", "client_import_error"):
                    self.status.set(value)
                    self.error(value)
                else:
                    counts = f"新增 {len(value.get('added', []))}，已存在 {len(value.get('skipped', []))}，失败 {len(value.get('failed', []))}，未勾选 {len(value.get('unselected', []))}。"
                    message = counts + value.get("message", "")
                    if kind == "client_import_result" and value.get("failed"):
                        message += "请检查客户端中的实际结果：" + "；".join(
                            f"{item.get('source_id', '')}: {item.get('native_code') or item.get('code', '')}" for item in value["failed"][:4])
                    self.status.set(message)
                    if value.get("status") in ("failed", "partial"):
                        self.error(message)
            elif kind == "client_connected":
                previous = self.ide_client
                self.ide_client, health, sources, problem = value
                self.client_connecting = False
                self.client_supported = not problem
                self.source_models = sources
                if previous is not self.ide_client:
                    self.catalog = {}
                    self.catalog_status.set("已连接当前 Qoder IDE；请重新拉取上游列表和该客户端的参考目录。")
                    self.refresh_model_rows()
                self.sources.configure(values=list(sources))
                edition = "国内版" if self.client_edition == "cn" else "国际版"
                self.mode_hint.set(f"已连接 Qoder IDE {edition} {self.ide_client.info['version']}；" +
                                   (problem or "原生模型入口可用，可一键添加；无需 IDEA 桥接插件。"))
                self.status.set(problem or "客户端已连接，可拉取模型、确认映射、启动转发后再一键添加。")
                self.update_batch_controls()
                if self.auto_start and self.client_supported:
                    self.fetch()
                elif problem:
                    self.auto_start = False
            elif kind == "client_error":
                self.client_connecting = False
                self.client_supported = False
                self.auto_start = False
                self.update_batch_controls()
                self.status.set(value)
                self.error(value)
            elif kind == "log":
                self.logs.configure(state="normal")
                self.logs.insert("end", value + "\n")
                self.logs.see("end")
                self.logs.configure(state="disabled")
                try:
                    event = json.loads(value)
                    if event.get("event") == "ready":
                        self.status.set(f"本地代理已监听 127.0.0.1:{self.port.get()}；正在确认启动状态。")
                except ValueError:
                    pass
            elif kind == "qoder_attached":
                self.proxy_connecting = False
                self.stop_button.configure(state="normal")
                self.update_batch_controls()
                policy = "停止后保留 Qoder IDE 代理设置。" if self.active_client == "qoder_ide" else "停止会恢复这个客户端的原代理。"
                self.status.set(f"所选客户端已连接 {value}。可添加映射模型；{policy}")
            elif kind == "qoder_attach_error":
                self.proxy_connecting = False
                self.stop_button.configure(state="normal")
                self.update_batch_controls()
                self.status.set(value)
            elif kind == "qoder_unverified":
                self.proxy_connecting = False
                self.stop_button.configure(state="normal")
                self.update_batch_controls()
                self.status.set(value)
            elif kind == "exit":
                self.proxy_connecting = False
                try:
                    restored = self.finish_proxy_settings() if self.owns_proxy or self.active_client == "qoder_ide" else ""
                    self.owns_proxy = False
                except Exception:
                    restored = ("Qoder IDE 设置保持现状；活动记录未归档、仍待处理，请检查工具数据目录。"
                                if self.active_client == "qoder_ide" else
                                "原代理尚未恢复；请重新连接所选客户端后停止，或按恢复记录手动恢复")
                self.process = None
                self.stop_button.configure(state="disabled")
                self.fetch_button.configure(state="normal")
                self.start_button.configure(state="normal" if self.auth_base else "disabled")
                self.update_batch_controls()
                self.status.set(f"代理已停止，退出状态 {value}。{restored}。")
        self.root.after(100, self.poll)

    def close(self):
        if not self.stop():
            return
        self.key = ""
        self.password.set("")
        if self.ide_client:
            self.ide_client.detach()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=NOTICE)
    parser.add_argument("--config", type=Path, help="非秘密 JSON 配置")
    parser.add_argument("--no-gui", action="store_true", help="明确选择命令行调试")
    parser.add_argument("--list-models", action="store_true", help="只认证并读取模型，不启动代理")
    parser.add_argument("--connect", action="store_true", help="使用已确认的配置和环境变量 Key 自动连接，并显示窗口")
    parser.add_argument("--mode", choices=("manual", "bridge"), help="模型添加方式；默认 manual，无需 IDEA 桥接插件")
    parser.add_argument("--client", choices=("idea", "qoder_ide"), help="目标客户端；默认保留已有 IDEA 模式")
    parser.add_argument("--edition", choices=("global", "cn"), help="Qoder IDE 国际版或国内版")
    parser.add_argument("--map", action="append", default=[], metavar="SOURCE=TARGET", help="明确的实际请求 model 映射，可多次提供")
    args = parser.parse_args()
    is_gui = not (args.no_gui or args.list_models)
    try:
        config_path = args.config or default_config_path()
        should_load = args.config is not None or (is_gui and config_path.exists())
        config = read_config(config_path) if should_load else {}
        if args.mode:
            config["integration_mode"] = args.mode
        if args.client:
            config["client_type"] = args.client
        if args.edition:
            config["client_edition"] = args.edition
        validate_config(config)
        config["integration_mode"] = get_integration_mode(config)
        config.setdefault("base_url", os.environ.get("INTERNAL_BASE_URL", DEFAULT_BASE))
        config["base_url"] = normalize_base(config["base_url"])
        config.setdefault("listen_port", int(os.environ.get("PROXY_PORT", "8899")))
        config.setdefault("no_auth", os.environ.get("INTERNAL_NO_AUTH") == "1")
        if not isinstance(config["no_auth"], bool):
            raise ValueError("no_auth 必须是 true 或 false。")
        config.setdefault("model_map", {})
        for item in args.map:
            source, sep, target = item.partition("=")
            if not sep:
                raise ValueError("--map 必须使用 SOURCE=TARGET。")
            config["model_map"][source] = target
        validate_config(config)
        if args.no_gui or args.list_models:
            key = os.environ.get("INTERNAL_API_KEY", "").strip()
            if not key and not config["no_auth"]:
                key = getpass.getpass("上游 API Key（不保存）: ").strip()
            models = fetch_models(config["base_url"], key, config["no_auth"])
            client_type, _ = get_client(config)
            catalog = local_catalog()[0] if client_type == "idea" else {}
            if args.list_models:
                for mid, model in models.items():
                    name, source = display_name(mid, model, catalog, client_type, config["model_map"])
                    print(json.dumps({"id": mid, "display_name": name, "name_source": source}, ensure_ascii=False))
                return 0
            validate_mapping(config["model_map"], models)
            process, ca = start_proxy(config, key)
            print("公开 CA：" + str(ca), flush=True)
            def interrupted(*_):
                stop_proxy(process)
            signal.signal(signal.SIGTERM, interrupted)
            try:
                for line in process.stdout:
                    print(line.rstrip(), flush=True)
                return process.wait()
            finally:
                stop_proxy(process)
        try:
            import tkinter as tk
        except ImportError:
            raise ValueError("当前 Python 缺少 Tk；请使用含 Tcl/Tk 的 Python，或设置 PROXY_GUI_PYTHON。") from None
        root = tk.Tk()
        config["_connect_on_start"] = args.connect
        config["_config_path"] = str(config_path)
        App(root, config)
        root.mainloop()
        return 0
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        if is_gui:
            try:
                import tkinter as tk
                from tkinter import messagebox
                error_root = tk.Tk()
                error_root.withdraw()
                try:
                    messagebox.showerror("配置无法使用", str(e), parent=error_root)
                finally:
                    error_root.destroy()
            except Exception:
                print("无法显示配置错误对话框，请检查非秘密配置文件。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

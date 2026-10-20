# 安装与使用

[返回 README](../README.md) · [兼容范围](COMPATIBILITY.md) · [故障排查](TROUBLESHOOTING.md)

## 获取方式

当前处于实验性/Beta源码阶段，尚未发布 GitHub Releases 安装包。历史 Mac 1.1.3 / Windows 1.1.2 只用于说明已完成的内部验证，不作为本仓库的公开下载物。先使用下面的源码安装方式；发行构建输入见 [BUILD.md](../BUILD.md)。

项目名是 Qoder Model Bridge，现有界面仍显示「Qoder 内网模型接入」。

它包含桌面助手、本机转发器与可选IDEA桥接插件，不能只靠插件市场中的一个安装动作完成接入。

## 运行前准备

1. 安装兼容的 Qoder IDE，或 IntelliJ IDEA 与官方 Qoder 插件。账号与原生 BYOK / 供应商能力必须可用；本工具不会绕过这些检查。
2. 准备满足下列接口要求的网关；不要只凭“兼容 OpenAI”的宣传就假定所有模型能力都可用。
3. 确认网关的 Base URL、API Key，以及 `/v1/models` 返回的实际模型 ID。`http://127.0.0.1:3000/v1` 是源码中的通用示例，没有随本项目附带服务。
4. 选择空闲的本机监听端口。两个工具窗口不能共用同一端口。
5. 阅读下面的 HTTPS / CA 要求。运行时仅监听 `127.0.0.1`，不是向局域网发布的网关服务。

### 网关必要接口

假设 Base URL 为 `https://gateway.example.com/v1`（虚构示例，必须替换）：

| 接口 | 必要行为 |
| --- | --- |
| `GET <Base URL>/models`，即示例的 `/v1/models` | 返回JSON对象，包含非空 `data` 数组；每个条目有有效字符串 `id`，例如 `{"data":[{"id":"gateway-model-a"}]}` |
| `POST <Base URL>/chat/completions`，即示例的 `/v1/chat/completions` | 接受对应模型的OpenAI Chat Completions请求；流式请求需支持SSE，当前原生转换默认请求流式 |

仅提供Responses API、Anthropic Messages API或网页聊天界面不满足上述要求。Base URL填API前缀，不填完整的 `/models` 或 `/chat/completions` 动作地址。无路径的HTTP(S)地址会补 `/v1`；其他前缀应按服务实际地址填写。

鉴权使用 `Authorization: Bearer <Key>`，除非上游确实无需鉴权。工具不跟随模型列表接口的重定向；应填写最终授权地址。不接受包含用户名/密码、查询参数或片段的Base URL，也不把OpenRouter官方域名作为本工具的自有上游。

## macOS 源码运行

安装包含 Tcl/Tk 的标准64位 CPython 3.13。历史验证使用3.13.16；系统自带或其他发行方式的 Python 不一定包含 Tk。

从项目根目录运行：

```sh
cd app
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python launcher.py
```

无需激活虚拟环境。上面的命令把 `mitmdump` 安装到启动器实际查找的 `app/.venv/bin/`；不要改成只在仓库根目录建立 `.venv`。

## Windows 源码运行

安装带 Tcl/Tk 的64位 CPython 3.13，并确保 Python Launcher 的 `py -3.13` 指向该版本。

从项目根目录使用 PowerShell：

```powershell
cd app
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launcher.py
```

本次没有 Windows 新版实机源码安装验收，见[兼容矩阵](COMPATIBILITY.md)。`app/bootstrap.py`、`app/start.cmd` 是原Windows离线发行材料，依赖额外的 Python ZIP / wheels；fresh clone 不包含这些资产，不能直接把它们当作一键安装器。

## Mac / Windows 发行应用

当前没有公开安装包。历史 Mac 应用采用自包含运行时，Windows采用当前用户目录离线安装；这些结果不能代替未来公开发行包的签名、安装与真实客户端验收。历史 Mac 为 ad-hoc 签名、未公证，Windows EXE 未做 Authenticode 签名。

以后更换已安装版本时，应先保存非秘密配置，停止转发并正常退出旧窗口，再安装或替换。新文件不会热更新已打开的旧窗口。不要通过关闭 Gatekeeper、全局清除隔离属性或关闭证书验证来替代安装排查。

## Qoder IDE 首次配置和原生添加

1. 正常退出准备连接的 Qoder IDE。其他 IDEA 或独立 Qoder 桌面 Agent 不需要因此退出。
2. 在工具选择国际版或国内版，必要时用「选择程序」指定安装位置。
3. 点击「连接 / 启动 Qoder IDE」，在客户端中完成登录。工具不替你处理验证码、账号或产品授权。
4. 输入自己的 Base URL 和 Key，点击「1. 验证 Key 并拉取模型」。确实无需鉴权的上游才选择「上游明确无需鉴权」。
5. 勾选模型，点击「一键加入映射」，核对源 ID 与实际转发 ID。
6. 启用「启动时设置 Qoder IDE 代理，停止后保留」并点击「2. 启动 Qoder 转发」。控制连接可用时才会重新设置并核验客户端代理。
7. 点击「一键添加到所选 Qoder IDE」。通过原生校验后保存 `local-proxy` 占位凭据，真实网关 Key 仍用于本地转发。
8. 在 IDE 模型菜单选择新增模型，并保持转发运行。重复添加会跳过已有模型，不自动重写已有模型信息。

当前适配依赖客户端目录中的原生能力，包含 OpenRouter 入口及账号检查。供应商入口缺失、账号不支持或版本不兼容时，不保证能进行原生导入。国内版不是仅按界面语言区分，详见[兼容说明](COMPATIBILITY.md)。

## Qoder IDE 已配置后的再次使用

如果原 `http.proxy` 仍正确指向工具端口，可正常打开 IDE，再打开工具、重新输入 Key、拉取模型并启动**同端口**本地转发。

没有控制连接时，工具会明确显示「未重新核验客户端代理」。这只证明本地转发已启动，不证明 IDE 当前配置正确。首次配置、端口改变后的自动设置、原生目录读取和新模型导入仍需建立控制连接。

## IDEA 手动添加

手动模式不需要桥接插件，也不会检查 IDEA Build 或发现桥接：

1. 在工具选择IDEA与「手动添加」，拉取模型、确认映射并启动转发。
2. 打开Qoder插件的原生模型添加窗口，使用其已提供的OpenRouter入口；具体菜单以当前插件版本为准。
3. 填写与你映射一致的**源模型ID**，凭据使用 `local-proxy`，按网关实际能力配置后完成原生校验。真实网关Key仅填写在转发工具中。
4. 在Qoder模型菜单选择新增模型，保持转发运行。

当前版本或账号没有该原生入口时，这条路径不可用；不通过手工改写客户端内部文件绕过账号/能力校验。

## IDEA 桥接插件

桥接模式必须使用[精确兼容版本](COMPATIBILITY.md)。仓库不附带预编译桥接 ZIP，也不分发 IDEA / 官方 Qoder 插件的 JAR。先安装所需客户端和官方插件，再构建本项目桥接。

从项目根目录运行；用自己的安装位置替换尖括号占位值：

```sh
app/.venv/bin/python app/idea-bridge/build.py --ide-home "<IDEA_HOME>" --qoder-plugin "<QODER_PLUGIN_DIR>"
```

Windows PowerShell：

```powershell
.\app\.venv\Scripts\python.exe app\idea-bridge\build.py --ide-home "<IDEA_HOME>" --qoder-plugin "<QODER_PLUGIN_DIR>"
```

macOS 的 `IDEA_HOME` 指 IDEA 应用的 `Contents`；Windows 指其安装根目录。`QODER_PLUGIN_DIR` 指已安装的 Qoder 官方插件目录，里面应有 `lib/*.jar`。构建脚本使用所选IDE的 JBR `javac` / `java`，以 Java 21 为目标；该环境必须实际包含编译器。脚本不会替你安装客户端。

成功构建后得到 `app/idea-bridge/.build/qoder-internal-bridge-0.1.2.zip`：

1. IDEA：Settings → Plugins → 齿轮 → Install Plugin from Disk，选择刚构建的 ZIP；如 IDE 提示重启，正常重启。
2. 在 Find Action 中执行 `Qoder Internal Bridge: Enable`。
3. 工具选择「桥接导入」，拉取模型、加入映射并启动转发。
4. 点击「刷新桥接」，选中目标 IDEA，再点击「一键添加到 IDEA / Qoder」。
5. 原生窗口中输入 `local-proxy` 并确认。不要在这里填写真实网关 Key。新增、跳过与失败结果会分别显示。

原生校验或导入成功不等于该模型的真实推理能力已验收。

## 映射与模型名称

| 字段 | 含义 |
| --- | --- |
| 源模型 ID | Qoder 客户端请求中携带、需要被匹配的 ID |
| 实际转发 ID | 网关 `/v1/models` 返回、实际发给上游的 ID |
| 显示名称 | 用户可读标签或参考信息，不是模型真实版本证明 |

例如 `client-model-a → gateway-model-a` 只是虚构示例；请使用自己的真实返回值。不要把展示名当作请求 ID。

每次成功拉取会刷新列表。Qoder IDE 参考名来自当前连接客户端，IDEA 则读取本机目录；Qoder IDE 1.34.0 的原生接口可能缓存30秒或失败时沿用旧值。读取时点不等于联网更新时间，不能据别名推断实际模型版本。

## 配置、Key 与升级

| 启动方式 | 普通GUI的默认非秘密配置 |
| --- | --- |
| 源码运行 | `app/proxy-config.json` |
| 历史Mac应用 | `~/Library/Application Support/QoderInternalProxy/proxy-config.json` |
| 历史Windows安装包 | 实际安装目录中的 `proxy-config.json` |

显式 `--config` 优先，并成为本次活动保存文件。在 `app/` 内运行的示例：

```sh
.venv/bin/python launcher.py --config my-config.json
```

`my-config.json` 需是你准备好的、符合 `config.example.json` 结构的非秘密配置。不要加入 Key。手动另存后，本次自动保存继续写同一文件；外部文件下次仍需通过 `--config` 指定。

转发成功监听后会自动保存非秘密字段。目标文件含未知/敏感字段或非法内容时会拒绝覆盖，并明确提示本次配置未记住。Key不写入该文件，重新打开仍需输入；`--connect` 也不是免 Key 或免校验开关。

旧窗口尚未保存的映射不会自动迁移。升级前使用「保存非秘密配置」，再按旧工具的停止规则正常退出。

## HTTPS 与本地 CA

本地转发会为受限目标进行 HTTPS 代理。mitmproxy 在工具运行数据的 `.runtime/certs/` 中生成 CA，状态行会显示公开文件 `mitmproxy-ca-cert.pem` 的路径。

- 客户端必须能够信任本地代理连接；工具不会自动导入系统/IDE证书库，也没有「信任证书」按钮。
- `mitmproxy-ca-cert.pem` 是公开证书；`mitmproxy-ca.pem` 含 CA 私钥。不要上传整个证书目录或私钥。
- Qoder客户端信任本地代理CA，与Python运行时信任上游HTTPS网关证书，是两个独立问题。内网网关使用私有CA时，需要按你的客户端、运行时和组织策略配置相应信任。
- 当前文档不提供未经本客户端组合验证的通用系统导入命令，不建议关闭TLS校验或设置 `http.proxyStrictSSL=false`。实现中的 `upstream_cert=false` 是关闭上游证书探测，不是关闭TLS验证。

原理及各平台信任背景见[mitmproxy 官方证书文档](https://docs.mitmproxy.org/stable/concepts/certificates/)。它使用的默认目录可能与本工具不同，应以本工具实际显示路径为准。

## 停止、卸载与恢复连接

Qoder IDE：停止/退出只停止本工具启动的转发，IDE保留 `http.proxy`。若停止使用工具，需要在**所选IDE的本机用户设置**中恢复原代理或直连；原来有公司代理时，应恢复原值，而非随意清空。不要改工作区或其他客户端的代理来代替它。

IDEA：正常恢复本工具管理的代理；失败时保留窗口和转发并提示处理，恢复完成后再退出。

卸载工具或禁用桥接不会删除已经添加的模型，也不会清理IDE保留设置。用户配置与证书目录也会保留。若安装器提示还有活动记录，应先由原工具正常停止/退出并确认归属；不要直接删除记录绕过检查。

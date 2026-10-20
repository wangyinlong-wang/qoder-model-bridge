# Qoder Model Bridge

[English](README.en.md) · [安装与使用](docs/INSTALLATION.md) · [兼容范围](docs/COMPATIBILITY.md) · [故障排查](docs/TROUBLESHOOTING.md)

非官方的 Qoder 模型接入工具：把你自己的 OpenAI 兼容模型网关接到 **Qoder IDE** 或 **IntelliJ IDEA 中的 Qoder 插件**，提供模型发现、ID 映射、本地转发与原生模型导入。

项目由**桌面助手、本机转发器、可选IDEA桥接插件**组成。使用时需要启动本机转发，IDEA桥接按需另装。

模型接入方向是：**自有网关的模型 → 本工具 → Qoder 客户端**。模型资源和计费来自你的网关；使用时仍需满足Qoder账号、供应商能力与原生校验，完整Qoder工作流仍可能依赖官方联网服务。

项目名为 `qoder-model-bridge`，公开版本窗口名为 **Qoder Model Bridge**。旧内部版本窗口名是「Qoder 内网模型接入」。

## 当前提供什么

- 从网关的 `/v1/models` 发现模型，维护客户端模型 ID 到实际网关 ID 的映射。
- 刷新模型列表、保留有效勾选，并区分上游声明名称、客户端参考名称与未核实的版本。
- Qoder IDE：使用其自身运行时连接客户端，读取当前原生目录，并通过原生接口添加模型。
- IDEA：保留手动添加模式；可选 Java 桥接插件将选中模型交给 Qoder 插件的原生添加、校验和保存流程。
- 保存和下次加载非秘密配置；网关 Key 不写入该配置文件，重新打开工具后仍需输入。

## 下载安装包

首个公开版本为实验性 **v1.1.4-beta.1**。在[该版本的 GitHub Releases 页面](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/tag/v1.1.4-beta.1)选择：

| 平台 | 文件 |
| --- | --- |
| macOS Apple芯片 / Intel | [Qoder-Model-Bridge-1.1.4-beta.1-macos-universal.dmg](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/Qoder-Model-Bridge-1.1.4-beta.1-macos-universal.dmg) |
| Windows 10/11 x64 | [Qoder-Model-Bridge-1.1.4-beta.1-windows-x64-setup.exe](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/Qoder-Model-Bridge-1.1.4-beta.1-windows-x64-setup.exe) |
| 可选IDEA桥接（两安装包也已包含） | [qoder-local-model-bridge-0.1.2.zip](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/qoder-local-model-bridge-0.1.2.zip) |
| 第三方对应源码 / 校验 | [对应源码ZIP](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip) / [SHA256SUMS](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/SHA256SUMS) |

安装包自带运行时；Mac为ad-hoc签名、未公证，Windows未做Authenticode签名。Windows本版只做安装器编译、解包与静态检查，**未完成Windows实机验收**。安装、升级和系统放行步骤见[安装指南](docs/INSTALLATION.md#mac--windows-发行应用)，实际覆盖见[兼容范围](docs/COMPATIBILITY.md)。源码运行方式保留如下。

历史Mac1.1.3和Windows1.1.2内部安装包不作为公开资产。构建输入见 [BUILD.md](BUILD.md)；fresh clone不包含完整runtime/wheels。

## 适合哪些场景

- 已有公司内网、私有部署或自建的 OpenAI 兼容网关，希望继续使用 Qoder IDE / IDEA 工作流。
- 需要发现多个模型、维护不同模型 ID，并减少重复的原生添加操作。
- 能接受本地转发常驻，并愿意按兼容版本和证书要求配置客户端。

Qoder 的独立桌面 Agent 产品与 Qoder IDE 是两条产品线。[官方概述](https://docs.qoder.com/qoder/overview)说明它们并行存在；独立桌面 Agent 已有[自定义 Base URL](https://docs.qoder.com/qoder/custom-models)。如果你使用的是该桌面 Agent，且原生配置已经满足需求，可以直接使用官方入口。本项目主要处理 Qoder IDE / IDEA 插件的接入。

## 源码快速开始

先安装带 Tcl/Tk 的 **64 位 CPython 3.13**。历史打包验证使用 3.13.16；依赖声明范围与未验证项见[兼容说明](docs/COMPATIBILITY.md)。

从项目根目录执行。虚拟环境必须放在 `app/.venv`，因为启动器会从这里查找独立的 `mitmdump`。

### macOS

```sh
cd app
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python launcher.py
```

### Windows PowerShell

```powershell
cd app
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launcher.py
```

依赖安装需要网络。本仓库没有附带 Python 运行时或离线 wheels；`bootstrap.py` / `start.cmd` 是离线发行包材料，不是 fresh clone 的一键启动方式。

## 最短使用流程

1. 准备你能访问的网关、API Key 和可用模型。网关必须提供 `GET <Base URL>/models`（非空 `data` 数组、有效 `id`）与 `POST <Base URL>/chat/completions`；流式请求还需SSE支持。示例地址 `http://127.0.0.1:3000/v1` 只是占位，本项目不会在该地址提供模型服务；请填写你自己的 Base URL。
2. 选择客户端。Qoder IDE 首次设置时，先正常退出所选 IDE，再点击「连接 / 启动 Qoder IDE」并完成客户端登录。IDEA 可选择手动模式，或按[安装指南](docs/INSTALLATION.md#idea-桥接插件)安装随包桥接。
3. 输入 Base URL 和 Key，点击「1. 验证 Key 并拉取模型」。仅在上游确实无需鉴权时选择「上游明确无需鉴权」。
4. 勾选模型，点击「一键加入映射」，核对**源模型 ID → 实际转发 ID**。显示名称不是实际请求 ID，也不能证明模型版本。
5. 点击「2. 启动 Qoder 转发」，再按需使用原生导入按钮。使用 Qoder IDE 模型时保持本地转发运行。

Qoder IDE 的首次代理设置、原生目录读取和新增模型需要可用控制连接。已经保留了正确代理地址的客户端，可在重新输入 Key、拉取模型后直接启动同端口转发；此时若没有控制连接，工具只确认本地服务已启动，并明确提示**未重新核验客户端代理**。

## 停止与退出：两种客户端不同

| 客户端 | 停止或正常退出后的行为 |
| --- | --- |
| Qoder IDE | 只停止本工具启动的转发，IDE保留 `http.proxy`。 |
| IDEA / Qoder 插件 | 正常恢复本工具管理的代理；失败时保留窗口与转发并提示处理。 |

**Qoder IDE 的代理保留后，本地转发停止期间该端口不提供服务。** 下次启动同端口转发，或在所选 IDE 的本机用户设置中恢复原代理/直连。卸载工具不会替你移除 IDE 中已保留的代理和模型；请先处理这些设置。不要用删除活动记录或结束未知进程的方式绕过保护。

## HTTPS、证书与 Key

本地转发使用 mitmproxy，为受限目标连接生成本地 CA。状态行会显示公开 CA 路径；工具没有自动导入系统/IDE 证书库或「一键信任证书」操作。客户端需要信任这条代理连接，使用私有 CA 的上游网关也需要相应的运行时信任配置。参见[证书说明](docs/INSTALLATION.md#https-与本地-ca)。

真实网关 Key 由转发进程在运行期间使用，不写入非秘密配置。原生导入使用 `local-proxy` 占位凭据；IDEA 桥接模式会让你在原生窗口确认。不要把配置、证书私钥或原始请求日志上传到仓库。

模型能被添加不代表所有聊天、工具调用、视觉或长上下文能力都经过真实 API 验证。平台、版本及实际验证范围见[兼容矩阵](docs/COMPATIBILITY.md)。

## 项目组成

| 位置 | 用途 |
| --- | --- |
| `app/launcher.py` | GUI、模型发现与映射、配置和转发生命周期 |
| `app/qoder_to_internal.py`、`app/qoder_protocol.py` | 本地转发与协议适配 |
| `app/qoder_clients.py`、`app/client_api.js`、`app/client_relay.cjs` | Qoder IDE 识别与原生控制连接 |
| `app/idea-bridge/` | 可选 IDEA 桥接源码与构建入口 |
| `packaging/` | Mac / Windows 发行构建资料，不包含完整运行时 |

## 反馈与许可

反馈时请提供操作步骤、系统/架构、客户端与插件版本、启动方式和脱敏后的简短错误。不要附真实 Key、内网实际地址、个人绝对路径、完整配置或证书私钥。

本项目自有代码使用 [MIT License](LICENSE)。Python、Tcl/Tk、mitmproxy、NSIS 等第三方组件仍遵守各自许可证，见[第三方说明](THIRD_PARTY_NOTICES.md)。本仓库不提供 Qoder、IntelliJ IDEA 或 Qoder 官方插件本体，也不是其官方项目。

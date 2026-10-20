# Qoder Model Bridge

[中文](README.md) · [Installation (中文)](docs/INSTALLATION.md) · [Compatibility (中文)](docs/COMPATIBILITY.md) · [Troubleshooting (中文)](docs/TROUBLESHOOTING.md)

An unofficial bridge connecting Qoder IDE and its IntelliJ IDEA plugin to your own OpenAI-compatible model gateways, with model discovery and mapping.

It consists of a **desktop helper, a local forwarder, and an optional IDEA bridge plugin**. Run the local forwarder while the client uses it, and add the IDEA bridge when needed.

The direction is **your model gateway → Qoder clients**. Your gateway supplies the models and bills usage; Qoder account, provider availability and native validation requirements still apply, and the full Qoder workflow may require official online services. The public GUI is named **Qoder Model Bridge**; older internal builds used **Qoder 内网模型接入**.

## What it does

- Discovers models through your gateway's `/v1/models` and maps client IDs to actual upstream IDs.
- Connects to Qoder IDE using its own runtime and uses native model import/validation.
- Supports manual IDEA setup and an optional, version-specific IDEA bridge.
- Saves non-secret settings. The gateway Key is not written into that configuration and must be entered again after reopening.

The independent **Qoder desktop Agent** and **Qoder IDE** are separate products. The desktop Agent already supports [custom Base URLs](https://docs.qoder.com/qoder/custom-models); see the [official product overview](https://docs.qoder.com/qoder/overview). This bridge targets Qoder IDE and the Qoder plugin in IDEA.

## Distribution status

The first public version is the experimental **v1.1.4-beta.1** prerelease. Open its [GitHub release page](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/tag/v1.1.4-beta.1) and select:

- macOS universal: [Qoder-Model-Bridge-1.1.4-beta.1-macos-universal.dmg](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/Qoder-Model-Bridge-1.1.4-beta.1-macos-universal.dmg)
- Windows 10/11 x64: [Qoder-Model-Bridge-1.1.4-beta.1-windows-x64-setup.exe](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/Qoder-Model-Bridge-1.1.4-beta.1-windows-x64-setup.exe)
- Optional IDEA bridge, also included in both packages: [qoder-local-model-bridge-0.1.2.zip](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/qoder-local-model-bridge-0.1.2.zip)
- Third-party corresponding source: [Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip)
- Checksums: [SHA256SUMS](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/download/v1.1.4-beta.1/SHA256SUMS)

The Mac app is ad-hoc signed and **not notarized**. The Windows EXE has **no Authenticode signature**; its validation is limited to installer compilation, extraction and static checks, with no Windows-machine acceptance. See the [installation steps (中文)](docs/INSTALLATION.md#mac--windows-发行应用) for installation, checksums, upgrades and Apple's per-app approval flow. Source execution remains available below.

Historical internal Mac 1.1.3 / Windows 1.1.2 installers are not public assets. See [BUILD.md](BUILD.md) for the external runtime inputs needed to build installers.

## Source quickstart

Install standard **64-bit CPython 3.13 with Tcl/Tk** first. Historical bundled validation used 3.13.16. From the project root:

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

Keep the virtual environment at `app/.venv`: the launcher looks there for `mitmdump`. Dependency installation needs network access. The repository does not contain the offline Python/wheel payload; `bootstrap.py` and `start.cmd` belong to offline installer preparation and are not a fresh-clone quickstart.

## Use it

1. Prepare a reachable gateway and Key. It must implement `GET <Base URL>/models` with a nonempty `data` array of valid IDs and `POST <Base URL>/chat/completions`, including SSE for streaming requests. The sample `http://127.0.0.1:3000/v1` is a placeholder, not a model server supplied by this project.
2. Select the client. For first-time Qoder IDE configuration, quit that IDE normally, then use **连接 / 启动 Qoder IDE** and sign in. For IDEA, use manual mode or install the bundled optional bridge as described in the [Chinese installation guide](docs/INSTALLATION.md#idea-桥接插件).
3. Enter your Base URL and Key; use **1. 验证 Key 并拉取模型** to fetch the model list.
4. Select models, use **一键加入映射**, and verify each client ID → upstream ID pair.
5. Start forwarding with **2. 启动 Qoder 转发**, then use native import when needed. Keep forwarding running while the client uses the local proxy.

First proxy configuration, native catalog access and model import require an IDE control connection. If the correct proxy is already retained, you may start forwarding on the same port without reconnecting the control channel; the UI explicitly reports that the client proxy was **not reverified**.

## Stop and trust behavior

- **Qoder IDE retains `http.proxy` after Stop/Exit.** The stopped port has no service. Restart forwarding on the same port or restore the IDE's previous user proxy/direct connection. Uninstalling the helper does not remove retained IDE settings or models.
- **IDEA mode restores its managed proxy.** If restoration fails, the GUI and forwarder remain available for retry.
- The proxy generates a local CA but does not automatically install it in system/IDE trust stores or turn off TLS validation. See [HTTPS/CA setup (中文)](docs/INSTALLATION.md#https-与本地-ca).
- Native import uses the `local-proxy` placeholder credential. Do not place your actual gateway Key in the native import dialog.

Compatibility is intentionally limited. See the [matrix (中文)](docs/COMPATIBILITY.md) for exact IDEA/Qoder plugin versions and the difference between package targets, mock checks and actual hardware validation. Model import does not prove every model's inference, tool, vision or long-context capabilities.

## License

Project-owned code is licensed under [MIT](LICENSE). Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Do not publish your local configuration, CA private keys, credentials or raw request logs.

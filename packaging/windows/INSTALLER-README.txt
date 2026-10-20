Qoder Model Bridge 1.1.4-beta.1 — Windows x64

Download/checksums: https://github.com/wangyinlong-wang/qoder-model-bridge/releases/tag/v1.1.4-beta.1
Asset: Qoder-Model-Bridge-1.1.4-beta.1-windows-x64-setup.exe

This experimental installer includes CPython 3.13.16, Tcl/Tk 8.6.15 and the pinned proxy dependencies. Setup uses local bundled files and installs for the current user, normally under %LOCALAPPDATA%\Programs\QoderInternalProxy. It does not include a model service. Fill in your own gateway, Key and model mappings; http://127.0.0.1:3000/v1 is only a placeholder.

The EXE is not Authenticode-signed. It has been prepared for NSIS/archive/hash/static checks, not a Windows-machine install/GUI/upgrade/uninstall acceptance test. Verify the publisher/source and SHA256 before running. Follow your organization's software approval policy; do not turn off system protection.

Before an upgrade, save non-secret configuration in the old window, stop forwarding and exit normally. Both old Chinese and new English window titles are protected. Active proxy records block installation/uninstallation; do not delete records to bypass this. Qoder IDE retains http.proxy after Stop/Exit; restart forwarding on the same port or restore its previous proxy/direct settings manually. IDEA must complete its managed proxy restoration; on failure keep the tool open and retry.

Use the Qoder Model Bridge desktop/Start Menu shortcut. The existing data location/schema remains unchanged. The ordinary GUI configuration is proxy-config.json in the installation root. Gateway Keys are not saved there and must be entered again. Unsaved in-memory mappings are not migrated, and new files do not update an already open old window. Unknown files, saved configuration and certificates are preserved by the uninstaller.

Optional IDEA plugin: idea-bridge\qoder-local-model-bridge-0.1.2.zip. Install it from disk in the exact supported IDEA/Qoder versions; no JDK is required for the prebuilt plugin. This ZIP contains project classes/metadata/license only, no vendor JARs. See docs\INSTALLATION.md and docs\COMPATIBILITY.md.

Project MIT: LICENSE. Original third-party texts: ThirdPartyLicenses\ and NSIS-COPYING.txt. Source URLs/hashes are in ThirdPartyLicenses\CORRESPONDING-SOURCES.json. The release supplies Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip for the listed corresponding sources. Third-party components retain their own terms; this is not a blanket MIT relicensing or a complete independent legal audit.

For source execution use the repository's app/.venv setup, not this installer bootstrap without its complete offline inputs.

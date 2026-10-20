# Third-party notices

The root [MIT license](LICENSE) covers this project's original code and documentation. It does not replace the licenses of dependencies, build tools, client applications, or redistributed runtimes.

The Git repository contains project source and packaging scripts, not the runtime archives/wheels. The separate v1.1.4-beta.1 desktop assets include a clean public application build, CPython, Tcl/Tk and platform-specific dependencies. Historical privately configured installers are not public assets.

| Component | Use | Upstream license / notice |
| --- | --- | --- |
| mitmproxy 12.2.3 | Proxy runtime declared in `app/requirements.txt` | [MIT license](https://github.com/mitmproxy/mitmproxy/blob/v12.2.3/LICENSE) |
| CPython | Source runtime; offline templates target 3.13.16 | [PSF License Version 2 and incorporated-software notices](https://docs.python.org/3.13/license.html) |
| Tcl / Tk | Tkinter GUI; macOS 8.6.18, Windows 8.6.15 | Original runtime `license.terms`; upstream [Tcl 8.6.18](https://github.com/tcltk/tcl/blob/core-8-6-18/license.terms), [Tk 8.6.18](https://github.com/tcltk/tk/blob/core-8-6-18/license.terms), [Tcl 8.6.15](https://github.com/tcltk/tcl/blob/core-8-6-15/license.terms) |
| certifi | Mozilla-derived CA bundle used by the macOS bootstrap fallback | [MPL-2.0 notice for the CA bundle](https://github.com/certifi/python-certifi/blob/master/LICENSE) |
| NSIS 3.13 | Windows installer stub/plugins | Component-specific terms are retained verbatim in the Windows installation's `NSIS-COPYING.txt` and [repository copy](https://github.com/wangyinlong-wang/qoder-model-bridge/blob/v1.1.4-beta.1/packaging/windows/NSIS-COPYING.txt) |

Other Python packages and native dependencies retain their own licenses. The Mac inputs contain 42 wheels per architecture (including existing architecture-specific argon2-cffi-bindings versions); the Windows input contains 43 wheels. Their exact filenames, versions, hashes, upstream links and preserved notice files appear in each package's `ThirdPartyLicenses/inventory.json`.

- macOS: `Qoder Model Bridge.app/Contents/Resources/ThirdPartyLicenses/`.
- Windows: `ThirdPartyLicenses/` in the installation root, alongside the unmodified `NSIS-COPYING.txt`.

Original license bytes are extracted from the actual wheels and runtime. The mitmproxy_rs / mitmproxy-macos 0.12.11 wheels omit their MIT notice, so the collection supplements it with the unmodified [official v0.12.11 source license](https://github.com/mitmproxy/mitmproxy_rs/blob/v0.12.11/LICENSE), recording its origin and hash. CPython's incorporated notices and the Windows bundled pip/vendor notices are retained as supplied.

IDEA, the Qoder plugin, Qoder IDE, their Java/Electron runtimes, and their artwork are not redistributed here. The optional bridge compiles against libraries from a developer's separately installed IDE and plugin. Its distributable ZIP contains only this project's classes, plugin metadata and MIT/NOTICE, with no vendor JARs.

## LGPL / MPL components and corresponding source

The [v1.1.4-beta.1 release](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/tag/v1.1.4-beta.1) includes `Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip`. It supplies the following unmodified, versioned upstream source archives, with original URLs and SHA-256 hashes in `SOURCES.json`:

| Source | Terms / reason |
| --- | --- |
| ldap3 2.9.1 | LGPLv3 Python package |
| urwid 3.0.5 | LGPL-2.1-only package/extension |
| pydivert 2.1.0 | LGPLv3 package, including its WinDivert integration |
| WinDivert 1.3.0 and 2.2.2 | Sources/build scripts for the bundled DLL/driver versions; this distribution selects the LGPLv3 option from upstream's dual LGPLv3/GPLv2 terms |
| mitmproxy_rs 0.12.11 | Rust/native source, build materials and Cargo.lock; covers the mitmproxy native-component project |
| certifi 2026.10.18 | MPL-2.0 CA data/package source |
| publicsuffix2 2.20191221 | MIT / MPL-2.0 package and public-suffix data |

The package-level licenses remain separate from this project's MIT license. LGPL components are separately replaceable packages/DLLs; this project imposes no restriction on modification/replacement or reverse engineering to debug those modifications under their respective terms. The supplied source archive is used instead of a future source-on-request promise. `CORRESPONDING-SOURCES.json` in each license directory records the same archive origins and hashes.

This collection preserves the upstream notices carried by the selected distribution inputs and supplies the listed corresponding sources. It is not a claim of a complete independent legal audit of every statically linked native subcomponent or a reproducible rebuild of upstream binaries. See the inventory and each preserved license for the actual terms.

The project's MIT text follows the [Open Source Initiative's MIT template](https://opensource.org/license/mit).

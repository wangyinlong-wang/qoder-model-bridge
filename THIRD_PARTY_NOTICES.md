# Third-party notices

The root [MIT license](LICENSE) covers this project's original code and documentation. It does not replace the licenses of dependencies, build tools, client applications, or redistributed runtimes.

This repository contains source and packaging templates. It does not include Python distributions, wheels, Tcl/Tk frameworks, IDE libraries, generated bridge ZIPs, or desktop installers.

| Component | Use | Upstream license / notice |
| --- | --- | --- |
| mitmproxy 12.2.3 | Proxy runtime declared in `app/requirements.txt` | [MIT license](https://github.com/mitmproxy/mitmproxy/blob/v12.2.3/LICENSE) |
| CPython | Source runtime; offline templates target 3.13.16 | [PSF License Version 2 and incorporated-software notices](https://docs.python.org/3.13/license.html) |
| Tcl / Tk | Tkinter GUI; the macOS bundle baseline uses 8.6.18 | Upstream [Tcl license.terms](https://github.com/tcltk/tcl/blob/core-8-6-18/license.terms) and [Tk license.terms](https://github.com/tcltk/tk/blob/core-8-6-18/license.terms) |
| certifi | Mozilla-derived CA bundle used by the macOS bootstrap fallback | [MPL-2.0 notice for the CA bundle](https://github.com/certifi/python-certifi/blob/master/LICENSE) |
| NSIS | Optional Windows installer build tool | Component-specific terms are retained verbatim in [NSIS-COPYING.txt](packaging/windows/NSIS-COPYING.txt) |

Other Python packages and native dependencies retain their own licenses. The offline requirements files identify dependency versions and hashes; they are not a complete license inventory.

IDEA, the Qoder plugin, Qoder IDE, their Java/Electron runtimes, and their artwork are not redistributed here. The optional bridge compiles against libraries from a developer's separately installed IDE and plugin.

Before distributing a binary bundle, prepare a clean public build, inventory its exact runtime and transitive dependencies, retain their required license texts and copyright notices, and review any separately included assets. No complete binary-license audit or public binary release is claimed by this source preparation. Historical privately configured installers are not public release artifacts of this repository.

The project's MIT text follows the [Open Source Initiative's MIT template](https://opensource.org/license/mit).

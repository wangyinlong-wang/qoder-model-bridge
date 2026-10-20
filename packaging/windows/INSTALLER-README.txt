Qoder Model Bridge — Windows offline package build notes

This source repository does not include a complete offline payload or a public installer.
For source execution, create app/.venv, install app/requirements.txt, and run app/launcher.py.
bootstrap.py and start.cmd require the complete separately prepared Windows x64 offline package.

The upstream example http://127.0.0.1:3000/v1 is not a bundled service. Supply your own compatible endpoint and authorization through the tool. Do not put real keys, private CA keys, runtime state or user configuration into a distribution payload.

The project's original code is under the root MIT LICENSE. NSIS and all bundled runtimes and dependencies retain their own licenses. Keep NSIS-COPYING.txt unchanged and include the exact runtimes' required notices in any future binary distribution.

See the repository BUILD.md, THIRD_PARTY_NOTICES.md and docs/INSTALLATION.md before assembling a public package. No existing private installer is a public release of this repository.

# Self-update Beta isolation

`bootstrap_test.py` remains a separate test-only component at the project root for backwards compatibility. Normal `Updater` startup, CLI checks, IPC sessions and update downloads do not import or invoke it.

The component is not a V1 production feature. It only accepts explicit test-directory paths and must never replace a formal `Updater.exe`.

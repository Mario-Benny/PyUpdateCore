# Self-update Beta isolation

`bootstrap_test.py` is a separate test-only component in this directory. Normal `Updater` startup, CLI checks, IPC sessions and update downloads do not import or invoke it.

The component is not a V1 production feature. It only accepts explicit test-directory paths and must never replace a formal `Updater.exe`.

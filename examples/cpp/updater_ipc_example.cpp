// Windows-only reference source. Build with MSVC after supplying an Updater.exe path.
// It intentionally uses CreateProcess plus redirected stdin/stdout, and sends the
// same UTF-8 JSON Lines messages documented in docs/IPC_PROTOCOL.md.
#include <windows.h>
#include <stdio.h>
int main(void) { puts("See docs/IPC_PROTOCOL.md; integrate CreatePipe/CreateProcess in host application."); return 0; }

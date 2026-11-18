"""Chrome Native Messaging framing test host; no shell, paths, credentials or downloads."""
import json, struct, sys
ALLOWED = {"proxy_probe"}
def receive():
    raw=sys.stdin.buffer.read(4)
    if len(raw)!=4: return None
    size=struct.unpack("<I",raw)[0]
    if size>65536: return None
    return json.loads(sys.stdin.buffer.read(size).decode("utf-8"))
def send(value):
    body=json.dumps(value,separators=(",",":")).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I",len(body))+body); sys.stdout.buffer.flush()
while True:
    request=receive()
    if request is None: break
    if not isinstance(request,dict) or request.get("type") not in ALLOWED: send({"ok":False,"error":"unsupported_request"})
    else: send({"ok":True,"request":"proxy_probe","note":"extension must supply sanitized mode"})

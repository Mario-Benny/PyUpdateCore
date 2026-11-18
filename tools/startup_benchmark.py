"""Local-only startup benchmark for packaged test binaries."""
from __future__ import annotations
import json, subprocess, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TARGETS={"P2.5-onefile":ROOT/"Updater-P2.5-Test.exe","P2.6-onefile":ROOT/"p2_6_test_build/onefile/dist/Updater-P2.6-Test.exe","P2.6-onedir":ROOT/"p2_6_test_build/onedir/dist/Updater-P2.6-Test-Dir/Updater-P2.6-Test-Dir.exe"}
def timed(args:list[str], data:str="") -> float:
    start=time.perf_counter(); proc=subprocess.run(args,input=data,text=True,encoding="utf-8",capture_output=True,timeout=20,cwd=ROOT); elapsed=(time.perf_counter()-start)*1000
    if proc.returncode: raise RuntimeError(proc.stderr)
    return round(elapsed,1)
out=[]
for name,exe in TARGETS.items():
    help_times=[timed([str(exe),"--help"]) for _ in range(5)]
    session=[]
    for index in range(5):
        payload='{"protocol_version":1,"type":"request","request_id":"1","command":"get_version","params":{}}\n{"protocol_version":1,"type":"request","request_id":"2","command":"shutdown","params":{}}\n'
        session.append(timed([str(exe),"serve","--stdio","--data-root",str(ROOT/"test_environment"/"benchmark"/name/str(index))],payload))
    out.append({"name":name,"size_bytes":exe.stat().st_size,"help_ms":help_times,"help_avg_ms":round(sum(help_times)/5,1),"ipc_session_ms":session,"ipc_session_avg_ms":round(sum(session)/5,1)})
(ROOT/"test_environment/p2_6_benchmark.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,indent=2))

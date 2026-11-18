"""Test-only atomic updater replacement. Refuses to operate outside an explicit test root."""
from __future__ import annotations
import argparse, hashlib, os, shutil, subprocess, sys, time
from pathlib import Path

def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""): h.update(part)
    return h.hexdigest()
def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--test-root",type=Path,required=True); p.add_argument("--current",type=Path,required=True); p.add_argument("--candidate",type=Path,required=True); p.add_argument("--sha256",required=True); p.add_argument("--launch",action="store_true"); args=p.parse_args()
    root=args.test_root.resolve(); current=args.current.resolve(); candidate=args.candidate.resolve()
    if root.name != "test_environment" or root not in current.parents or root not in candidate.parents: raise SystemExit("only test_environment targets are allowed")
    if digest(candidate).lower()!=args.sha256.lower(): raise SystemExit("candidate SHA-256 mismatch")
    backup=current.with_suffix(current.suffix+".backup")
    try:
        if backup.exists(): backup.unlink()
        os.replace(current, backup); os.replace(candidate, current)
        if args.launch: subprocess.Popen([str(current), "--help"], cwd=root)
    except Exception:
        if backup.exists() and not current.exists(): os.replace(backup,current)
        raise
    return 0
if __name__ == "__main__": raise SystemExit(main())

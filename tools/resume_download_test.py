"""Creates a controlled partial file then verifies Downloader Range continuation."""
from __future__ import annotations
import hashlib, json, shutil, time
from pathlib import Path
import httpx
from update_core.config import load_split_config
from update_core.downloader import Downloader
from update_core.models import InstallerAsset
from update_core.storage import atomic_write_json

URL="https://github.com/Mario-Benny/ClassTool/releases/download/v1.2.2/ClassTool-v1.2.2-Setup.exe"
SHA="e2312c73d32c852a357bcf40e6d8585b8ed73ffaec14b7dacd3e67d47431885f"; SIZE=39144786
root=Path("test_environment/real_download").resolve(); task=root/"resume_single"; shutil.rmtree(task,ignore_errors=True); (task/"tmp").mkdir(parents=True)
config=load_split_config(root/"userdata.json",root/"coredata.json"); asset=InstallerAsset("ClassTool-v1.2.2-Setup.exe",URL,SIZE,SHA)
tid=hashlib.sha256(f"{asset.url}\0{asset.size}\0{asset.sha256}".encode()).hexdigest()[:16]; task_dir=task/"tmp"/f"task_{tid}"; task_dir.mkdir()
with httpx.Client(proxy="http://127.0.0.1:7897",trust_env=False,follow_redirects=True,timeout=30) as client:
    r=client.get(URL,headers={"Range":"bytes=0-2097151","Accept-Encoding":"identity"}); r.raise_for_status(); body=r.content; etag=r.headers.get("ETag","")
(task_dir/"single.part").write_bytes(body)
atomic_write_json(task_dir/"state.json",{"schema_version":1,"resource":{"url":URL,"size":SIZE,"sha256":SHA,"etag":etag,"threads":1},"single_downloaded":len(body)})
events=[]; start=time.monotonic(); d=Downloader(config,task/"tmp",task/"state.json",event_callback=events.append)
try:
    result=d.download(asset,threads=1)
finally: d.close()
h=hashlib.sha256(result.read_bytes()).hexdigest()
atomic_write_json(task/"result.json",{"partial_bytes":len(body),"initial_status":r.status_code,"initial_content_range":r.headers.get("Content-Range"),"continuation_events":events,"seconds":round(time.monotonic()-start,3),"sha256":h,"sha256_match":h==SHA})

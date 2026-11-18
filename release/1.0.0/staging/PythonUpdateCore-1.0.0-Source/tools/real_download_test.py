"""Controlled P2.5 downloader test; never launches an installer or calls GitHub API."""
from __future__ import annotations

import hashlib, json, shutil, time
from pathlib import Path
import httpx

from update_core.config import coredata_template, load_split_config, userdata_template
from update_core.downloader import Downloader
from update_core.models import InstallerAsset
from update_core.storage import atomic_write_json, read_json

URL = "https://github.com/Mario-Benny/ClassTool/releases/download/v1.2.2/ClassTool-v1.2.2-Setup.exe"
SHA256 = "e2312c73d32c852a357bcf40e6d8585b8ed73ffaec14b7dacd3e67d47431885f"
SIZE = 37_33 * 1024 * 1024  # replaced below by supplied exact 37.33 MiB approximation only for preflight


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def redirect_probe(proxy: str) -> tuple[list[dict[str, object]], str, int]:
    chain: list[dict[str, object]] = []
    url = URL
    with httpx.Client(proxy=proxy, trust_env=False, follow_redirects=False, timeout=30) as client:
        for _ in range(8):
            response = client.get(url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity", "User-Agent": "PyUpdateCore-P2.5-Test"})
            chain.append({"url": url, "status": response.status_code, "location": response.headers.get("location"), "content_range": response.headers.get("content-range")})
            if response.status_code not in {301, 302, 303, 307, 308}:
                return chain, str(response.url), int(response.headers.get("content-range", "bytes 0-0/0").rsplit("/", 1)[-1])
            url = str(response.next_request.url) if response.next_request else httpx.URL(url).join(response.headers["location"]).__str__()
    raise RuntimeError("redirect limit exceeded")


def main() -> None:
    root = Path("test_environment/real_download").resolve()
    root.mkdir(parents=True, exist_ok=True)
    proxy = "http://127.0.0.1:7897"
    with httpx.Client(proxy=proxy, trust_env=False, timeout=20) as client:
        ip = client.get("https://api.ipify.org?format=json").json().get("ip")
    redirects, final_url, exact_size = redirect_probe(proxy)
    hosts = sorted({httpx.URL(str(item["url"])).host for item in redirects if httpx.URL(str(item["url"])).host} | {httpx.URL(final_url).host})
    user, core = userdata_template(), coredata_template()
    user["application"] = {"id": "p2.5.real-download-test", "name": "P2.5 Real Download Test"}
    core["security"]["allowed_hosts"] = hosts
    core["security"]["signature_required"] = False
    core["network"] = {"download_channel": "native", "proxy_mode": "system", "proxy_url": "", "browser": {"type": "auto", "mode": "proxy_probe"}}
    atomic_write_json(root / "userdata.json", user); atomic_write_json(root / "coredata.json", core)
    config = load_split_config(root / "userdata.json", root / "coredata.json")
    asset = InstallerAsset("ClassTool-v1.2.2-Setup.exe", URL, exact_size, SHA256)
    outcomes = []
    for threads in (1, 4, 16):
        task = root / f"threads_{threads}"
        if task.exists(): shutil.rmtree(task)
        task.mkdir()
        events=[]; start=time.monotonic()
        downloader = Downloader(config, task / "tmp", task / "state.json", event_callback=events.append)
        try:
            result=downloader.download(asset, threads=threads)
        except Exception as exc:
            outcomes.append({"threads":threads,"success":False,"error":f"{type(exc).__name__}: {exc}","seconds":round(time.monotonic()-start,3),"events":events})
        else:
            seconds=time.monotonic()-start
            speeds=[e.get("speed_bytes_per_second") for e in events if isinstance(e.get("speed_bytes_per_second"),(int,float))]
            state=read_json(task / "state.json")
            outcomes.append({"threads":threads,"success":True,"seconds":round(seconds,3),"average_bytes_per_second":round(exact_size/seconds,2),"peak_bytes_per_second":round(max(speeds),2) if speeds else None,"sha256":sha256(result),"sha256_match":sha256(result)==SHA256,"events":events,"state":state})
        finally: downloader.close()
    atomic_write_json(root / "result.json", {"proxy_expected": proxy, "exit_ip": ip, "redirects": redirects, "final_url": final_url, "asset_size": exact_size, "results": outcomes})

if __name__ == "__main__": main()

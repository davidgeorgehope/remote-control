"""TV Remote Control - FastAPI backend using androidtvremote2."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

import pychromecast
from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("remote")

CERT_DIR = Path(__file__).parent / "certs"
CERT_DIR.mkdir(exist_ok=True)

# ── State ────────────────────────────────────────────────────────────────────

android_tvs: Dict[str, AndroidTVRemote] = {}  # host -> remote

# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for remote in android_tvs.values():
        try:
            remote.disconnect()
        except Exception:
            pass

app = FastAPI(lifespan=lifespan)

# ── Models ───────────────────────────────────────────────────────────────────

class HostRequest(BaseModel):
    host: str

class PairFinish(BaseModel):
    host: str
    code: str

class KeyCommand(BaseModel):
    host: str
    key: str
    direction: str = "SHORT"

class TextCommand(BaseModel):
    host: str
    text: str

class AppCommand(BaseModel):
    host: str
    app: str

# ── Discovery (uses Chromecast mDNS to find devices + their IPs) ────────────

@app.post("/api/discover")
async def discover_devices():
    """Find TVs on the network. Uses Chromecast mDNS since it reliably finds Android TVs."""
    devices = []

    # Try Chromecast discovery (this is what found the device before)
    def _cast_discover():
        try:
            ccs, browser = pychromecast.get_chromecasts(timeout=6)
            for cc in ccs:
                ip = getattr(cc.cast_info, "host", None)
                if not ip or ip == "unknown":
                    # Fallback to uri
                    uri_host = cc.uri.split(":")[0] if cc.uri else None
                    if uri_host and uri_host != "unknown":
                        ip = uri_host
                if ip and ip != "unknown":
                    devices.append({
                        "name": cc.name or ip,
                        "host": ip,
                        "model": cc.model_name or "Unknown",
                    })
            browser.stop_discovery()
        except Exception as e:
            log.warning("Chromecast discovery failed: %s", e)

    await asyncio.get_event_loop().run_in_executor(None, _cast_discover)

    # Also try androidtvremote2 mDNS
    try:
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

        async def on_found(zeroconf, service_type, name, state_change):
            from zeroconf import ServiceStateChange
            if state_change is not ServiceStateChange.Added:
                return
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            if info and info.parsed_scoped_addresses():
                host = info.parsed_scoped_addresses()[0]
                # Don't add duplicates
                if not any(d["host"] == host for d in devices):
                    devices.append({
                        "name": name.replace("._androidtvremote2._tcp.local.", ""),
                        "host": host,
                        "model": "Android TV",
                    })

        zc = AsyncZeroconf()
        browser = AsyncServiceBrowser(
            zc.zeroconf,
            ["_androidtvremote2._tcp.local."],
            handlers=[lambda *args: asyncio.ensure_future(on_found(*args))],
        )
        await asyncio.sleep(3)
        await browser.async_cancel()
        await zc.async_close()
    except Exception as e:
        log.warning("ATV mDNS discovery failed: %s", e)

    return {"devices": devices}

# ── Connect / Pair ───────────────────────────────────────────────────────────

@app.post("/api/connect")
async def connect(req: HostRequest):
    """Connect to a TV by IP. Returns needs_pairing=true if not yet paired."""
    host = req.host
    certfile = str(CERT_DIR / f"{host}.cert.pem")
    keyfile = str(CERT_DIR / f"{host}.key.pem")

    # Not paired yet
    if not os.path.exists(certfile):
        return {"status": "needs_pairing", "host": host}

    remote = AndroidTVRemote(
        client_name="Web Remote",
        certfile=certfile,
        keyfile=keyfile,
        host=host,
    )

    try:
        await remote.async_connect()
        remote.keep_reconnecting()
    except InvalidAuth:
        return {"status": "needs_pairing", "host": host}
    except CannotConnect as e:
        raise HTTPException(502, f"Cannot reach TV at {host}: {e}")

    android_tvs[host] = remote
    return {
        "status": "connected",
        "host": host,
        "device_info": remote.device_info,
        "is_on": remote.is_on,
    }


@app.post("/api/pair/start")
async def pair_start(req: HostRequest):
    """Start pairing — TV will show a code."""
    host = req.host
    certfile = str(CERT_DIR / f"{host}.cert.pem")
    keyfile = str(CERT_DIR / f"{host}.key.pem")

    remote = AndroidTVRemote(
        client_name="Web Remote",
        certfile=certfile,
        keyfile=keyfile,
        host=host,
    )
    await remote.async_generate_cert_if_missing()

    try:
        await remote.async_start_pairing()
    except (CannotConnect, ConnectionClosed) as e:
        raise HTTPException(502, f"Cannot reach TV at {host}: {e}")

    android_tvs[host] = remote
    return {"status": "pairing_started"}


@app.post("/api/pair/finish")
async def pair_finish(req: PairFinish):
    """Finish pairing with the code from the TV screen, then connect."""
    remote = android_tvs.get(req.host)
    if not remote:
        raise HTTPException(400, "Call /api/pair/start first")

    try:
        await remote.async_finish_pairing(req.code)
    except InvalidAuth:
        raise HTTPException(400, "Wrong code — try again")
    except ConnectionClosed:
        raise HTTPException(400, "Connection lost — try again")

    # Now connect
    try:
        await remote.async_connect()
        remote.keep_reconnecting()
    except (CannotConnect, InvalidAuth) as e:
        raise HTTPException(502, str(e))

    return {
        "status": "connected",
        "host": req.host,
        "device_info": remote.device_info,
        "is_on": remote.is_on,
    }

# ── Commands ─────────────────────────────────────────────────────────────────

@app.post("/api/key")
async def send_key(cmd: KeyCommand):
    remote = android_tvs.get(cmd.host)
    if not remote:
        raise HTTPException(400, "Not connected")
    remote.send_key_command(cmd.key, cmd.direction)
    return {"status": "ok"}


@app.post("/api/text")
async def send_text(cmd: TextCommand):
    remote = android_tvs.get(cmd.host)
    if not remote:
        raise HTTPException(400, "Not connected")
    for ch in cmd.text:
        if ch == " ":
            key = "SPACE"
        elif ch == ".":
            key = "PERIOD"
        elif ch == ",":
            key = "COMMA"
        elif ch == "-":
            key = "MINUS"
        elif ch == "/":
            key = "SLASH"
        elif ch == "@":
            key = "AT"
        elif ch.isalpha():
            key = ch.upper()
        elif ch.isdigit():
            key = ch
        else:
            continue
        remote.send_key_command(key)
    return {"status": "ok"}


@app.post("/api/launch")
async def launch_app(cmd: AppCommand):
    remote = android_tvs.get(cmd.host)
    if not remote:
        raise HTTPException(400, "Not connected")
    remote.send_launch_app_command(cmd.app)
    return {"status": "ok"}


@app.get("/api/state/{host}")
async def get_state(host: str):
    remote = android_tvs.get(host)
    if not remote:
        raise HTTPException(400, "Not connected")
    return {
        "is_on": remote.is_on,
        "current_app": remote.current_app,
        "device_info": remote.device_info,
        "volume_info": remote.volume_info,
    }

# ── Static Files ─────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)

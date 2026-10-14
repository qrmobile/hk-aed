#!/usr/bin/env python3
"""Deploy a static directory to Cloudflare Pages (hk-aed) via direct upload.

Usage: pages_deploy.py [site_dir]

Implements the Pages direct-upload protocol (same as wrangler):
  upload-token (JWT) -> check-missing -> upload assets -> upsert-hashes
  -> create deployment with manifest -> poll until success.

Auth: API token via the custom.cloudflare connector surrogate mechanism
(swapped by the egress proxy; never the real secret). Asset uploads use
the short-lived JWT returned by the API.
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry, ensure_allowed_url
from blake3 import blake3

ACCOUNT_ID = "b0d2e5f82526bbae6e7608b80517c60d"
PROJECT = sys.argv[2] if len(sys.argv) > 2 else "hkaed"
API = "https://api.cloudflare.com"
HOSTS = ["api.cloudflare.com"]
TIMEOUT = 90

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
}


def pages_hash(data: bytes, filename: str) -> str:
    """wrangler hashFile: hex(blake3(base64(content) + extWithoutDot))[:32]"""
    ext = os.path.splitext(filename)[1].lstrip(".")
    return blake3((base64.b64encode(data).decode("ascii") + ext).encode()).hexdigest()[:32]


def curl_api(method, path, bearer, data=None, multipart=None, retries=3):
    """Call api.cloudflare.com. bearer is the hsurr surrogate or a JWT."""
    url = API + path
    ensure_allowed_url(url, HOSTS)
    last_err = None
    for attempt in range(retries):
        cmd = ["curl", "-sS", "--max-time", str(TIMEOUT), "-X", method,
               "-H", "Authorization: Bearer " + bearer]
        tmpfile = None
        if multipart:
            for k, v in multipart.items():
                cmd += ["-F", "%s=%s;type=application/json" % (k, v)]
        else:
            cmd += ["-H", "Content-Type: application/json"]
            if data is not None:
                # bodies can be ~1MB (base64 assets); pass via file, not argv
                tmpfile = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
                json.dump(data, tmpfile)
                tmpfile.close()
                cmd += ["-d", "@" + tmpfile.name]
        cmd.append(url)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 10)
        except subprocess.TimeoutExpired:
            last_err = "curl timed out"
            ok, out = False, None
        else:
            if p.returncode != 0:
                last_err = "curl rc=%d: %s" % (p.returncode, p.stderr.strip()[:200])
                ok, out = False, None
            else:
                try:
                    out = json.loads(p.stdout)
                    ok = True
                except json.JSONDecodeError:
                    last_err = "non-JSON response: " + p.stdout[:200]
                    ok, out = False, None
        finally:
            if tmpfile:
                os.unlink(tmpfile.name)
        if not ok:
            time.sleep(2 * (attempt + 1))
            continue
        if not out.get("success"):
            last_err = "API error: %s" % json.dumps(out.get("errors"))[:300]
            time.sleep(2 * (attempt + 1))
            continue
        return out.get("result")
    raise RuntimeError("API call failed after %d tries: %s %s | %s" % (retries, method, path, last_err))


def curl_jwt(method, path, jwt, data):
    return curl_api(method, path, jwt, data=data)


def main():
    site = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/workspace/cloudflare/hk-aed/site")
    files = {}
    for name in sorted(os.listdir(site)):
        if name in ("_headers", "_redirects", "_routes.json") or name.startswith("."):
            continue
        fp = os.path.join(site, name)
        if not os.path.isfile(fp):
            continue
        with open(fp, "rb") as f:
            data = f.read()
        if len(data) > 25 * 1024 * 1024:
            raise RuntimeError("file too large for Pages: " + name)
        files["/" + name] = (data, pages_hash(data, name))
    if "/index.html" not in files:
        raise RuntimeError("site dir needs index.html: " + site)
    print("files: %s" % {k: v[1][:8] for k, v in files.items()}, flush=True)

    entry = dynamic_credential_entry("custom.cloudflare", "access_token")
    surr = str(entry["surrogate"]).strip()
    if not surr.startswith("hsurr:"):
        raise RuntimeError("credential did not resolve to a surrogate")

    # 1. upload token (JWT)
    tok = curl_api("GET", "/client/v4/accounts/%s/pages/projects/%s/upload-token" % (ACCOUNT_ID, PROJECT), surr)
    jwt = tok["jwt"]
    print("got upload token", flush=True)

    # 2. check which hashes are missing
    hashes = [h for _, h in files.values()]
    missing = curl_jwt("POST", "/client/v4/pages/assets/check-missing", jwt, {"hashes": hashes})
    if isinstance(missing, dict):
        missing = missing.get("missing", missing.get("hashes", hashes))
    missing = set(missing or [])
    print("missing assets: %d/%d" % (len(missing), len(hashes)), flush=True)

    # 3. upload missing assets
    to_upload = [(p, d, h) for p, (d, h) in files.items() if h in missing]
    if to_upload:
        batch = []
        for path, data, h in to_upload:
            ext = os.path.splitext(path)[1]
            batch.append({"key": h, "value": base64.b64encode(data).decode("ascii"),
                          "metadata": {"contentType": CONTENT_TYPES.get(ext, "application/octet-stream")},
                          "base64": True})
        curl_jwt("POST", "/client/v4/pages/assets/upload", jwt, batch)
        print("uploaded %d assets" % len(batch), flush=True)
        curl_jwt("POST", "/client/v4/pages/assets/upsert-hashes", jwt, {"hashes": hashes})
        print("upserted hashes", flush=True)

    # 4. create deployment with manifest
    manifest = {p: h for p, (_, h) in files.items()}
    headers_path = os.path.join(site, "_headers")
    multipart = {"manifest": json.dumps(manifest)}
    if os.path.isfile(headers_path):
        with open(headers_path) as f:
            multipart["_headers"] = f.read()
    dep = curl_api("POST", "/client/v4/accounts/%s/pages/projects/%s/deployments" % (ACCOUNT_ID, PROJECT),
                   surr, multipart=multipart)
    dep_id, dep_url = dep["id"], dep.get("url")
    print("deployment created: %s -> %s" % (dep_id, dep_url), flush=True)

    # 5. poll until done
    for _ in range(60):
        time.sleep(5)
        d = curl_api("GET", "/client/v4/accounts/%s/pages/projects/%s/deployments/%s" % (ACCOUNT_ID, PROJECT, dep_id), surr)
        stage = (d.get("latest_stage") or {}).get("status")
        print("stage: %s" % stage, flush=True)
        if stage == "success":
            print("DEPLOY OK: %s" % dep_url)
            return 0
        if stage in ("failed", "canceled"):
            raise RuntimeError("deployment failed: %s" % json.dumps(d.get("latest_stage"))[:500])
    raise RuntimeError("deployment timed out waiting for success")


if __name__ == "__main__":
    sys.exit(main())

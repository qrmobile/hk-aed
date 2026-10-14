#!/usr/bin/env python3
"""Daily AED data updater for the hk-aed Cloudflare Pages site.

1. Downloads the latest AED CSV from HKFSD (curl; sandbox proxy hangs urllib).
2. Converts to the compact aed_data.json format the app fetches.
3. Writes aed_meta.json with the update date (HKT).
4. Redeploys the Pages site if the data changed.

Usage: update_aed_data.py  (uses the venv python for blake3 in pages_deploy)
"""
import csv
import json
import os
import subprocess
import sys
import time

BASE = os.path.expanduser("~/workspace/cloudflare/hk-aed")
SITE = os.path.join(BASE, "site")
VENV_PY = os.path.join(BASE, "venv", "bin", "python")
CSV_URL = "https://es.hkfsd.gov.hk/aed_api/export_aed.php?lang=TC"
OP_MAP = {"Anyone": "A", "Trained Personnel Only": "T", "Staff only": "S"}


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def main():
    os.makedirs(SITE, exist_ok=True)
    tmp_csv = "/tmp/aed_daily.csv"
    log("downloading AED CSV from HKFSD")
    p = subprocess.run(["curl", "-sSL", "--max-time", "120", "-o", tmp_csv, CSV_URL],
                       capture_output=True, text=True, timeout=150)
    if p.returncode != 0 or os.path.getsize(tmp_csv) < 1000:
        raise RuntimeError("CSV download failed: " + p.stderr.strip()[:200])

    rows = []
    with open(tmp_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                lat = float(r["Location Google Map coordinate: latitude"])
                lng = float(r["Location Google Map coordinate: longitude"])
            except (ValueError, TypeError):
                continue
            if not (18 < lat < 25 and 110 < lng < 118):
                continue
            rows.append([
                r["AED Name"].strip(),
                r["AED Address"].strip(),
                r["Detailed location of the AED installed"].strip(),
                round(lat, 6),
                round(lng, 6),
                1 if r["Whether the AED can be used by anyone"].strip().lower() == "yes" else 0,
                OP_MAP.get(r["Person allowed to operate the AED"].strip(), "?"),
                1 if r["Ground level categories"].strip().lower().startswith("street") else 0,
                r["Service Hour Remark"].strip(),
            ])
    if len(rows) < 1000:
        raise RuntimeError("suspiciously few rows (%d), aborting" % len(rows))
    log("parsed %d AED records" % len(rows))

    new_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    data_path = os.path.join(SITE, "aed_data.json")
    old_json = open(data_path, encoding="utf-8").read() if os.path.exists(data_path) else None
    today = subprocess.run(["date", "+%F"], capture_output=True, text=True,
                           env=dict(os.environ, TZ="Asia/Hong_Kong")).stdout.strip()
    meta = {"updated": today, "count": len(rows), "source": "香港消防處",
            "source_url": "https://es.hkfsd.gov.hk/care/zh-hant/location/"}
    with open(os.path.join(SITE, "aed_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))

    if old_json == new_json:
        log("data unchanged since last update; skipping deploy")
        return 0
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(new_json)
    log("data changed, deploying to Cloudflare Pages")
    r = subprocess.run([VENV_PY, os.path.join(BASE, "pages_deploy.py"), SITE])
    if r.returncode != 0:
        raise RuntimeError("pages deploy failed")
    log("deploy finished")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("ERROR: %s" % e)
        sys.exit(1)

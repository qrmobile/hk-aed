# 香港 AED 搜尋器 (HK AED Finder)

Private archive of the Hong Kong AED finder web app.

- Live site: https://hkaed.pages.dev
- Data source: 香港消防處「AED 搵得到」公開 API — https://es.hkfsd.gov.hk/care/zh-hant/location/

## Files

- `index.html` — self-contained app (Traditional Chinese, HK style). Uses browser geolocation, shows the nearest AEDs on an OpenStreetMap (Leaflet) map + list, with filters (anyone-usable, street level), walking-route links, a manual nearby-place fallback, and a disclaimer section.
- `aed_data.json` — compact AED dataset snapshot. Format: JSON array, each record `[名稱, 地址, 詳細位置, 緯度, 經度, 任何人可用(1/0), 操作者代碼(A/T/S), 街鋪層(1/0), 服務時間備註]`. The app fetches this at startup (`cache: no-store`) and falls back to its embedded snapshot if unavailable.
- `aed_meta.json` — `{"updated","count","source","source_url"}` for the data snapshot.

## Deployment

- Cloudflare Pages project `hkaed` (direct-upload API), served at https://hkaed.pages.dev
- Data refreshed daily ~06:00 HKT by an automated job: downloads the latest HKFSD CSV, rebuilds `aed_data.json` / `aed_meta.json`, and redeploys only when the data changed.

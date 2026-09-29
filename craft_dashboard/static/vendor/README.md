# Vendored frontend dependencies

Pinned copies of third-party browser libraries, served from this app rather
than a CDN. A CDN outage would break charts and Markdown rendering, a floating
version tag would ship unreviewed upgrades silently, and a CDN compromise would
mean arbitrary script execution on the dashboard.

| File | Library | Version | Source |
|---|---|---|---|
| `chart.umd.min.js` | Chart.js | 4.4.9 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.9/dist/chart.umd.min.js` |
| `marked.min.js` | marked | 15.0.12 | `https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js` |
| `purify.min.js` | DOMPurify | 3.2.6 | `https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js` |

To upgrade, download the new pinned URL over the existing file and update the
version in this table.

# Looked

A local reverse-image-search app with Google Lens results, SQLite history,
quota safeguards, and CSV/JSON downloads. Python runs the backend; the browser
uses plain HTML, CSS, and JavaScript. There is no Node build, telemetry, account
system, deployment step, or cloud database.

**Coverage:** this searches **Google Lens through SerpApi**. It does not search
every social-media platform or the entire internet. Private, unindexed,
login-only, and inaccessible content may not appear. A result is a lead to
investigate, not proof of ownership, identity, or where an image first appeared.

## 1. Install Python

Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/).
On Windows, select **Add Python to PATH**, then reopen your terminal.
This project was tested on Windows with Python 3.13.9.

Download or clone this repository into a folder you can write to. Keep the
files together. You do not need Node.js, Docker, or a database server.

## 2. Get a free SerpApi key

1. Visit [SerpApi](https://serpapi.com/) and register for its Free plan.
2. Find your key in [Manage API Key](https://serpapi.com/manage-api-key).
3. Copy `.env.example` to a new file named **`.env`** in the project folder.
   On Windows, enable file-name extensions so it is not named `.env.txt`.
4. Open `.env` in a text editor and replace the placeholder:

   ```dotenv
   SERPAPI_API_KEY=your_actual_key_goes_here
   MONTHLY_SEARCH_LIMIT=200
   DAILY_SEARCH_LIMIT=20
   HOURLY_SEARCH_LIMIT=40
   PORT=5000
   ```

Only Python reads this file. The UI has no API-key field. Never paste your key
into an image URL, upload name, screenshot, issue, or Git commit. `.env` is
excluded from Git; `.env.example` contains only a placeholder. Restart the app
after changing `.env`.

As checked on October 24, 2026, SerpApi advertises **250 searches per month** and
**50 searches per hour** for its Free plan. Plans can change: check the current
[pricing page](https://serpapi.com/pricing) and your account dashboard. This app
does not subscribe you to a paid plan or buy credits.

## 3. Start and stop

### Windows

Double-click **`start.bat`**, or run it from PowerShell in the project folder:

```powershell
.\start.bat
```

### Linux

Open a terminal in the project folder:

```sh
sh start.sh
```

The launcher creates a local `.venv`, installs `requirements.txt`, and copies
`.env.example` to `.env` if needed. The first launch needs internet access to
download dependencies. It never overwrites an existing `.env`.

Open **[http://127.0.0.1:5000](http://127.0.0.1:5000)** in your browser. The app
does not automatically open a browser. Keep the terminal open. To stop, press
**Ctrl+C** in that terminal. On Windows, answer `Y` if asked to terminate the
batch job. Start it again with the same launcher; history and usage persist.

After installation, you can start without checking/downloading dependencies:

```powershell
# Windows
.\.venv\Scripts\python.exe app.py
```

```sh
# Linux
.venv/bin/python app.py
```

The server binds to **127.0.0.1 only**. There is no automatic deployment or
network-exposure option. It uses Waitress with debugging disabled.

## 4. Search and export

1. **Upload an image:** choose or drop a still JPEG, PNG, or WebP, up to
   **500,000 bytes**. Larger files must be resized locally first. Images must
   have at most 25 megapixels and 10,000 pixels on either side. Originals are
   validated and sent without resizing or stripping their embedded metadata.
2. Or choose **Paste an image URL** and enter a public, direct HTTP/HTTPS image
   address. The app downloads and validates up to **10 MB** to compute the
   image's actual content hash. Private network addresses, nonstandard ports,
   unsupported formats, animations, and hosts requiring login are rejected.
3. Choose **Visual matches** or **Provider exact matches**, a language code
   such as `en` or `vi`, and optionally a country such as `us`. Safe filtering
   is always enabled. Each different set of these parameters is a separate
   cached search.
4. Click **Search with Google Lens**. A new file search uploads the image to
   SerpApi, then makes a Lens search request. A URL search sends the public URL
   to SerpApi. Google and SerpApi receive the image or its URL as needed to
   perform the search; the search itself is not offline.
5. Results show available thumbnails, titles, source domains, and original-page
   links. Missing fields have placeholders. Unsafe links are omitted. Provider
   response order is retained; duplicates keep their first occurrence, so rank
   numbers can have gaps. No similarity percentages are fabricated. The exact
   label is used only for entries in the returned `exact_matches` section; a
   visual result's `exact_matches` navigation flag does not establish a match.
6. A social-domain filter appears only for social hosts actually present in the
   returned original-page links. Filtering is local and costs no API calls.
   It does not initiate searches of those platforms.
7. **CSV** or **JSON** downloads all the saved results to your browser's local
   download folder, preserving order, regardless of the current filter. JSON
   also includes the image hash, parameters, source, save time, and warnings.
   It does not contain raw provider metadata, image bytes, or API keys. CSV
   cells that could be spreadsheet formulas receive a leading apostrophe.

Thumbnails are fetched lazily through Python from their external hosts and
decoded into safe JPEGs. Uncheck **Load available thumbnails** to avoid new
thumbnail requests. Cached searches make **zero SerpApi search/upload API
calls**; displaying thumbnails can still download image files from their
hosts, including SerpApi's static image host. Opening an original page visits
that external site in a new tab. Referrers are suppressed.

## Cache and history

- SQLite is created automatically at **`data/looked.sqlite3`**. It stores
  normalized results, parameters, image hashes, original image bytes for local
  preview/refresh, source information, URL aliases, and usage counters. The
  database is local and excluded from Git. Protect the folder like other
  personal files; storage is not encrypted.
- Cache identity is SHA-256 of a versioned record containing the image's
  **SHA-256 content hash** and normalized search parameters. Identical bytes
  and parameters share one entry, including identical bytes found at different
  URLs. Merely looking similar is insufficient. The most recently used source
  becomes that entry's refresh source.
- Reopen from history or submit the same image/parameters: **zero API calls**.
  Reopening works after restarting, with a missing key, or after exhausting a
  local limit. Cached results have no automatic expiration. Empty successful
  responses are cached too; failed/malformed responses are not.
- **Refresh · uses 1 search** explicitly bypasses the local result cache and
  sends `no_cache=true` to SerpApi. A file is uploaded again; a URL is downloaded
  and hashed again. If the remote bytes change, a new cache entry is created.
  A failed refresh retains the previously saved results.
- A remembered URL uses its **last downloaded content hash**. **Caching a URL
  cannot detect when its remote image changes** until you explicitly refresh
  or clear history. Even during a fresh URL search, the image could change
  between this app's download and the provider's download.
- **Clear** removes saved searches, stored images, URL aliases, and operation
  replay records. It **does not remove quota counters or credential-free
  attempt records**. It does not delete files you downloaded as exports.
  History clearing is a logical database deletion, not a forensic disk wipe.
- Do not delete the database to clear history: that would also remove the
  safeguards' counters. Removing `.venv` does not remove history or counters.

## Usage limits and concurrent clicks

The defaults are deliberately conservative: **200/month, 20/day, 40/hour**.
They are local caps on attempted searches, not an authoritative SerpApi balance.
Each uses **UTC calendar periods**, not your account's billing anniversary or a
rolling 60-minute window. A setting of `0` blocks all new searches for that
period. Adjust `.env` to fit your actual remaining allowance and restart.

SQLite atomically reserves one search before contacting SerpApi. Concurrent
threads/processes cannot overspend a cap. Only one new search can be active
against a given database; overlapping clicks receive a busy message. Each UI
submission also has an operation ID so replaying the same request, including a
refresh, cannot send another search. Buttons are disabled while work is active.

One local search can cause **one HTTP call for a URL** or **two for a file**
(upload + search). The UI separately counts these outbound SerpApi HTTP
attempts. Validation errors and local quota blocks use no provider calls.
An attempt stays reserved after an upload error, invalid key, provider error,
timeout, or crash. This conservative accounting avoids silently overspending
when the app cannot know whether the provider processed a request. No automatic
retries, pagination requests, account API polls, or background searches occur.

SerpApi's own billing may differ: it can serve cached responses without charging
a search, and other apps using the same account also consume allowance. This
app neither synchronizes nor guarantees your remaining provider credits. Check
your dashboard if it returns a quota error. Switching keys does not erase local
counters. A crash can leave the search lock active for up to **10 minutes**;
waiting for it to expire does not reset usage.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Python not found | Install Python 3.11+, enable PATH on Windows, reopen the terminal. |
| Linux says `venv` or `ensurepip` is missing | Install your distribution's `python3-venv` and `python3-pip` packages, then rerun the launcher. |
| Dependencies cannot download | Check your internet/proxy settings. Pip prints installation errors. The app's image/provider HTTP clients deliberately ignore environment proxy settings. |
| Windows PowerShell blocks scripts | Use `start.bat`; no PowerShell execution-policy change is needed. |
| Missing key | Confirm the file is exactly `.env`, beside `app.py`, replace the placeholder, and restart. |
| Invalid key | Copy the current key from SerpApi's dashboard into `.env`, check for extra characters, and restart. Error messages never echo the key. |
| Local limit reached | Wait for the displayed UTC reset, use saved searches, or adjust a cap only if your account has sufficient allowance. |
| Provider quota/rate limit | Check SerpApi's dashboard. Other apps and differing billing/reset periods can explain the difference from local counters. |
| Timeout or connection error | Check history before retrying. The attempt remains counted; there are no automatic retries. Refresh or a new submission uses another search. |
| No results | Try a clearer image or a different tab. Absence of results does not establish absence from the web. |
| Malformed response | No failed response is cached. Wait and retry deliberately; previously saved results remain available. |
| URL cannot be downloaded | Use a direct public image URL on port 80/443. Try uploading a supported file instead. Private/DNS-rebinding targets and excessive redirects are blocked. |
| Missing thumbnail | The host may deny access, the image may be too large or unsupported, or the URL may have expired. The original-page link still works if supplied. |
| Port already in use | Stop the other app instance or change `PORT` in `.env`, restart, and open that port on 127.0.0.1. |
| Page expired / CSRF error | Reload after restarting the app. Each app process issues a new local page token. Run one server instance per project folder. |
| Busy after a crash | Wait up to 10 minutes for the durable search lock to expire. Do not delete the database. |
| Local database unavailable | Ensure the folder is writable, disk space is available, and another tool is not holding an exclusive SQLite lock. |

## Tests — no live API calls or credits

Install the test dependencies into the same local environment:

```powershell
# Windows
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

```sh
# Linux
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

All provider responses are **synthetic mocks**. JSON fixtures are explicitly
labeled in `tests/fixtures/README.md`. Tests block socket connections, DNS, and
unmocked requests, use temporary databases, and do not read your `.env`.
They cover quota blocking, atomic reservations, concurrent clicks, request
replay, cache hits across restarts, URL refresh, history clearing, UTC period
rollover, provider errors/timeouts, uploads, parsing, safe links, DNS pinning,
CSV formula escaping, CSRF/Host checks, exports, and secret redaction.

**No live SerpApi search has been performed or verified during development.**
A real integration check requires your own valid key and an explicit search;
it may consume credits. The local server and HTTP endpoints were smoke-tested;
visual browser verification was unavailable in the development session.
Linux launcher execution requires a Linux machine.

## Project layout

```text
app.py                 Loopback-only Waitress entry point
looked/web.py          Local HTTP routes and browser security headers
looked/service.py      Search orchestration and cache/refresh decisions
looked/serpapi.py      SerpApi image upload and Google Lens HTTP client
looked/quota.py        Durable atomic budgets and UTC period management
looked/database.py     SQLite cache, history, operation IDs, search lease
looked/images.py       Image decoding, validation, SHA-256, safe downloading
looked/results.py      Parsing, order, deduplication, social filters, CSV
looked/security.py     URL validation and credential redaction
looked/config.py       Python-only .env settings
templates/index.html  Browser UI
static/               Local CSS, JavaScript, and icon; no CDN assets
tests/                Offline mocked tests and labeled fixtures
```

API behavior follows the official [Google Lens API](https://serpapi.com/google-lens-api)
and [Image API](https://serpapi.com/image-api) documentation. Uploaded images use
SerpApi's Image API followed by `image_id`; no public tunnel or third-party
image hosting service is created. Provider behavior and coverage can change.

Licensed under the repository's [MIT license](LICENSE).

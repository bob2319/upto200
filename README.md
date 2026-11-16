# Upto200

Optimize images for the web. Keep your images under 200 KB.

**Smaller images. Faster websites.**

Free MVP web service that optimizes images to a **configurable target file size (KB)** using the project’s quality-search algorithm (WebP engine preserved).

Default target: **150 KB** (users can choose 50, 100, 150, 200 KB, or any custom value).

Supported **inputs:** JPG/JPEG, PNG, WebP, HEIC/HEIF  
Supported **outputs:** WebP, JPEG, PNG  
Not supported: AVIF (input/output), HEIC/HEIF output

## Requirements

- Python 3.11+ (tested on 3.13)
- Windows or Linux
- Dependencies in `requirements.txt` (Pillow, pillow-heif, FastAPI, Uvicorn)

## Local development (Windows)

```powershell
cd d:\resize
python -m venv .venv-win
.\.venv-win\Scripts\pip install -r requirements.txt
.\.venv-win\Scripts\uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- App UI: http://127.0.0.1:8000/
- API docs: http://127.0.0.1:8000/docs

Optional CLI (batch workflow):

```powershell
.\.venv-win\Scripts\python main.py
```

Reads `images/`, writes WebP files to `compressed/`.

## Local development (Linux / macOS)

```bash
cd /path/to/resize
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

## Tests

```powershell
.\.venv-win\Scripts\python -m pytest tests -v
```

Coverage includes regression against the original WebP algorithm behavior (happy path), API tests, verification (PNG alpha, `target_achieved`, download pinning), optimization correctness (negative compression / quality reporting), and integration matrix/batch/security tests.

## API overview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/optimize` | Multipart upload + optimize |
| `GET` | `/api/jobs/{job_id}` | Fetch job results |
| `GET` | `/api/jobs/{job_id}/files/{file_id}` | Download one file |
| `GET` | `/api/jobs/{job_id}/zip` | Download ZIP |

### Optimize form fields

| Field | Default | Notes |
|-------|---------|-------|
| `files` | required | One or more images |
| `target_size_kb` | `150` | Target size in KB (configurable) |
| `max_width` | `1920` | Max width; aspect ratio preserved |
| `output_format` | `webp` | `webp`, `jpeg`, or `png` |
| `start_quality` | `90` | Lossy starting quality |
| `min_quality` | `40` | Quality floor |
| `step_quality` | `5` | Quality step |

`target_achieved` is `true` only when optimized size ≤ target. The engine continues searching when output is under target but still larger than the original. Reported `quality` always matches the encoded file.

## Production deployment (Linux)

Example with Uvicorn behind a reverse proxy:

```bash
python3 -m venv /opt/upto200/.venv
source /opt/upto200/.venv/bin/activate
pip install -r requirements.txt

export OPTIMIZER_TEMP_ROOT=/var/tmp/upto200-jobs
export OPTIMIZER_MAX_UPLOAD_BYTES=15728640
export OPTIMIZER_MAX_FILES=20
export OPTIMIZER_MAX_CONCURRENT=2
export OPTIMIZER_JOB_TTL_SECONDS=3600
export OPTIMIZER_RATE_LIMIT_REQUESTS=30
export OPTIMIZER_RATE_LIMIT_WINDOW_SECONDS=60

uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Notes:

- Prefer **1 Uvicorn worker** for this MVP: jobs and rate limits are in-memory per process.
- Put Nginx/Caddy in front for TLS, request body size limits, and static caching if desired.
- Ensure the temp directory is on disk with enough free space and is not publicly served.
- `pillow-heif` needs a working libheif stack on the host (the wheel usually bundles this on common platforms).

### systemd sketch

```ini
[Unit]
Description=Upto200 image optimizer
After=network.target

[Service]
WorkingDirectory=/opt/upto200
Environment=OPTIMIZER_TEMP_ROOT=/var/tmp/upto200-jobs
ExecStart=/opt/upto200/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

## Project layout

```
optimizer/     # compression engine + service wrapper
api/           # FastAPI app, security, temp jobs, cleanup
web/           # frontend (HTML/CSS/JS)
tests/         # regression + API + integration tests
main.py        # CLI batch entrypoint
requirements.txt
```

## Security defaults

- Content-based image validation (Pillow), not extension alone
- Upload size limit (default 15 MB/file)
- Max 20 files per request
- Randomized temp filenames; path-safe ZIP names
- In-memory rate limiting
- Concurrent compression capped (default 2)
- Job directories under UUID paths with TTL cleanup
- Active downloads pin jobs so cleanup cannot delete files mid-transfer

## Known limitations

- No AVIF support
- No accounts, auth, payments, or multi-worker shared job store
- PNG “quality” uses compress levels, not JPEG-style quality
- Browser HEIC preview may be unavailable (processing still works server-side)
- Very aggressive KB targets may be unreachable; reported honestly via `target_achieved`
- If no smaller encoding exists within the quality range, output may still exceed the original size (reported honestly)

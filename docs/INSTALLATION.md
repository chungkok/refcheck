# Installation

RefCheck needs Python 3.10 or newer. Tests run on Python 3.13 in development; CI runs 3.10 and 3.13
on Windows, macOS and Linux.

## Windows

1. Install Python from python.org (tick *Add python.exe to PATH*).
2. Download or clone the repository, open the folder, double click `run_app.bat`.
   It creates a private environment in `.venv` on first run, installs the pinned dependencies and
   opens http://localhost:8501.

## macOS and Linux

```
git clone https://github.com/chungkok/refcheck
cd refcheck
./run_app.sh
```

## Manual install (any system)

```
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements-lock.txt     # exact tested versions
streamlit run app.py
```

`requirements.txt` lists version ranges instead, for use inside other environments.

## Optional components

| Component | For | Install |
|---|---|---|
| LibreOffice | `.doc`, `.odt` and better `.rtf` input | libreoffice.org; RefCheck finds `soffice` on the PATH |
| GROBID | alternative PDF reference extraction | run a current GROBID release locally, for example with its official Docker image published on `127.0.0.1:8070`, then `--grobid http://localhost:8070` on the command line |
| defusedxml | extra hardening of XML parsing | `pip install defusedxml` (RefCheck already refuses DTDs without it) |
| NCBI API key | about three times faster searching | create one in your NCBI account settings; enter it in the app or set `NCBI_API_KEY` |

## Docker

```
docker compose up --build        # then open http://localhost:8501
```

The compose file publishes the port on `127.0.0.1` only. The image runs as a non root user. The
Docker files were written and reviewed but the image has not yet been built in the development
environment; please report any problem.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `NCBI_EMAIL`, `NCBI_API_KEY` | contact email and key sent to NCBI | none |
| `REFCHECK_CONFIG` | JSON settings file (for example thresholds fitted on labelled data) | none |
| `REFCHECK_AUTO_SCORE`, `REFCHECK_REVIEW_SCORE`, `REFCHECK_MIN_GAP` | matching thresholds | 86, 70, 5 (provisional) |
| `REFCHECK_CACHE` | PubMed cache file; empty disables it | `~/.cache/refcheck/pubmed_cache.sqlite` |
| `REFCHECK_CACHE_TTL_DAYS` | cache lifetime | 30 |
| `REFCHECK_OFFLINE` | answer only from the cache | off |
| `REFCHECK_DEPLOYMENT` | `local`, `hosted` or `auto` (from the browser address) | auto |
| `REFCHECK_DISABLE` | ablation switches, comma separated | none |

## Institutional hosting

Run the container behind your institution's reverse proxy with TLS and sign in, set
`REFCHECK_DEPLOYMENT=hosted` so users see the hosted notice, and set `REFCHECK_CACHE=` (empty) so
no user's search terms are stored on the shared server. See [PRIVACY.md](PRIVACY.md).

## Checking the install

```
python -m pip install -r requirements-dev.txt
python -m pytest -q          # offline; NCBI is mocked
```

## Licences of dependencies

See [DEPENDENCIES.md](DEPENDENCIES.md).

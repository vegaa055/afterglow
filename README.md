# Afterglow

Sunset and sunrise vividness forecasting — self-hosted, no API key required.

Afterglow computes a **0–100 vividness score** for each sunset and sunrise using a
composite atmospheric model: cloud cover (low/mid/high), aerosol optical depth,
humidity, visibility, and precipitation are averaged across the golden-hour window
and fed into a tuned Gaussian scoring algorithm.

```
Poor  0–20  ·  Fair 21–40  ·  Good 41–60  ·  Vivid 61–80  ·  Epic 81–100
```

Data sources: [Open-Meteo](https://open-meteo.com) (weather + air quality) ·
[Astral](https://sffjunkie.github.io/astral/) (solar events) · No API key needed.

---

## Stack

| Layer      | Tech                                      |
|------------|-------------------------------------------|
| Backend    | FastAPI + Uvicorn / Gunicorn              |
| Scheduler  | APScheduler (hourly cache warm)           |
| Solar      | Astral 3.2                                |
| Weather    | Open-Meteo `/v1/forecast` + `/v1/air-quality` |
| Cache      | requests-cache (SQLite)                   |
| Frontend   | Vanilla JS · Chart.js · Cormorant Garamond |
| Container  | Docker (multi-stage) · Docker Compose     |
| Deploy     | Dokploy + Traefik (auto TLS)              |

---

## Local development

```bash
# Clone
git clone https://github.com/youruser/afterglow.git
cd afterglow

# Option A — plain Python
cd app
pip install -r ../requirements.txt
python main.py
# → http://localhost:8000

# Option B — Docker Compose (live reload)
docker compose --profile dev up --build
# → http://localhost:8000
```

---

## Production deploy (Dokploy)

1. Push repo to GitHub.
2. In your Dokploy dashboard → **New Compose** service.
3. Set:
   - **Source**: GitHub → your repo → branch `main`
   - **Compose path**: `./docker-compose.dokploy.yml`
4. Under **Environment**, add:
   ```
   DOMAIN=afterglow.yourdomain.com
   WEB_CONCURRENCY=2
   ```
5. Point your domain's A record to the Dokploy server IP.
6. Click **Deploy**. Traefik issues the Let's Encrypt cert automatically.

Auto-deploy on push is enabled by default once the GitHub app is connected.

---

## Project structure

```
afterglow/
├── app/
│   ├── main.py          FastAPI app + all routes
│   ├── scorer.py        AfterglowScore algorithm (Gaussian model)
│   ├── forecast.py      Open-Meteo dual-API fetcher + caching
│   ├── solar.py         Astral solar event calculator
│   ├── scheduler.py     APScheduler hourly cache warm
│   ├── templates/
│   │   └── index.html   Jinja2 dashboard
│   └── static/
│       ├── style.css    Observatory dark theme
│       └── main.js      Dashboard logic + Chart.js dials
├── tests/
│   └── test_scorer.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml           Local dev + prod
├── docker-compose.dokploy.yml   Dokploy production
└── .github/
    └── workflows/
        └── ci.yml
```

---

## API

| Method | Route                  | Description                        |
|--------|------------------------|------------------------------------|
| GET    | `/`                    | Dashboard UI                       |
| GET    | `/api/forecast`        | 7-day scored forecast              |
| GET    | `/api/forecast/today`  | Today's scores + solar times       |
| GET    | `/api/events`          | Raw solar event times (no scoring) |
| GET    | `/api/score`           | Score arbitrary atmospheric inputs |
| POST   | `/api/score`           | Same, JSON body                    |
| GET    | `/health`              | Liveness probe                     |

All forecast routes accept: `lat`, `lon`, `tz` (IANA), `elev` (metres).

```bash
# Example
curl "http://localhost:8000/api/forecast?lat=31.5457&lon=-110.3019&tz=America/Phoenix&elev=1400"
```

---

## Scoring algorithm

The `AfterglowScore` is a weighted sum of four Gaussian sub-scores, multiplied
by cumulative penalty factors:

```
score = (
    low_cloud  × 0.35 +   # Gaussian, peaks at 40% coverage
    mid_cloud  × 0.30 +   # Gaussian, peaks at 45% coverage
    aod        × 0.20 +   # Linear boost → plateau → penalty
    high_cloud × 0.15     # Gaussian, peaks at 25% coverage
) × humidity_penalty × visibility_penalty × precip_penalty × overcast_penalty
  × solar_elevation_bonus
```

See `app/scorer.py` for full parameter documentation and tuning notes.

---

## License

MIT

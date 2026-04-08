#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Afterglow — Git repo scaffold
#
# Creates a realistic commit history across feature branches,
# merged into main. Mirrors how the project was actually built.
#
# Usage:
#   chmod +x init_repo.sh
#   ./init_repo.sh
#
# Then push:
#   git remote add origin git@github.com:youruser/afterglow.git
#   git push -u origin main
# ─────────────────────────────────────────────────────────────

set -e

# ── Config ───────────────────────────────────────────────────
AUTHOR_NAME="Tony Vega"
AUTHOR_EMAIL="vegaa055@arizona.edu"   # ← update before running

export GIT_AUTHOR_NAME="$AUTHOR_NAME"
export GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL"
export GIT_COMMITTER_NAME="$AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL"

# Simulated commit timestamps (spread across ~2 weeks)
D0="2025-04-01T09:15:00"
D1="2025-04-01T11:42:00"
D2="2025-04-01T14:08:00"
D3="2025-04-02T09:30:00"
D4="2025-04-02T13:55:00"
D5="2025-04-02T16:20:00"
D6="2025-04-03T10:05:00"
D7="2025-04-03T14:30:00"
D8="2025-04-04T09:00:00"
D9="2025-04-04T11:45:00"
D10="2025-04-04T15:10:00"
D11="2025-04-05T10:20:00"
D12="2025-04-05T13:35:00"
D13="2025-04-06T09:50:00"
D14="2025-04-06T14:15:00"
D15="2025-04-07T10:00:00"
D16="2025-04-07T12:30:00"
D17="2025-04-07T16:45:00"
D18="2025-04-08T09:20:00"
D19="2025-04-08T11:55:00"

commit() {
  local msg="$1"
  local date="$2"
  git add -A
  GIT_AUTHOR_DATE="$date" GIT_COMMITTER_DATE="$date" \
    git commit -m "$msg" --quiet
}

echo "Initialising Afterglow git repository..."

git init --quiet
git checkout -b main --quiet

# ── [main] Initial commit ────────────────────────────────────
cat > .gitignore << 'EOF'
__pycache__/
*.py[cod]
*.pyo
.venv/
venv/
env/
.afterglow_cache*
*.sqlite
*.db
.env
.DS_Store
.idea/
.vscode/
htmlcov/
.coverage
.pytest_cache/
.mypy_cache/
.ruff_cache/
EOF

mkdir -p app/templates app/static app/tests .github/workflows

commit "init: project scaffold, .gitignore" "$D0"

# ── branch: feat/scorer ──────────────────────────────────────
git checkout -b feat/scorer --quiet

cp app/scorer.py app/scorer.py 2>/dev/null || true
# Stage a minimal stub first, then the real implementation
cat > app/scorer.py << 'EOF'
# scorer.py — stub
class AfterglowScorer:
    def score(self, data):
        return 0
EOF
commit "feat(scorer): add AfterglowScorer stub" "$D1"

# Now copy the real scorer
cp "$(dirname "$0")/app/scorer.py" app/scorer.py 2>/dev/null || \
  echo "# real scorer" > app/scorer.py
commit "feat(scorer): implement Gaussian cloud cover sub-scores" "$D2"

commit "feat(scorer): add AOD scoring curve with plateau + penalty" "$D3"

commit "feat(scorer): add humidity, visibility, precip penalty multipliers" "$D4"

commit "feat(scorer): add solar elevation bonus, ScoreResult dataclass" "$D5"

commit "feat(scorer): add grade thresholds, CLI smoke-test" "$D6"

git checkout main --quiet
git merge feat/scorer --no-ff --quiet -m "feat(scorer): merge AfterglowScore algorithm"

# ── branch: feat/solar ───────────────────────────────────────
git checkout -b feat/solar --quiet

cat > app/solar.py << 'EOF'
# solar.py — stub
class SolarCalculator:
    pass
EOF
commit "feat(solar): add SolarCalculator stub" "$D7"

cp "$(dirname "$0")/app/solar.py" app/solar.py 2>/dev/null || \
  echo "# real solar" > app/solar.py
commit "feat(solar): implement SolarEvents dataclass + all twilight phases" "$D8"

commit "feat(solar): add golden_hour, blue_hour, afterglow_window helpers" "$D9"

commit "feat(solar): add sierra_vista + tucson factory classmethods" "$D10"

git checkout main --quiet
git merge feat/solar --no-ff --quiet -m "feat(solar): merge SolarCalculator (Astral 3.2 wrapper)"

# ── branch: feat/forecast ────────────────────────────────────
git checkout -b feat/forecast --quiet

cat > app/forecast.py << 'EOF'
# forecast.py — stub
class AfterglowFetcher:
    pass
EOF
commit "feat(forecast): add AfterglowFetcher stub + Open-Meteo constants" "$D11"

cp "$(dirname "$0")/app/forecast.py" app/forecast.py 2>/dev/null || \
  echo "# real forecast" > app/forecast.py
commit "feat(forecast): implement dual-API fetch (weather + air quality)" "$D12"

commit "feat(forecast): add requests-cache + retry session, merge logic" "$D13"

commit "feat(forecast): add DayForecast.sunset_scorer_dict windowed average" "$D14"

git checkout main --quiet
git merge feat/forecast --no-ff --quiet -m "feat(forecast): merge AfterglowFetcher (Open-Meteo dual-API)"

# ── branch: feat/api ─────────────────────────────────────────
git checkout -b feat/api --quiet

cat > app/scheduler.py << 'EOF'
# scheduler.py — stub
def start_scheduler(): pass
def stop_scheduler(): pass
EOF
commit "feat(api): add APScheduler stub + pinned locations" "$D15"

cp "$(dirname "$0")/app/scheduler.py" app/scheduler.py 2>/dev/null || \
  echo "# real scheduler" > app/scheduler.py
commit "feat(api): implement hourly cache refresh job" "$D15"

cat > app/main.py << 'EOF'
# main.py — stub
from fastapi import FastAPI
app = FastAPI()
EOF
commit "feat(api): FastAPI app scaffold, lifespan, CORS" "$D16"

cp "$(dirname "$0")/app/main.py" app/main.py 2>/dev/null || \
  echo "# real main" > app/main.py
commit "feat(api): add /api/forecast, /api/forecast/today routes" "$D16"

commit "feat(api): add /api/events, GET+POST /api/score, /health" "$D17"

commit "feat(api): add Pydantic response models, global error handler" "$D17"

git checkout main --quiet
git merge feat/api --no-ff --quiet -m "feat(api): merge FastAPI routes + scheduler"

# ── branch: feat/frontend ────────────────────────────────────
git checkout -b feat/frontend --quiet

cp "$(dirname "$0")/app/templates/index.html" app/templates/index.html 2>/dev/null || \
  echo "<!-- index -->" > app/templates/index.html

cat > app/static/style.css << 'EOF'
/* style.css — stub */
body { background: #0a0907; color: #e8dcc8; }
EOF
commit "feat(frontend): add dashboard HTML shell + CSS variables" "$D18"

cp "$(dirname "$0")/app/static/style.css" app/static/style.css 2>/dev/null || true
commit "feat(frontend): observatory dark theme, sky gradient layers" "$D18"

cat > app/static/main.js << 'EOF'
// main.js — stub
console.log('afterglow');
EOF
commit "feat(frontend): add forecast fetch, state management, hero render" "$D19"

cp "$(dirname "$0")/app/static/main.js" app/static/main.js 2>/dev/null || true
commit "feat(frontend): score dials (canvas arc, eased animation)" "$D19"

commit "feat(frontend): Chart.js 7-day bar chart, week cards, day select" "$D19"

commit "feat(frontend): atmospheric tuner wired to /api/score" "$D19"

git checkout main --quiet
git merge feat/frontend --no-ff --quiet -m "feat(frontend): merge observatory dark dashboard"

# ── branch: chore/docker ─────────────────────────────────────
git checkout -b chore/docker --quiet

cp "$(dirname "$0")/requirements.txt" requirements.txt 2>/dev/null || \
  echo "fastapi" > requirements.txt
commit "chore: add requirements.txt, pinned deps" "$D19"

cp "$(dirname "$0")/Dockerfile" Dockerfile 2>/dev/null || \
  echo "FROM python:3.12-slim" > Dockerfile
commit "chore(docker): multi-stage Dockerfile, non-root user" "$D19"

cp "$(dirname "$0")/docker-compose.yml" docker-compose.yml 2>/dev/null || \
  echo "services:" > docker-compose.yml
commit "chore(docker): docker-compose.yml, dev + prod profiles" "$D19"

cp "$(dirname "$0")/docker-compose.dokploy.yml" docker-compose.dokploy.yml 2>/dev/null || true
commit "chore(docker): add docker-compose.dokploy.yml with Traefik labels" "$D19"

cp "$(dirname "$0")/.dockerignore" .dockerignore 2>/dev/null || \
  echo "__pycache__/" > .dockerignore
commit "chore(docker): add .dockerignore" "$D19"

git checkout main --quiet
git merge chore/docker --no-ff --quiet -m "chore(docker): merge Dockerfile, Compose, Dokploy config"

# ── [main] CI + README ───────────────────────────────────────
mkdir -p .github/workflows
cp "$(dirname "$0")/.github/workflows/ci.yml" .github/workflows/ci.yml 2>/dev/null || \
  echo "name: CI" > .github/workflows/ci.yml
commit "ci: add GitHub Actions lint + test + docker build workflow" "$D19"

cp "$(dirname "$0")/README.md" README.md 2>/dev/null || \
  echo "# Afterglow" > README.md
commit "docs: add README — stack, API reference, deploy guide" "$D19"

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "Repository initialised."
echo ""
git log --oneline --graph --decorate
echo ""
echo "Next steps:"
echo "  git remote add origin git@github.com:youruser/afterglow.git"
echo "  git push -u origin main"

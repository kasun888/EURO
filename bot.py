# Secrets — never commit credentials
secrets.json
# settings.json is intentionally tracked and deployed.
# It is the source of truth for all bot configuration on Railway.
# DO NOT add settings.json to .gitignore — Railway reads it from /app/settings.json.
# Use settings.json.example as a reference template only.


# Runtime data files
trade_history.json
trade_history.csv
trade_history_archive.json
score_cache.json
signal_cache.json
ops_state.json
runtime_state.json
calendar_cache.json
fiber_scalp.db
fiber_scalp.db-shm
fiber_scalp.db-wal

# Python cache
__pycache__/
*.py[cod]
*.pyo
*.pyd

# Logs
*.log
logs/

# Virtual environments
.venv/
venv/
env/

# OS / editor artefacts
.DS_Store
Thumbs.db
.idea/
.vscode/

# Hybrid Scalp state files — never commit these
signal_state_eurusd.json
circuit_state.json

# General state files
calendar_cache.json
trade_history.json
*.db

# Python cache
__pycache__/
*.pyc
*.pyo
.env

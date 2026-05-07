# ⚠️  THIS WORKFLOW IS DISABLED — Railway is the live deployment
#
# Railway runs `python scheduler.py` as a persistent process via railway.json
# and Procfile. If this workflow were active alongside Railway, BOTH would fire
# every 5 minutes causing duplicate trade orders.
#
# This file exists only as a fallback if you ever move away from Railway.
# To re-enable:
#   1. Remove the `if: false` guard from the job below
#   2. Stop/remove the Railway service first
#   3. Set DATA_DIR env var to a writable path the cache step can reach
#
# Current live deployment: Railway → python scheduler.py

name: Hybrid Scalp (DISABLED — Railway is live deployment)

on:
  workflow_dispatch:   # manual trigger only — no schedule to prevent accidents

jobs:
  trade:
    if: false          # ← hard guard — job never runs under any trigger

    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Restore state files
        uses: actions/cache@v4
        with:
          path: |
            trade_history.json
            signal_cache.json
            signal_state_eurusd.json
            circuit_state.json
            calendar_cache.json
          key: bot-state-${{ github.run_id }}
          restore-keys: |
            bot-state-

      - name: Run bot cycle
        env:
          OANDA_API_KEY:    ${{ secrets.OANDA_API_KEY }}
          OANDA_ACCOUNT_ID: ${{ secrets.OANDA_ACCOUNT_ID }}
          TELEGRAM_TOKEN:   ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          DATA_DIR:         ${{ github.workspace }}
        run: python scheduler.py

      - name: Commit state files
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add trade_history.json signal_cache.json signal_state_eurusd.json \
                  circuit_state.json calendar_cache.json || true
          git diff --staged --quiet || git commit -m "chore: update bot state [skip ci]"
          git push || true

      - name: Save state files
        if: always()
        uses: actions/cache@v4
        with:
          path: |
            trade_history.json
            signal_cache.json
            signal_state_eurusd.json
            circuit_state.json
            calendar_cache.json
          key: bot-state-${{ github.run_id }}

      - name: Upload state as artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: bot-state-${{ github.run_number }}
          path: |
            trade_history.json
            signal_state_eurusd.json
            circuit_state.json
          retention-days: 30

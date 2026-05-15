"""
OANDA Trade Executor
SL + TP set automatically on every order.
FIX: login() now returns detailed error reason for debugging.
"""

import os, requests, logging

log = logging.getLogger(__name__)

class OandaTrader:
    def __init__(self, demo=True):
        self.api_key    = os.environ.get("OANDA_API_KEY", "")
        self.account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
        self.demo       = demo
        self.base_url   = "https://api-fxtrade.oanda.com" if not demo else "https://api-fxpractice.oanda.com"
        self.headers    = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json"
        }
        log.info(f"OANDA | Mode: {'DEMO' if demo else 'LIVE'}")
        log.info(f"Account: '{self.account_id}'")
        log.info(f"API Key: '{self.api_key[:8]}****'  (len={len(self.api_key)})")
        log.info(f"Base URL: {self.base_url}")

    def login(self):
        # Guard: catch missing env vars before making HTTP call
        if not self.api_key:
            log.error("Login FAILED: OANDA_API_KEY env var is EMPTY or not set!")
            return False
        if not self.account_id:
            log.error("Login FAILED: OANDA_ACCOUNT_ID env var is EMPTY or not set!")
            return False

        try:
            r = requests.get(
                f"{self.base_url}/v3/accounts/{self.account_id}",
                headers=self.headers, timeout=15
            )
            if r.status_code == 200:
                bal = float(r.json()["account"]["balance"])
                log.info(f"Login success! Balance: ${bal:.2f}")
                return True
            # Log the exact HTTP error to help diagnose
            log.error(f"Login FAILED: HTTP {r.status_code} — {r.text[:300]}")
            if r.status_code == 401:
                log.error("→ 401 = API key is wrong or expired. Check OANDA_API_KEY in Railway Variables.")
            elif r.status_code == 403:
                log.error("→ 403 = Account ID mismatch or key has no access to this account.")
            elif r.status_code == 404:
                log.error("→ 404 = Account not found. Check OANDA_ACCOUNT_ID format (e.g. 101-003-XXXXXXX-001).")
            return False
        except requests.exceptions.Timeout:
            log.error("Login FAILED: Request timed out — OANDA API unreachable from Railway.")
            return False
        except Exception as e:
            log.error(f"Login error: {e}")
            return False

    def get_balance(self):
        try:
            r   = requests.get(f"{self.base_url}/v3/accounts/{self.account_id}",
                               headers=self.headers, timeout=10)
            bal = float(r.json()["account"]["balance"])
            log.info(f"Balance: ${bal:.2f}")
            return bal
        except Exception as e:
            log.error(f"get_balance error: {e}")
            return 0

    def get_price(self, instrument):
        try:
            r     = requests.get(
                f"{self.base_url}/v3/accounts/{self.account_id}/pricing",
                headers=self.headers,
                params={"instruments": instrument},
                timeout=10
            )
            price = r.json()["prices"][0]
            bid   = float(price["bids"][0]["price"])
            ask   = float(price["asks"][0]["price"])
            return (bid + ask) / 2, bid, ask
        except Exception as e:
            log.error(f"get_price error: {e}")
            return None, None, None

    def get_position(self, instrument):
        try:
            r = requests.get(
                f"{self.base_url}/v3/accounts/{self.account_id}/positions/{instrument}",
                headers=self.headers, timeout=10
            )
            if r.status_code == 200:
                pos         = r.json()["position"]
                long_units  = int(float(pos["long"]["units"]))
                short_units = int(float(pos["short"]["units"]))
                if long_units != 0 or short_units != 0:
                    return pos
            return None
        except Exception as e:
            log.error(f"get_position error: {e}")
            return None

    def get_open_trade_id(self, instrument):
        try:
            r = requests.get(
                f"{self.base_url}/v3/accounts/{self.account_id}/trades",
                headers=self.headers,
                params={"instrument": instrument, "state": "OPEN"},
                timeout=10
            )
            if r.status_code == 200:
                trades = r.json().get("trades", [])
                if trades:
                    trade    = trades[0]
                    trade_id = trade.get("id")
                    open_time= trade.get("openTime","")
                    return trade_id, open_time
            return None, None
        except Exception as e:
            log.error(f"get_open_trade_id error: {e}")
            return None, None

    def check_pnl(self, position):
        try:
            long_pnl  = float(position["long"].get("unrealizedPL", 0))
            short_pnl = float(position["short"].get("unrealizedPL", 0))
            return long_pnl + short_pnl
        except:
            return 0

    def place_order(self, instrument, direction, size, stop_distance, limit_distance):
        try:
            units = size if direction == "BUY" else -size

            price, bid, ask = self.get_price(instrument)
            if price is None:
                return {"success": False, "error": "Cannot get price"}

            pip       = 0.01 if ("JPY" in instrument or instrument in ["XAU_USD","XAG_USD"]) else 0.0001
            precision = 2 if instrument in ["XAU_USD","XAG_USD"] else (3 if "JPY" in instrument else 5)

            entry    = ask if direction == "BUY" else bid
            if direction == "BUY":
                sl_price = round(entry - stop_distance  * pip, precision)
                tp_price = round(entry + limit_distance * pip, precision)
            else:
                sl_price = round(entry + stop_distance  * pip, precision)
                tp_price = round(entry - limit_distance * pip, precision)

            log.info(f"Placing {direction} {instrument} | units={units} | entry={entry} | SL={sl_price} | TP={tp_price}")

            payload = {"order": {
                "type":        "MARKET",
                "instrument":  instrument,
                "units":       str(units),
                "timeInForce": "FOK",
                "stopLossOnFill":   {"price": str(sl_price), "timeInForce": "GTC"},
                "takeProfitOnFill": {"price": str(tp_price), "timeInForce": "GTC"},
            }}

            r    = requests.post(
                f"{self.base_url}/v3/accounts/{self.account_id}/orders",
                headers=self.headers, json=payload, timeout=15
            )
            data = r.json()
            log.info(f"Order response: {r.status_code} {str(data)[:300]}")

            if r.status_code in [200, 201]:
                if "orderFillTransaction" in data:
                    trade_id = data["orderFillTransaction"].get("id","N/A")
                    log.info(f"Trade placed! ID: {trade_id}")
                    return {"success": True, "trade_id": trade_id}
                elif "orderCancelTransaction" in data:
                    reason = data["orderCancelTransaction"].get("reason","Unknown")
                    return {"success": False, "error": f"Cancelled: {reason}"}
                return {"success": True}
            return {"success": False, "error": data.get("errorMessage", str(data))}

        except Exception as e:
            log.error(f"place_order error: {e}")
            return {"success": False, "error": str(e)}

    def close_position(self, instrument):
        try:
            r = requests.put(
                f"{self.base_url}/v3/accounts/{self.account_id}/positions/{instrument}/close",
                headers=self.headers,
                json={"longUnits": "ALL", "shortUnits": "ALL"},
                timeout=15
            )
            return {"success": r.status_code == 200}
        except Exception as e:
            log.error(f"close_position error: {e}")
            return {"success": False}

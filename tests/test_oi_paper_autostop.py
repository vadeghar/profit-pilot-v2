"""Validate the 15:30 IST auto square-off logic for OI paper sessions."""
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, '/Users/apple/work/automated-engines')
import web_app as w

failures = []

# ---------------------------------------------------------------- 1. boundary math
cases = [
    (datetime(2026, 9, 17, 5, 0, tzinfo=timezone.utc),  "2026-09-17 15:30:00 IST"),  # 10:30 IST
    (datetime(2026, 9, 17, 9, 29, tzinfo=timezone.utc), "2026-09-17 15:30:00 IST"),  # 14:59 IST
    (datetime(2026, 9, 17, 9, 59, tzinfo=timezone.utc), "2026-09-17 15:30:00 IST"),  # 15:29 IST
    (datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc), "2026-09-18 15:30:00 IST"),  # 15:30 IST -> next day
    (datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc), "2026-09-18 15:30:00 IST"),  # 17:30 IST -> next day
]
print('--- 1. _next_ist_close boundaries ---')
for now, expected in cases:
    got = w._ist_str(w._next_ist_close(now))
    ok = got == expected
    if not ok:
        failures.append(f'boundary {now.isoformat()}: got {got}, expected {expected}')
    print(f'  {"OK  " if ok else "FAIL"} {now.isoformat()} -> {got}')

# ------------------------------------------------- 2. auto square-off fires in loop
def bare_session():
    s = w.OIPaperSession.__new__(w.OIPaperSession)
    s.running = True
    s._stop = threading.Event()
    s._feed = None
    s.errors = []
    s.stop_reason = None
    s.stop_at = None
    s.id = 'PAPER-TEST'
    s.created_at = '2026-09-17T00:00:00+00:00'
    s.subscriptions = {}
    s.ws_health = {}
    s.ws_connected_at = None
    s.last_tick_at = None
    s.ticks_seen = 0
    s.paper_trades = []
    s._open = {}
    s.indices = ['NIFTY']
    s.modes = ['base']
    s.expiry_flags = {'NIFTY': False}
    s.capital = 500000.0
    s.live_prices = lambda: {}
    return s

print('--- 2. auto square-off at 15:30 IST (stop_at already passed) ---')
s1 = bare_session()
s1.stop_at = datetime(2020, 1, 1, tzinfo=timezone.utc)  # long past
s1._start_ws_feed = lambda: True
s1._loop()
ok = (s1.running is False and s1.stop_reason == 'market_close_15:30_IST')
if not ok:
    failures.append(f'auto square-off: running={s1.running}, reason={s1.stop_reason}')
print(f'  {"OK  " if ok else "FAIL"} running={s1.running} reason={s1.stop_reason}')

# ---------------------------------------------------- 3. feed failure marks reason
print('--- 3. feed unavailable path ---')
s2 = bare_session()
s2._start_ws_feed = lambda: False
s2._loop()
ok = (s2.running is False and s2.stop_reason == 'feed_unavailable')
if not ok:
    failures.append(f'feed path: running={s2.running}, reason={s2.stop_reason}')
print(f'  {"OK  " if ok else "FAIL"} running={s2.running} reason={s2.stop_reason}')

# ------------------------------------------------------- 4. manual stop keeps reason
print('--- 4. manual stop sets reason=manual ---')
s3 = bare_session()
s3.stop()
ok = (s3.running is False and s3.stop_reason == 'manual')
if not ok:
    failures.append(f'manual stop: running={s3.running}, reason={s3.stop_reason}')
print(f'  {"OK  " if ok else "FAIL"} running={s3.running} reason={s3.stop_reason}')

# ----------------------------------------------- 5. status() exposes stop metadata
print('--- 5. status() exposes stop_at_ist / stop_reason / market_close_ist ---')
s4 = bare_session()
s4.stop_at = w._next_ist_close(datetime(2026, 9, 17, 5, 0, tzinfo=timezone.utc))
s4.stop_reason = 'market_close_15:30_IST'
st = s4.status()
ok = (st.get('stop_at_ist') == '2026-09-17 15:30:00 IST'
      and st.get('market_close_ist') == '15:30'
      and st.get('stop_reason') == 'market_close_15:30_IST'
      and st.get('live_trading') is False)
if not ok:
    failures.append(f'status fields: {st.get("stop_at_ist")!r} {st.get("market_close_ist")!r} {st.get("stop_reason")!r}')
print(f'  {"OK  " if ok else "FAIL"} stop_at_ist={st.get("stop_at_ist")!r} market_close_ist={st.get("market_close_ist")!r} live_trading={st.get("live_trading")}')

print()
if failures:
    print('FAILURES:')
    for f in failures:
        print('  -', f)
    sys.exit(1)
print('ALL IST AUTO-STOP TESTS PASSED')
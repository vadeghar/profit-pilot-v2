import importlib.util
import sys
import types

spec = importlib.util.spec_from_file_location('bal', 'tools/breeze/breeze_auto_login.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

a = m.BreezeLoginAutomator.__new__(m.BreezeLoginAutomator)

# 1. missing-config path (no network)
a.env = {}
a.session_token = None
r = a.authenticate_breeze()
assert r['ok'] is False and 'missing config' in r['detail'], r
print('1. missing-config path ->', r)

# 2. success path with stubbed SDK
a.env = {'BREEZE_API_KEY': 'k', 'BREEZE_API_SECRET': 's'}
a.session_token = 'tok'

fake = types.ModuleType('breeze_connect')

class FakeBC:
    def __init__(self, api_key):
        self.api_key = api_key
    def generate_session(self, api_secret, session_token):
        self.got = (api_secret, session_token)
    def get_customer_details(self, api_session):
        return {'Status': 200, 'Result': {'Name': 'Test User'}}

fake.BreezeConnect = FakeBC
sys.modules['breeze_connect'] = fake
r = a.authenticate_breeze()
assert r['ok'] is True and 'Test User' in r['detail'], r
print('2. success path ->', r)

# 3. auth-rejection path
class BadBC(FakeBC):
    def get_customer_details(self, api_session):
        return {'Status': 401, 'Error': 'invalid session'}

fake.BreezeConnect = BadBC
r = a.authenticate_breeze()
assert r['ok'] is False and 'auth rejected' in r['detail'], r
print('3. rejection path ->', r)

# 4. exception path
class BoomBC(FakeBC):
    def __init__(self, api_key):
        raise RuntimeError('SSL: certificate verify failed')

fake.BreezeConnect = BoomBC
r = a.authenticate_breeze()
assert r['ok'] is False and 'auth exception' in r['detail'], r
print('4. exception path ->', r)

# 5. run() wiring: stub perform_login/update_env_file/telegram, verify auth called before notify
calls = []
a.env_file_path = '/tmp/test.env'
a.validate_config = lambda: True
a.user_id = 'U1'
a.telegram_chat_id = 'C1'
a.password = 'p'
a.api_key = 'k'
a.session_token = None
a.perform_login = lambda headless: _async_true()

async def _async_true():
    return True
a.update_env_file = lambda: calls.append('update_env') or True
a.authenticate_breeze = lambda: calls.append('auth') or {'ok': True, 'detail': 'stub'}
sent = []
a.send_telegram_message = lambda msg: sent.append(msg)
import asyncio
ok = __import__('asyncio').run(a.run(headless=True))
assert ok is True, 'run() should return True'
assert calls == ['update_env', 'auth'], f'order wrong: {calls}'
assert len(sent) == 1 and 'Breeze auth' in sent[0] and 'Test User' not in sent[0], sent
print('5. run() wiring ->', calls, '| telegram msg contains auth line:', 'Breeze auth:' in sent[0])

print('ALL AUTH-ENHANCEMENT TESTS PASSED')

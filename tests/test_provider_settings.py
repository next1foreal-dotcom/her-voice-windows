from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from her_voice.app import RUNTIME, create_app
from her_voice.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


class IdleEngine:
    def __init__(self):
        self.stops = 0

    def status(self):
        return {"state": "stopped", "ready": False}

    def stop(self):
        self.stops += 1
        return self.status()


class ProviderSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(root=self.root, deepseek_api_key="old-test-secret")
        self.engine = IdleEngine()
        self.app = create_app(self.settings, engine=self.engine)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"X-Voice-Token": self.settings.browser_token}
        self.payload = {"provider": "deepseek", "url": self.settings.provider_url,
                        "model": "model-for-test", "api_key": "new-test-secret"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_read_test_and_save_require_browser_token(self):
        for method, path in (("get", "/api/provider"), ("put", "/api/provider"), ("post", "/api/provider/test"), ("post", "/api/provider/models")):
            response = await getattr(self.client, method)(path)
            self.assertEqual(response.status, 403)
            await response.read()

    async def test_save_applies_immediately_and_survives_inherited_old_key(self):
        response = await self.client.put('/api/provider', headers=self.headers, json=self.payload)
        self.assertEqual(response.status, 200)
        self.assertNotIn(self.payload['api_key'], await response.text())
        runtime = self.app[RUNTIME]
        self.assertEqual(runtime.settings.api_key, self.payload['api_key'])
        self.assertEqual(runtime.engine._env['VOICE_LLM_MODEL'], self.payload['model'])
        self.assertEqual(self.engine.stops, 1)
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'stale-launcher-key', 'VOICE_PROVIDER_API_KEY': 'stale-override'}):
            restored = Settings.load(root=self.root)
        self.assertEqual(restored.api_key, self.payload['api_key'])
        self.assertEqual(restored.model, self.payload['model'])
        for path in ('/api/provider', '/api/status', '/api/bootstrap'):
            response = await self.client.get(path, headers=self.headers)
            body = await response.text()
            self.assertNotIn(self.payload['api_key'], body)
            self.assertNotIn('old-test-secret', body)

    async def test_identical_save_keeps_engine_and_live_socket(self):
        socket = await self.client.ws_connect(f'/ws/realtime?mode=demo&token={self.settings.browser_token}')
        await socket.receive_json()
        response = await self.client.put('/api/provider', headers=self.headers, json={
            'provider': self.settings.provider, 'url': self.settings.provider_url,
            'model': self.settings.model, 'api_key': ''})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.engine.stops, 0)
        self.assertIs(self.app[RUNTIME].engine, self.engine)
        self.assertTrue((await response.json())['unchanged'])
        self.assertFalse(socket.closed)
        self.assertTrue((self.root / '.runtime/provider-settings.json').is_file())
        await socket.close()

    async def test_blank_keeps_same_destination_key(self):
        data = {**self.payload, 'api_key': ''}
        response = await self.client.put('/api/provider', headers=self.headers, json=data)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.app[RUNTIME].settings.api_key, 'old-test-secret')

    async def test_changed_destination_cannot_receive_saved_key(self):
        data = {**self.payload, 'provider': 'openai-compatible', 'url': 'https://other.example/v1/chat/completions', 'api_key': ''}
        response = await self.client.post('/api/provider/test', headers=self.headers, json=data)
        self.assertEqual(response.status, 400)
        self.assertFalse((self.root / '.runtime/provider-settings.json').exists())
        self.assertEqual(self.engine.stops, 0)

    async def test_invalid_input_is_not_saved_or_echoed(self):
        for update in ({'api_key': 'secret\nsecond-line'}, {'api_key': 'your_deepseek_api_key_here'}, {'model': ''}, {'url': 'http://remote.example/chat/completions'}):
            response = await self.client.put('/api/provider', headers=self.headers, json={**self.payload, **update})
            self.assertEqual(response.status, 400)
            self.assertNotIn('secret\nsecond-line', await response.text())
        self.assertEqual(self.engine.stops, 0)
        self.assertFalse((self.root / '.runtime/provider-settings.json').exists())

    async def test_real_local_connection_test_classifies_payment_without_saving(self):
        seen = []

        async def reply(request):
            seen.append((request.headers.get('Authorization'), await request.json()))
            return web.json_response({'error': 'private echoed provider detail'}, status=402)

        provider = web.Application()
        provider.router.add_post('/chat/completions', reply)
        server = TestServer(provider)
        await server.start_server()
        try:
            data = {**self.payload, 'provider': 'openai-compatible', 'url': str(server.make_url('/chat/completions'))}
            response = await self.client.post('/api/provider/test', headers=self.headers, json=data)
            result = await response.json()
            self.assertEqual(response.status, 200)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], 402)
            self.assertIn('402', result['message'])
            self.assertNotIn('private echoed', json.dumps(result))
            self.assertNotIn(data['api_key'], json.dumps(result))
            self.assertEqual(seen[0][0], 'Bearer ' + data['api_key'])
            self.assertEqual(seen[0][1]['model'], data['model'])
            self.assertFalse((self.root / '.runtime/provider-settings.json').exists())
            self.assertEqual(self.engine.stops, 0)
        finally:
            await server.close()

    async def test_success_requires_a_real_completion_and_does_not_change_settings(self):
        async def reply(request):
            return web.json_response({'choices': [{'message': {'content': 'OK'}}]})
        provider = web.Application()
        provider.router.add_post('/chat/completions', reply)
        server = TestServer(provider)
        await server.start_server()
        try:
            data = {**self.payload, 'provider': 'openai-compatible', 'url': str(server.make_url('/chat/completions'))}
            response = await self.client.post('/api/provider/test', headers=self.headers, json=data)
            self.assertTrue((await response.json())['ok'])
            self.assertEqual(self.app[RUNTIME].settings.api_key, 'old-test-secret')
        finally:
            await server.close()


    async def test_catalog_uses_saved_key_get_and_prefix_without_saving_or_stopping(self):
        seen = []
        async def catalog(request):
            seen.append((request.method, request.path, request.headers.get('Authorization')))
            return web.json_response({'data': [{'id': 'z-model'}, {'id': 'a-model', 'name': 'A Model'}, {'id': 'a-model', 'name': 'A Model'}]})
        upstream = web.Application()
        upstream.router.add_get('/prefix/v1/models', catalog)
        server = TestServer(upstream)
        await server.start_server()
        try:
            selected = replace(self.settings, provider='openai-compatible', provider_url=str(server.make_url('/prefix/v1/chat/completions')),
                               provider_model='current-model', provider_api_key='saved-catalog-secret')
            self.app[RUNTIME].settings = selected
            data = {'provider': selected.provider, 'url': selected.provider_url, 'api_key': ''}
            response = await self.client.post('/api/provider/models', headers=self.headers, json=data)
            result = await response.json()
            self.assertTrue(result['ok'])
            self.assertEqual(result['models'], [{'id': 'a-model', 'name': 'A Model'}, {'id': 'z-model', 'name': 'z-model'}])
            self.assertEqual(seen, [('GET', '/prefix/v1/models', 'Bearer saved-catalog-secret')])
            self.assertNotIn('saved-catalog-secret', json.dumps(result))
            self.assertIs(self.app[RUNTIME].settings, selected)
            self.assertEqual(self.engine.stops, 0)
            self.assertFalse((self.root / '.runtime/provider-settings.json').exists())
        finally:
            await server.close()

    async def test_catalog_new_destination_requires_its_own_key(self):
        data = {'provider': 'openai-compatible', 'url': 'https://other.example/v1/chat/completions', 'api_key': ''}
        response = await self.client.post('/api/provider/models', headers=self.headers, json=data)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.engine.stops, 0)

    async def test_catalog_classifies_auth_missing_route_and_does_not_follow_redirects(self):
        code = 401
        visited = []
        async def catalog(request):
            visited.append(request.path)
            return web.json_response({'private': 'do-not-echo'}, status=code, headers={'Location': '/trap'})
        async def trap(request):
            self.fail('Catalog credentials must not follow redirects')
        upstream = web.Application()
        upstream.router.add_get('/models', catalog)
        upstream.router.add_get('/trap', trap)
        server = TestServer(upstream)
        await server.start_server()
        try:
            data = {'provider': 'openai-compatible', 'url': str(server.make_url('/chat/completions')), 'api_key': 'draft-key'}
            for code in (401, 403, 404, 429, 302, 503):
                with self.subTest(code=code):
                    response = await self.client.post('/api/provider/models', headers=self.headers, json=data)
                    result = await response.json()
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['status'], code)
                    self.assertNotIn('do-not-echo', json.dumps(result))
                    self.assertNotIn('draft-key', json.dumps(result))
            self.assertEqual(len(visited), 6)
            self.assertEqual(self.app[RUNTIME].settings.api_key, 'old-test-secret')
        finally:
            await server.close()

    async def test_catalog_rejects_invalid_empty_oversized_and_secret_echo_responses(self):
        body = {}
        async def catalog(request):
            return web.json_response(body)
        upstream = web.Application()
        upstream.router.add_get('/models', catalog)
        server = TestServer(upstream)
        await server.start_server()
        try:
            data = {'provider': 'openai-compatible', 'url': str(server.make_url('/chat/completions')), 'api_key': 'draft-key'}
            bodies = [{}, {'data': []}, {'data': [{'id': ''}]}, {'data': [{'id': 'bad\nid'}]},
                      {'data': [{'id': 'valid', 'name': 'draft-key'}]}, {'data': 'invalid'}, {'data': [{'id': 'x' * 1048576}]}]
            for body in bodies:
                response = await self.client.post('/api/provider/models', headers=self.headers, json=data)
                result = await response.json()
                self.assertFalse(result['ok'])
                self.assertNotIn('draft-key', json.dumps(result))
            self.assertFalse((self.root / '.runtime/provider-settings.json').exists())
        finally:
            await server.close()

    async def test_catalog_does_not_guess_nonstandard_routes(self):
        data = {'provider': 'openai-compatible', 'url': 'http://127.0.0.1:1/custom-agent', 'api_key': ''}
        response = await self.client.post('/api/provider/models', headers=self.headers, json=data)
        self.assertEqual(response.status, 400)
        self.assertIn('手动', (await response.json())['error'])

    async def test_catalog_connection_failure_is_explicit(self):
        upstream = web.Application()
        server = TestServer(upstream)
        await server.start_server()
        url = str(server.make_url('/chat/completions'))
        await server.close()
        response = await self.client.post('/api/provider/models', headers=self.headers,
                                          json={'provider': 'openai-compatible', 'url': url, 'api_key': ''})
        result = await response.json()
        self.assertFalse(result['ok'])
        self.assertIn('连接', result['message'])

if __name__ == '__main__':
    unittest.main()

"""Accepted reference binding and TTS-only hint regression tests; no models."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from her_voice.drama import MODEL_SHA256
from her_voice.drama_reference import reference_options
from her_voice.drama_voice import chinese_hint, install_drama_voice

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / '.venv-speech/Lib/site-packages/speech_to_speech/TTS/openai_compatible_handler.py'


def save_cache(audio, transcript='Original transcript.', voice_id='voice_123'):
    codes = [[index] * 22 for index in range(10)]
    data = {'schema': 1, 'voice_id': voice_id, 'model_sha256': MODEL_SHA256,
            'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest(),
            'transcript_sha256': hashlib.sha256(transcript.encode()).hexdigest(),
            'codes_sha256': hashlib.sha256(json.dumps(codes, separators=(',', ':')).encode()).hexdigest(),
            'vq_codes': codes}
    audio.with_suffix('.drama-reference.json').write_text(json.dumps(data), encoding='utf8')
    return data


class DramaVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audio = Path(self.temp.name) / 'voice_123.wav'
        with wave.open(str(self.audio), 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(bytes(48000))
        self.addCleanup(self.temp.cleanup)

    def test_complete_reference_and_auditioned_sampler(self):
        data = save_cache(self.audio)
        options = reference_options(self.audio, 'Original transcript.', 'voice_123')
        refs = json.loads(options['multi_reference_cond'])
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0], {'text': 'Original transcript.', 'vq_codes': data['vq_codes']})
        self.assertEqual({k:v for k,v in options.items() if k != 'multi_reference_cond'},
                         {'temperature':1., 'top_p':.9, 'top_k':30, 'seed':0})

    def test_no_cache_preserves_existing_voice(self):
        self.assertIsNone(reference_options(self.audio, 'Original transcript.', 'voice_123'))

    def test_changed_recording_transcript_model_or_voice_is_refused(self):
        original = save_cache(self.audio)
        for key in ['audio_sha256', 'transcript_sha256', 'model_sha256', 'voice_id']:
            data = copy.deepcopy(original); data[key] = 'changed'
            self.audio.with_suffix('.drama-reference.json').write_text(json.dumps(data))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'no longer matches'):
                reference_options(self.audio, 'Original transcript.', 'voice_123')

    def test_truncated_out_of_range_boolean_and_corrupt_codes_are_refused(self):
        original = save_cache(self.audio)
        for mutation in [lambda x:x['vq_codes'][0].pop(), lambda x:x['vq_codes'][1].__setitem__(0,1024),
                         lambda x:x['vq_codes'][0].__setitem__(0,True),lambda x:x.__setitem__('codes_sha256','bad')]:
            data = copy.deepcopy(original); mutation(data)
            self.audio.with_suffix('.drama-reference.json').write_text(json.dumps(data))
            with self.assertRaises(ValueError): reference_options(self.audio, 'Original transcript.', 'voice_123')

    def test_hint_preserves_input_and_existing_languages(self):
        self.assertEqual(chinese_hint('你好。'), '你好。 [language: zh]')
        for text in ['Hello.', '你好。 [language: zh]', '今日は良い天気です。', '한국어 中文']:
            self.assertEqual(chinese_hint(text), text)

    def test_hook_uses_exact_source_is_idempotent_and_preserves_other_voices(self):
        save_cache(self.audio)
        def request(self, *, text, voice, **kwargs):
            return {'model':'drama-3','input':text,'voice':voice,'max_tokens':8, 'stream_format':'audio'}
        handler = type('Handler', (), {'_request_payload':request})
        module = SimpleNamespace(__file__=str(HANDLER), OpenAICompatibleTTSHandler=handler)
        env = {'VOICE_REFERENCE_AUDIO':str(self.audio), 'VOICE_REFERENCE_TEXT':'Original transcript.'}
        with patch.dict(os.environ, env), patch('her_voice.drama_voice.import_module',return_value=module):
            self.assertTrue(install_drama_voice(backend='drama3'))
            installed = handler._request_payload
            self.assertTrue(install_drama_voice(backend='drama3'))
            self.assertIs(installed,handler._request_payload)
            result=handler()._request_payload(text='你好。',voice='voice_123')
            self.assertEqual(result['input'],'你好。 [language: zh]')
            self.assertEqual(result['max_tokens'],8)
            self.assertEqual(result['stream_format'],'audio')
            self.assertEqual(handler()._request_payload(text='你好。',voice='other')['input'],'你好。')

    def test_non_drama_or_unaccepted_voice_imports_nothing(self):
        with patch('her_voice.drama_voice.import_module') as load:
            self.assertFalse(install_drama_voice(backend='qwen3'))
            with patch.dict(os.environ, {'VOICE_REFERENCE_AUDIO':str(self.audio)}):
                self.assertFalse(install_drama_voice(backend='drama3'))
            load.assert_not_called()

    def test_unknown_upstream_refused(self):
        save_cache(self.audio)
        with patch.dict(os.environ, {'VOICE_REFERENCE_AUDIO':str(self.audio),'VOICE_REFERENCE_TEXT':'Original transcript.'}), \
             patch('her_voice.drama_voice.import_module', return_value=SimpleNamespace(__file__=__file__)):
            with self.assertRaisesRegex(RuntimeError,'unknown'): install_drama_voice(backend='drama3')

if __name__ == '__main__': unittest.main()
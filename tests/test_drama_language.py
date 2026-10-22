"""Execute the actual pinned language observer without importing model workers."""
import ast
import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from her_voice.drama_language import SOURCE_SHA256, lazy_language_observer, install_drama_language_path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.venv-speech/Lib/site-packages/speech_to_speech/LLM/lm_output_processor.py'


def observer(detection):
    source = SOURCE.read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(source).hexdigest() == SOURCE_SHA256
    tree = ast.parse(source.decode())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LMOutputProcessor')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_observe_assistant_language')
    ns = {'language_detection': detection, 'logger': logging.getLogger('language-test')}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'pinned-language-observer', 'exec'), ns)
    return ns['_observe_assistant_language']


def state(detector=None, language=None):
    return SimpleNamespace(_detected_assistant_language=language, _language_detector=detector,
                           _assistant_language_probe='')


class DramaLanguageTests(unittest.TestCase):
    def detection(self):
        return SimpleNamespace(__file__=str(SOURCE.parents[1] / 'pipeline/language_detection.py'), warm_language_detector=Mock(side_effect=AssertionError('eager all-language warmup blocks first TTS')),
                               build_language_detector=Mock(return_value=object()),
                               detect_language_from_text=Mock(return_value='zh'), MIN_ASSISTANT_CONFIDENCE_GAP=0.1)

    def test_first_sentence_reaches_detection_without_eager_warmup(self):
        detection = self.detection()
        original = observer(detection)
        with self.assertRaisesRegex(AssertionError, 'blocks first TTS'):
            original(state(), '先说重点。')
        detection.warm_language_detector.reset_mock()
        processor = state()
        lazy_language_observer(original, detection.build_language_detector, detection.warm_language_detector)(processor, '先说重点。')
        detection.warm_language_detector.assert_not_called()
        detection.build_language_detector.assert_called_once_with()
        self.assertEqual(processor._detected_assistant_language, 'zh')
        detection.detect_language_from_text.assert_called_once_with('先说重点。', processor._language_detector,
            minimum_confidence_gap=0.1, allow_short_cjk=True)

    def test_reuses_detector_across_undetected_fragments_and_response_reset(self):
        detection = self.detection()
        detection.detect_language_from_text.side_effect = ['en', 'zh']
        observe = lazy_language_observer(observer(detection), detection.build_language_detector, detection.warm_language_detector)
        processor = state()
        observe(processor, '你'); observe(processor, '这一段话需要继续检测。')
        self.assertEqual(processor._detected_assistant_language, 'en')
        processor._detected_assistant_language = None
        processor._assistant_language_probe = ''
        observe(processor, '下一轮。')
        detection.build_language_detector.assert_called_once()
        detection.warm_language_detector.assert_not_called()
        self.assertEqual(processor._detected_assistant_language, 'zh')

    def test_existing_detector_and_resolved_language_are_preserved(self):
        detection = self.detection()
        observe = lazy_language_observer(observer(detection), detection.build_language_detector, detection.warm_language_detector)
        existing = object()
        processor = state(existing)
        observe(processor, 'Text stays unchanged.')
        self.assertIs(processor._language_detector, existing)
        observe(state(language='en'), 'already resolved')
        detection.build_language_detector.assert_not_called()
        self.assertEqual(detection.detect_language_from_text.call_count, 1)

    def test_other_backends_import_nothing_and_unknown_source_is_refused(self):
        with patch('her_voice.drama_language.import_module') as load:
            self.assertFalse(install_drama_language_path(backend='qwen3'))
            load.assert_not_called()
        module = SimpleNamespace(__file__=__file__)
        with patch('her_voice.drama_language.import_module', return_value=module):
            with self.assertRaisesRegex(RuntimeError, 'unknown'):
                install_drama_language_path(backend='drama3')

    def test_english_keeps_original_preload_and_cjk_switch_upgrades_once(self):
        detection = self.detection()
        detection.warm_language_detector.side_effect = None
        detection.warm_language_detector.return_value = object()
        detection.detect_language_from_text.side_effect = ['en', 'zh', 'en']
        observe = lazy_language_observer(observer(detection), detection.build_language_detector,
                                         detection.warm_language_detector)
        english = state()
        observe(english, 'Today we can take this one step at a time.')
        detection.build_language_detector.assert_not_called()
        self.assertEqual(english._detected_assistant_language, 'en')
        processor = state()
        observe(processor, '今天我们慢慢聊。')
        processor._detected_assistant_language = None
        processor._assistant_language_probe = ''
        observe(processor, 'Today we can continue in English.')
        self.assertEqual(processor._detected_assistant_language, 'en')
        self.assertIs(processor._language_detector, detection.warm_language_detector.return_value)
        self.assertEqual(detection.warm_language_detector.call_count, 2)
        detection.build_language_detector.assert_called_once()
    def test_undetectable_short_latin_preserves_probe_without_loading(self):
        detection = self.detection()
        detection.detect_language_from_text.return_value = None
        processor = state()
        observe = lazy_language_observer(observer(detection), detection.build_language_detector,
                                         detection.warm_language_detector)
        observe(processor, 'OK.')
        self.assertEqual(processor._assistant_language_probe, 'OK.')
        self.assertIsNone(processor._detected_assistant_language)
        detection.warm_language_detector.assert_not_called()
        detection.build_language_detector.assert_not_called()
        detection.detect_language_from_text.assert_not_called()
        detection.warm_language_detector.side_effect = None
        detection.warm_language_detector.return_value = object()
        observe(processor, 'Let us continue with the full answer.')
        detection.warm_language_detector.assert_called_once()
        self.assertEqual(detection.detect_language_from_text.call_args.args[0],
                         'OK. Let us continue with the full answer.')
    def test_install_is_idempotent(self):
        detection = self.detection()
        cls = type('Processor', (), {'_observe_assistant_language': observer(detection)})
        module = SimpleNamespace(__file__=str(SOURCE), LMOutputProcessor=cls, language_detection=detection)
        with patch('her_voice.drama_language.import_module', return_value=module):
            self.assertTrue(install_drama_language_path(backend='drama3'))
            installed = cls._observe_assistant_language
            self.assertFalse(install_drama_language_path(backend='drama3'))
            self.assertIs(cls._observe_assistant_language, installed)


if __name__ == '__main__': unittest.main()

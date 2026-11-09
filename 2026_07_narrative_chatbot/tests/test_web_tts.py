"""Tests for web_tts.py: the Web UI's optional Style-Bert-VITS2 narration.

No real HTTP calls or TTS server; requests.Session is mocked throughout.
"""

import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import web_tts


class CacheKeyTests(unittest.TestCase):
    def test_same_text_and_config_gives_the_same_key(self):
        config = web_tts.TTSConfig(server_url="http://x")
        self.assertEqual(config.cache_key("やあ"), config.cache_key("やあ"))

    def test_different_text_gives_a_different_key(self):
        config = web_tts.TTSConfig(server_url="http://x")
        self.assertNotEqual(config.cache_key("やあ"), config.cache_key("またね"))

    def test_different_voice_settings_give_a_different_key(self):
        a = web_tts.TTSConfig(server_url="http://x", pitch_scale=1.0)
        b = web_tts.TTSConfig(server_url="http://x", pitch_scale=0.8)
        self.assertNotEqual(a.cache_key("やあ"), b.cache_key("やあ"))


def _fake_session(responses):
    session = unittest.mock.Mock()
    session.post.side_effect = responses
    session.__enter__ = unittest.mock.Mock(return_value=session)
    session.__exit__ = unittest.mock.Mock(return_value=False)
    return session


class SynthesizeTests(unittest.TestCase):
    def test_posts_g2p_then_synthesis_with_the_mora_tone_list(self):
        config = web_tts.TTSConfig(server_url="http://tts.example")
        g2p_response = unittest.mock.Mock(json=unittest.mock.Mock(return_value=[["a", 1]]))
        g2p_response.raise_for_status = unittest.mock.Mock()
        synth_response = unittest.mock.Mock(content=b"RIFF...")
        synth_response.raise_for_status = unittest.mock.Mock()
        session = _fake_session([g2p_response, synth_response])
        with unittest.mock.patch.object(web_tts.requests, "Session", return_value=session):
            wav = web_tts.synthesize(config, "やあ")
        self.assertEqual(wav, b"RIFF...")
        g2p_call, synth_call = session.post.call_args_list
        self.assertEqual(g2p_call.args[0], "http://tts.example/api/g2p")
        self.assertEqual(g2p_call.kwargs["json"], {"text": "やあ"})
        self.assertEqual(synth_call.args[0], "http://tts.example/api/synthesis")
        self.assertEqual(synth_call.kwargs["json"]["moraToneList"], [["a", 1]])
        self.assertEqual(synth_call.kwargs["json"]["text"], "やあ")

    def test_propagates_a_failed_request(self):
        config = web_tts.TTSConfig(server_url="http://tts.example")
        session = _fake_session([requests.ConnectionError("down")])
        with unittest.mock.patch.object(web_tts.requests, "Session", return_value=session):
            with self.assertRaises(requests.ConnectionError):
                web_tts.synthesize(config, "やあ")


class TTSCacheTests(unittest.TestCase):
    def test_caches_by_text_so_a_repeat_line_is_not_resynthesized(self):
        config = web_tts.TTSConfig(server_url="http://x")
        cache = web_tts.TTSCache(config)
        with unittest.mock.patch.object(web_tts, "synthesize", return_value=b"wav") as mock_synth:
            key1, wav1 = cache.synthesize("やあ")
            key2, wav2 = cache.synthesize("やあ")
        self.assertEqual(key1, key2)
        self.assertEqual(wav1, wav2)
        mock_synth.assert_called_once()

    def test_a_failure_is_not_cached_so_a_later_call_can_retry(self):
        config = web_tts.TTSConfig(server_url="http://x")
        cache = web_tts.TTSCache(config)
        with unittest.mock.patch.object(
            web_tts, "synthesize", side_effect=[requests.ConnectionError("down"), b"wav"]
        ):
            with self.assertRaises(requests.ConnectionError):
                cache.synthesize("やあ")
            key, wav = cache.synthesize("やあ")
        self.assertEqual(wav, b"wav")
        self.assertIsNone(cache.get("not-a-real-key"))
        self.assertEqual(cache.get(key), b"wav")

    def test_a_burst_of_the_same_line_only_synthesizes_once(self):
        """Many sessions starting at once must not each hit the TTS server."""
        config = web_tts.TTSConfig(server_url="http://x")
        cache = web_tts.TTSCache(config)
        call_count = 0
        call_count_lock = threading.Lock()

        def slow_synthesize(_config, _text):
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            time.sleep(0.05)
            return b"wav"

        results = []
        with unittest.mock.patch.object(web_tts, "synthesize", side_effect=slow_synthesize):
            threads = [
                threading.Thread(target=lambda: results.append(cache.synthesize("やあ")))
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(call_count, 1)
        self.assertEqual({wav for _key, wav in results}, {b"wav"})


if __name__ == "__main__":
    unittest.main()

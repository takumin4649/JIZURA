"""Offline unit tests for the optional Openverse audio helper."""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_FILE = Path(__file__).resolve().parents[1] / "tools" / "openverse_audio.py"
SPEC = importlib.util.spec_from_file_location("jizura_openverse_audio", MODULE_FILE)
audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio)


class FakeResponse(io.BytesIO):
    def __init__(self, data, url="https://example.org/sound.mp3", content_type="audio/mpeg"):
        super().__init__(data)
        self._url = url
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return self._url


class OpenverseAudioTests(unittest.TestCase):
    def test_https_public_url(self):
        self.assertTrue(audio.https_public_url("https://example.org/sound.wav"))
        self.assertFalse(audio.https_public_url("http://example.org/sound.wav"))
        self.assertFalse(audio.https_public_url("https://127.0.0.1/sound.wav"))
        self.assertFalse(audio.https_public_url("https://localhost/sound.wav"))
        self.assertFalse(audio.https_public_url("file:///etc/passwd"))

    def test_search_filters_noncommercial_and_insecure(self):
        records = [
            {"id": "ok", "title": "Click", "license": "by",
             "url": "https://example.org/click.mp3", "mature": False},
            {"id": "nc", "license": "by-nc", "url": "https://example.org/no.mp3"},
            {"id": "http", "license": "cc0", "url": "http://example.org/no.mp3"},
            {"id": "adult", "license": "cc0", "url": "https://example.org/no.mp3",
             "mature": True},
        ]
        payload = json.dumps({"results": records}).encode("utf-8")
        with patch.object(audio.urllib.request, "urlopen",
                          return_value=FakeResponse(payload)) as mocked:
            result = audio.search_audio("mouse click", "sound_effect", 10)
        self.assertEqual([item["id"] for item in result["items"]], ["ok"])
        url = mocked.call_args.args[0].full_url
        self.assertIn("license=by%2Ccc0", url)
        self.assertIn("license_type=commercial", url)

    def test_search_rejects_bad_response(self):
        with patch.object(audio.urllib.request, "urlopen",
                          return_value=FakeResponse(b"{}")):
            with self.assertRaises(ValueError):
                audio.search_audio("test")

    def test_download_keeps_rights_metadata(self):
        candidate = {"id": "sound-1", "title": "Click", "creator": "Alice",
                     "license": "by", "license_version": "4.0",
                     "license_url": "https://creativecommons.org/licenses/by/4.0/",
                     "audio_url": "https://example.org/click.mp3",
                     "source_page": "https://example.org/source"}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(audio.urllib.request, "urlopen",
                              return_value=FakeResponse(b"fake mp3 bytes")):
                info = audio.fetch_audio(candidate, tmp)
            self.assertEqual(info["bytes"], len(b"fake mp3 bytes"))
            self.assertTrue((Path(tmp) / "sound-1.mp3").is_file())
            self.assertIn("Alice", (Path(tmp) / "sound-1.attribution.txt").read_text())
            self.assertEqual(json.loads((Path(tmp) / "sound-1.json").read_text())["license"], "by")

    def test_download_blocks_bad_license_and_redirect(self):
        item = {"id": "no", "license": "by-nc",
                "audio_url": "https://example.org/sound.mp3"}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                audio.fetch_audio(item, tmp)
            item["license"] = "cc0"
            with patch.object(audio.urllib.request, "urlopen",
                              return_value=FakeResponse(b"audio", url="http://example.org/audio.mp3")):
                with self.assertRaises(ValueError):
                    audio.fetch_audio(item, tmp)


if __name__ == "__main__":
    unittest.main()

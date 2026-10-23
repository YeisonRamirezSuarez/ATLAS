"""Offline checks for web_search, apple_music and youtube_video: no network, no audio, no browser.

Run:  python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Logs use emoji; a cp1252 console (Windows, piped output) would raise UnicodeEncodeError mid-action.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

# youtube_video imports pyautogui at module level; it fails on headless Linux (no display).
try:
    import pyautogui  # noqa: F401
except Exception:
    sys.modules["pyautogui"] = mock.MagicMock()

from agent import llm
from agent.executor import _FAILURE
from actions import apple_music, web_search, youtube_video


def _fake_ddgs(text=(), news=()):
    """Patch for ddgs.DDGS returning canned text/news results."""
    ddgs = mock.MagicMock()
    ddgs.text.return_value = list(text)
    ddgs.news.return_value = list(news)
    cls = mock.MagicMock()
    cls.return_value.__enter__.return_value = ddgs
    return mock.patch("ddgs.DDGS", cls), ddgs


TEXT = [{"title": "Dataclasses docs", "href": "https://docs.python.org/3/library/dataclasses.html",
         "body": "Data classes module."}]
NEWS = [{"title": "BTC hits record", "url": "https://news.example.com/btc",
         "body": "Bitcoin rose.", "date": "2026-10-20T08:15:00+00:00"}]


class WebSearchTests(unittest.TestCase):
    def test_text_result_has_url_and_date_unknown(self):
        patch, _ = _fake_ddgs(text=TEXT)
        with patch:
            out = web_search.web_search({"query": "python dataclasses"})
        self.assertIn("1. Dataclasses docs — Data classes module.", out)
        self.assertIn("source: https://docs.python.org/3/library/dataclasses.html", out)
        self.assertIn("date unknown", out)
        self.assertIsNone(_FAILURE.search(out))

    def test_time_sensitive_query_uses_recent_news_and_keeps_date(self):
        patch, ddgs = _fake_ddgs(news=NEWS)
        with patch:
            out = web_search.web_search({"query": "bitcoin price today"})
        self.assertEqual(ddgs.news.call_args.kwargs["timelimit"], "w")
        ddgs.text.assert_not_called()
        self.assertIn("(source: https://news.example.com/btc, date: 2026-10-20)", out)

    def test_compare_llm_failure_falls_back_to_raw_sources(self):
        patch, _ = _fake_ddgs(text=TEXT)
        with patch, mock.patch.object(web_search, "complete", side_effect=llm.LLMError("no key")):
            out = web_search.web_search({"mode": "compare", "items": ["a", "b"], "query": "x"})
        self.assertIn("source: https://docs.python.org/3/library/dataclasses.html", out)
        self.assertIsNone(_FAILURE.search(out))

    def test_compare_summary_asks_for_citations(self):
        patch, _ = _fake_ddgs(text=TEXT)
        with patch, mock.patch.object(web_search, "complete", return_value="A is faster [1].") as c:
            out = web_search.web_search({"mode": "compare", "items": ["a", "b"], "query": "x"})
        self.assertEqual(out, "A is faster [1].")
        self.assertIn("source: https://", c.call_args.args[0])
        self.assertIn("source number", c.call_args.kwargs["system"])

    def test_no_results_or_error_is_a_failure(self):
        patch, _ = _fake_ddgs()
        with patch:
            out = web_search.web_search({"query": "zzqx nothing"})
        self.assertTrue(out.startswith("Could not find"), out)
        self.assertTrue(_FAILURE.search(out))
        patch, ddgs = _fake_ddgs()
        ddgs.news.side_effect = RuntimeError("rate limited")
        with patch:
            self.assertTrue(_FAILURE.search(web_search.web_search({"query": "zzqx"})))


class YouTubePlayTests(unittest.TestCase):
    def test_opened_video_is_not_claimed_playing(self):
        url = "https://www.youtube.com/watch?v=abcdefghijk"
        with mock.patch.object(youtube_video, "_scrape_first_video_url", return_value=url), \
             mock.patch.object(youtube_video.subprocess, "Popen") as popen:
            out = youtube_video.youtube_video({"action": "play", "query": "lofi"})
        popen.assert_called_once()
        self.assertIn(url, out)
        self.assertIn("playback not verified", out)
        self.assertNotIn("Playing", out)

    def test_browser_failure_and_search_fallback(self):
        with mock.patch.object(youtube_video, "_scrape_first_video_url", return_value=None), \
             mock.patch.object(youtube_video.subprocess, "Popen"):
            out = youtube_video.youtube_video({"action": "play", "query": "lofi"})
        self.assertIn("nothing is playing", out)
        with mock.patch.object(youtube_video, "_scrape_first_video_url", return_value=None), \
             mock.patch.object(youtube_video.subprocess, "Popen", side_effect=OSError("no browser")):
            out = youtube_video.youtube_video({"action": "play", "query": "lofi"})
        self.assertTrue(out.startswith("Could not"), out)


class AppleMusicTests(unittest.TestCase):
    SONG = {"trackName": "Bichota", "artistName": "Karol G",
            "trackViewUrl": "https://music.apple.com/co/album/x", "trackCount": 1}

    def _run(self, label, params):
        """Run apple_music against a fake window whose play/pause button shows `label`."""
        w = mock.MagicMock()
        row = mock.MagicMock()
        row.window_text.return_value = "1 Bichota 2:58"
        w.descendants.return_value = [row]
        els = {"LCD": mock.MagicMock(), "TransportControl_PlayPauseStop": mock.MagicMock()}
        els["LCD"].window_text.return_value = "Bichota - Karol G"
        els["TransportControl_PlayPauseStop"].window_text.return_value = label
        with mock.patch.object(apple_music, "_find_song", return_value=self.SONG), \
             mock.patch.object(apple_music.os, "startfile", create=True) as start, \
             mock.patch.object(apple_music, "_window", return_value=w), \
             mock.patch.object(apple_music, "_el", side_effect=lambda _w, i: els.setdefault(i, mock.MagicMock())), \
             mock.patch.object(apple_music, "_wait", side_effect=lambda fn, timeout=0: fn() or None):
            return apple_music.apple_music(params), start

    def test_play_is_claimed_only_when_button_shows_pause(self):
        out, start = self._run("Pausar", {"query": "Bichota"})
        start.assert_called_once()
        self.assertTrue(out.startswith("Playing 'Bichota'"), out)
        out, _ = self._run("Reproducir", {"query": "Bichota"})
        self.assertIn("playback not verified", out)
        self.assertNotIn("Playing", out)

    def test_transport_reports_verified_state(self):
        out, _ = self._run("Pause", {"action": "resume"})
        self.assertIn("already playing", out)
        out, _ = self._run("Reproducir", {"action": "resume"})
        self.assertIn("not verified", out)

    def test_media_key_fallback_is_unverified(self):
        fake = mock.MagicMock()
        fake.play_pause.return_value = "Sent the play/pause media key."
        with mock.patch.object(apple_music, "_window", side_effect=StopIteration), \
             mock.patch.dict(sys.modules, {"actions.computer_settings": fake}):
            out = apple_music.apple_music({"action": "pause"})
        fake.play_pause.assert_called_once()
        self.assertIn("not verified", out)

    def test_late_transport_bar_is_awaited(self):
        """The LCD shows up only after playback starts: _el must wait for it, not fail at once."""
        lcd = mock.MagicMock()
        lcd.element_info.automation_id = "LCD"
        w = mock.MagicMock()
        w.descendants.side_effect = [[], [lcd]]
        self.assertIs(apple_music._el(w, "LCD", timeout=3), lcd)
        w.descendants.side_effect = None
        w.descendants.return_value = []
        with self.assertRaises(LookupError):
            apple_music._el(w, "LCD", timeout=0.1)

    def test_missing_transport_after_play_is_not_a_failure(self):
        def el(_w, i):
            if i == "LCD":
                raise LookupError("Apple Music control LCD not found")
            return mock.MagicMock()
        w = mock.MagicMock()
        row = mock.MagicMock()
        row.window_text.return_value = "1 Bichota 2:58"
        w.descendants.return_value = [row]
        with mock.patch.object(apple_music, "_find_song", return_value=self.SONG),              mock.patch.object(apple_music.os, "startfile", create=True),              mock.patch.object(apple_music, "_window", return_value=w),              mock.patch.object(apple_music, "_el", side_effect=el),              mock.patch.object(apple_music, "_wait", side_effect=lambda fn, timeout=0: fn() or None):
            out = apple_music.apple_music({"query": "Bichota"})
        self.assertFalse(_FAILURE.search(out), out)
        self.assertIn("not verified", out)

    def test_failures_match_failure_regex(self):
        with mock.patch.object(apple_music, "_find_song", return_value=None), \
             mock.patch.object(apple_music.os, "startfile", create=True):
            self.assertTrue(_FAILURE.search(apple_music.apple_music({"query": "zzqx"})))
        self.assertTrue(_FAILURE.search(apple_music.apple_music({"action": "shuffle"})))


if __name__ == "__main__":
    unittest.main()

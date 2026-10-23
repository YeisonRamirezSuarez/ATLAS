#apple_music.py
# Plays songs in the Apple Music desktop app (Windows) through UI Automation.
# UIA invoke() is used instead of clicks: Windows will not bring the app to the
# foreground on demand, so mouse input would land on whatever window is in front.
import os
import time

import psutil
import requests

PROCESS = "AppleMusic.exe"
TRANSPORT = {
    "pause":    "TransportControl_PlayPauseStop",
    "resume":   "TransportControl_PlayPauseStop",
    "next":     "TransportControl_SkipForward",
    "previous": "TransportControl_SkipBack",
}
PLAYING = ("Pausar", "Pause")  # the play/pause button shows "Pause" only while playing


def _wait(fn, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = fn()
            if r:
                return r
        except Exception:
            pass
        time.sleep(0.5)
    return None


def _window():
    from pywinauto import Desktop
    pid = next(p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == PROCESS)
    return next((x for x in Desktop(backend="uia").windows(process=pid) if x.window_text() == "Apple Music"), None)


def _el(w, auto_id, timeout=8.0):
    """Waits for a control: the transport bar (LCD, skip, play/pause) appears only once playback starts."""
    e = _wait(lambda: next((e for e in w.descendants() if e.element_info.automation_id == auto_id), None), timeout)
    if e is None:
        raise LookupError(f"Apple Music control {auto_id} not found")
    return e


def _playing(play) -> bool:
    return play.window_text() in PLAYING


def _find_song(title: str, artist: str = "") -> dict | None:
    r = requests.get(
        "https://itunes.apple.com/search",
        params={"term": f"{title} {artist}".strip(), "entity": "song", "country": "co", "limit": 10},
        timeout=10,
    )
    results = r.json().get("results", [])
    # Search ranks by popularity, so "Bichota Karol G" can return another Karol G hit first.
    return next((r for r in results if title.lower() in r["trackName"].lower()), results[0] if results else None)


def _play(query: str, artist: str = "") -> str:
    if not hasattr(os, "startfile"):
        return "Could not play songs by name: Apple Music automation only works on Windows."
    song = _find_song(query, artist)
    if not song:
        return f"Could not find '{query}' in Apple Music."
    name, artist = song["trackName"], song["artistName"]

    # The track URL opens the album page. Track rows ignore UIA invoke and a mouse
    # click would hit whatever window is in front, so: play the album, skip to the song.
    url = song["trackViewUrl"].replace("https://", "music://", 1)
    for _ in range(2):  # the deep link is sometimes ignored right after launch; retry once
        os.startfile(url)
        w = _wait(_window, 20)
        if w and _wait(lambda: f" {name} " in " ".join(
                i.window_text() for i in w.descendants(control_type="ListItem"))):
            break
    else:
        return f"Could not open '{name}' in Apple Music."

    _el(w, "PlayButtonElement").invoke()
    try:
        lcd, skip = _el(w, "LCD"), _el(w, "TransportControl_SkipForward")
    except LookupError:   # the album was started: not a failure, but the song is unchecked
        return f"Started the album with '{name}' by {artist} in Apple Music; which song plays is not verified."
    current = _wait(lcd.window_text, 8)
    for _ in range(song.get("trackCount", 30) * song.get("discCount", 1)):
        if current and current.startswith(name):
            break
        skip.invoke()
        current = _wait(lambda: (t := lcd.window_text()) != current and t, 4)
    else:
        return f"Could not reach '{name}': its album opened in Apple Music but skipping never landed on it."

    play = _el(w, "TransportControl_PlayPauseStop")
    if _wait(lambda: _playing(play), 2) is None:
        play.invoke()
    if _wait(lambda: _playing(play), 3):
        return f"Playing '{name}' by {artist} in Apple Music."
    return f"Loaded '{name}' by {artist} in Apple Music; playback not verified."


def apple_music(parameters: dict, player=None, **_) -> str:
    params = parameters or {}
    action = (params.get("action") or "play").lower().strip()
    query = params.get("query", "").strip()
    artist = params.get("artist", "").strip()

    if player:
        player.write_log(f"[Music] {action} {query}".strip())
    if action not in TRANSPORT and action != "play":
        return f"Could not do '{action}': use play, pause, resume, next or previous."
    try:
        if action == "play" and query:
            return _play(query, artist)
        w = _window()
        if not w:
            raise StopIteration
        play = _el(w, "TransportControl_PlayPauseStop")
        was_playing = _playing(play)
        if (action == "pause" and not was_playing) or (action in ("resume", "play") and was_playing):
            return f"Apple Music was already {'playing' if was_playing else 'paused'}."
        _el(w, TRANSPORT.get(action, "TransportControl_PlayPauseStop")).invoke()
        if was_playing and action in ("next", "previous"):  # skipping leaves the player paused
            if _wait(lambda: _playing(play), 2) is None:
                play.invoke()
        want = action in ("resume", "play") or (action != "pause" and was_playing)
        state = "playing" if want else "paused"
        if _wait(lambda: _playing(play) == want, 3):
            return f"Sent {action} to Apple Music; it is now {state}."
        return f"Sent {action} to Apple Music; {state} state not verified."
    except (StopIteration, LookupError, ImportError):
        if action == "play" and query:
            return "Could not find Apple Music's player controls."
        # Another player (Spotify, YouTube...) may own the media session: use the media keys.
        from actions.computer_settings import play_pause, next_track, prev_track
        key = {"next": next_track, "previous": prev_track}.get(action, play_pause)
        return f"Apple Music window not found. {key()} Effect not verified."
    except Exception as e:
        return f"Failed to control Apple Music: {e}"


if __name__ == "__main__":
    import sys
    print(apple_music({"query": " ".join(sys.argv[1:]) or "Hips Don't Lie Shakira"}))

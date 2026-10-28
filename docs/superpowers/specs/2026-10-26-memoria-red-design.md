# MEMORIA: a 3D network of everything ATLAS knows about the user

Date: 2026-10-26 · Status: approved in conversation, design written for planning

## Intent

The user wants to see, privately, everything ATLAS has learned about them and how that knowledge grew
over time, drawn as a futuristic neural network, and to fix or delete any fact on the spot. It lives
in Settings behind a PIN "in case someone else uses my PC". It should feel like the rest of the new
ATLAS look: celeste on black, Orbitron/Chakra Petch, smooth Qt Quick motion.

Success: the user opens it, recognises themselves in the network, plays the timeline and watches it
grow, finds a fact with the search box, and corrects or deletes it, and ATLAS really stops using a
deleted fact.

## Goals

- A new **MEMORIA** tab in the Settings overlay (`ui.SettingsOverlay`, next to GENERAL, AI ENGINE,
  ACCOUNT). It asks for the PIN, then offers "Abrir red", which opens the network **full window** over
  ATLAS (the settings panel is 520 px wide, too small for the sphere).
- **The network (option B, chosen by the user from three mockups):** a 3D sphere the user rotates with
  the mouse and zooms with the wheel; nodes nearer the viewer brighter and larger. "TÚ" at the centre.
  Each fact is a node coloured by its category (identity, preferences, projects, relationships,
  wishes, notes). Conversations are small grey nodes linked to the facts learned from them. Facts that
  relate across categories get a dashed link.
- **Search box** at the top: typing filters/highlights; picking a result turns the sphere to that node
  and selects it.
- **Side panel** for the selected fact: category, value, when it was learned (date and time), the
  conversation it came from, linked facts, and two buttons: **Corregir** (edit the value in place) and
  **Borrar** (asks to confirm).
- **Timeline** at the bottom with ▶: replays the network growing from the first memory to now; a
  draggable handle shows the network as it was on a date, with the date and the fact count.
- **PIN:** set the first time the tab is opened; stored only as a salted hash; asked every time the
  tab is opened; the view locks again on close and after a few minutes without input.

## Non-goals

- Encrypting the memory files on disk. The PIN guards the view inside ATLAS only; anyone who opens the
  folder can still read `memory/long_term.json`. The UI must say so in one line where the PIN is set.
- Showing raw conversation transcripts in full; the panel shows a short excerpt.
- A web page or any new runtime: it is Qt Quick inside the existing PyQt6 app.

## Anti-goals

- No new dependencies. Same stack as `qml/hud.qml`: QtQuick, QtQuick.Shapes, Python bridge objects.
- It must not slow the main HUD: the network renders only while open.
- Never invent facts or dates for the timeline. Where a date is unknown, say so.
- "Borrar" must not leave the fact behind anywhere ATLAS reads it back from (see Data below).

## Data: what exists today (verified 2026-10-26)

- `memory/long_term.json`: facts as `{category: {key: {"value", "updated"}}}`, six categories, date
  only (`YYYY-MM-DD`), capped at `MEMORY_MAX_CHARS = 2200`; oldest facts are trimmed when full.
- **Engram** (`bin/engram.exe`, v3.5.2, project `mark-xxxix`): every fact change is also saved as an
  observation (`type` = category, `title` = `category/key`, content `key: value`), and every exchange
  as `type: conversation`, each with `created_at` to the second. `engram export <file> --project
  mark-xxxix` gives all of it as JSON. Engram keeps facts trimmed from `long_term.json`.
  On this PC Engram was missing until today, so history starts on 2026-10-26.
- **Edit/delete paths already in code:** `memory_manager.update_memory()` (writes the fact, saves an
  Engram observation, queues ATLAS ID sync) and `memory_manager.forget(key, category)` (removes it,
  queues a sync delete). `forget` does **not** touch Engram today.
- **ATLAS ID sync** (`memory/sync.py`) shares facts across the user's devices; a delete queued here
  is applied on the other devices.

## Decisions made (by the user)

- Option B, the 3D sphere, over a radial constellation or a time spiral.
- Purpose: see how the knowledge grew and what ATLAS knows about them.
- PIN-protected, against other people using the same PC.
- Fix or delete facts right in the view.

## Calls made by Claude (the user may revisit)

- Full-window view rather than inside the settings panel.
- The search box, to offset the sphere being hard to search.
- Delete propagates through ATLAS ID sync to the user's other devices (the memory already syncs).
- **Delete also hard-deletes the fact's Engram observations** (`title == category/key`), else ATLAS
  could recall it through `search_memory`. Conversations that mention it are kept (see open question).
- Facts trimmed from `long_term.json` but still in Engram are shown dimmed as "archived", since ATLAS
  can still recall them; they can be deleted the same way.
- "From which conversation" is inferred: the conversation observation saved just before the fact's
  first observation (Engram stores no explicit link). Shown as "probably from…".
- Cross-category dashed links: facts whose values share a meaningful word (simple, local; no LLM).
- Auto-lock after 5 minutes without input; PIN of at least 4 digits; three wrong tries add a short
  delay. Forgotten PIN: reset by signing in again with ATLAS ID if signed in, else by deleting the
  `memory_pin` entry from `config/api_keys.json` (documented, since this is a privacy screen, not a
  vault).

## Left to the builder

- Node layout on the sphere (e.g. categories as regions, Fibonacci points within), rotation and zoom
  feel, label density, how the timeline animates nodes in.
- Bridge shape between Python and QML (likely one QObject exposing the graph as a list of maps plus
  slots for search, edit, delete), loading Engram off the UI thread.
- PIN hashing details (stdlib `hashlib.pbkdf2_hmac` with a random salt is enough).
- Tests: graph building from a fake export and memory file, edit/delete reaching `long_term.json`,
  Engram and the sync queue, PIN check, and the QML loading under the existing offscreen test pattern
  (`tests/test_settings_ui.py`).
- Light theme: same tokens as the HUD (`_qml_theme()`), dark navy behind the sphere like the core.

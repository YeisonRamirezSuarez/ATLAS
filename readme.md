# 🤖 MARK XXXIX-OR (39)
### The Ultimate Cross-Platform Personal AI Assistant — By FatihMakes

> 📺 **[Watch the full setup video on YouTube](https://youtu.be/ldvDNzwnM8k)**

A real-time voice AI that can hear, see, understand, and control your computer — on any OS. Supporting Windows, macOS, and Linux. Local execution. Zero subscriptions. Engineered for total autonomy.

---

## ✨ Overview

MARK XXXIX-OR represents the pinnacle of the Jarvis series, evolving into a more flexible and robust system. It bridges the gap between the operating system and human intent. Through natural dialogue, Mark 39 analyzes your screen, processes uploaded documents, and executes complex workflows with a brand-new, adaptive interface.

It's not just an assistant — it's an extension of your digital life.

---

## 🚀 Capabilities

### Core Features
| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Ultra-low latency conversation in any language |
| 🖥️ System Control | Launch apps, manage files, execute terminal commands |
| 🧩 Autonomous Tasks | High-level planning for complex, multi-step goals |
| 👁️ Visual Awareness | Real-time screen processing and webcam vision |
| 🧠 Persistent Memory | Deeply remembers your projects, preferences, and personal context |
| ⌨️ Hybrid Input | Seamlessly switch between keyboard typing and voice commands |

---

## 🆕 What's New in XXXIX-OR

- 📂 **Advanced File Handling** — New support for direct file uploads. Drop PDFs, source code, or images into the assistant to have them analyzed, summarized, or edited instantly.
- 🎨 **Adaptive & Flexible UI** — A complete overhaul of the interface. The new UI is fully resizable and responsive, featuring transparency controls and customizable layouts to fit your workspace perfectly.
- 🐧🍎 **Refined Cross-Platform Stability** — Major fixes for macOS and Linux compatibility. Core system actions are now more consistent across all three major operating systems.
- ⚡ **Optimized Core Engine** — Significant performance boost in tool-calling logic and response generation, resulting in a 40% faster interaction speed.
- 🔀 **OpenRouter Integration** — Selected action modules (web search, memory, flight finder, desktop control, and more) now route their LLM calls through OpenRouter's free-tier models. This significantly increases the effective request limit without any additional cost, while Gemini Live continues to handle real-time voice and tool-calling.

---

## ⚡ Quick Start

```bash
git clone https://github.com/FatihMakes/Mark-XXXIX-OR.git
cd Mark-XXXIX-OR
pip install -r requirements.txt
playwright install
python main.py
```

> ⚠️ **Installation Note:** To keep the repository lightweight, some OS-specific dependencies are not bundled in `requirements.txt`. If you run into a `ModuleNotFoundError`, simply install the missing package via `pip install <module_name>` for your specific system.

### 📦 Installers

`python installer/build.py` (run from a uv venv with Python 3.11) builds a self-contained installer for the current OS: Python, packages and Chromium included.

| OS | Output | Install |
|---|---|---|
| Windows | `dist/Jarvis-Setup.exe` (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)) | Run it; no admin needed |
| macOS | `dist/Jarvis.dmg` | Drag J.A.R.V.I.S to Applications |
| Linux | `dist/Jarvis-linux.tar.gz` | Extract, run `./install.sh` |

The **Build installers** GitHub Action (`.github/workflows/build.yml`) builds all three. macOS and Linux installers can only be built on those systems.

### 🧠 Engines & API keys

The ⚙ button in the header opens the settings panel at any time. Pick the engine (Gemini Live, Claude, OpenAI, OpenRouter, Groq, DeepSeek or a local Ollama), its model and keys; JARVIS restarts with them. The left panel shows the active model and the tokens used this session.

Gemini Live is native voice. The other engines use speech-to-text (OpenAI `gpt-4o-mini-transcribe` when an `openai_api_key` is saved, otherwise free Google STT, language `stt_language`, default `es-ES`) and Edge TTS (`tts_voice`, default `es-CO-GonzaloNeural`). Some tools (vision, web search, code helpers, memory) still use the Gemini and OpenRouter keys.

### 📧 Email

JARVIS lists, searches, reads and drafts email, replies, and opens your webmail. It sends only after you say yes to the exact recipient, subject and text. Add accounts to `config/api_keys.json`:

```json
"email_accounts": [
  {"name": "work", "type": "graph", "client_id": "<Entra ID app id>", "tenant": "organizations"},
  {"name": "personal", "type": "imap", "address": "me@gmail.com", "password": "<app password>"}
]
```

`graph` is Microsoft 365 / Outlook.com (use `"tenant": "consumers"` for personal Outlook.com). Register a public-client app in Entra ID with delegated `Mail.ReadWrite` and `Mail.Send`; the first use shows a code for microsoft.com/devicelogin. `imap` covers Gmail, Yahoo, iCloud, or any server with `imap`/`smtp`/`smtp_port`. Gmail needs an app password. Details: `actions/email_client.py`.

---

## 📋 Requirements

| Requirement | Details |
|---|---|
| **OS** | Windows 10/11, macOS, or Linux |
| **Python** | 3.11 or 3.12 |
| **Microphone** | Required for voice interaction |
| **API Keys** | Free Gemini API key + Free OpenRouter API key |

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

---

## 👤 Connect with the Creator

Engineered by a developer building a real-world JARVIS-style assistant.
⭐ **Star the repository to support the journey to Mark 100.**

| Platform | Link |
|---|---|
| YouTube | [@FatihMakes](https://www.youtube.com/@FatihMakes) |
| Instagram | [@fatihmakes](https://www.instagram.com/fatihmakes) |

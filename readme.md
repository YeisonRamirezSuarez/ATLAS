# 🤖 J.A.R.V.I.S

### Asistente personal de voz con IA para Windows, macOS y Linux

[![Descargar](https://img.shields.io/github/v/release/pablo29855/Jarvis-?label=descargar&style=for-the-badge)](https://github.com/pablo29855/Jarvis-/releases/latest)
[![Tests](https://img.shields.io/github/actions/workflow/status/pablo29855/Jarvis-/tests.yml?label=tests&style=for-the-badge)](https://github.com/pablo29855/Jarvis-/actions/workflows/tests.yml)
[![Licencia](https://img.shields.io/badge/licencia-CC%20BY--NC%204.0-lightgrey?style=for-the-badge)](https://creativecommons.org/licenses/by-nc/4.0/)

JARVIS escucha, ve y controla tu computador. Le hablas o le escribes, y él abre apps, busca en la web, lee tus correos, maneja archivos, escribe y ejecuta código, y hace tareas de varios pasos en segundo plano mientras te cuenta lo que está haciendo.

> Basado en **[MARK XXXIX-OR](https://github.com/FatihMakes/Mark-XXXIX-OR)** de **[FatihMakes](https://www.youtube.com/@FatihMakes)**. Ver [Créditos](#-créditos).

---

## 📥 Descargar

Instaladores listos para usar: traen Python, todas las librerías, Chromium y el modelo de la palabra de activación. No hay que instalar nada más.

| Sistema | Descarga | Cómo instalar |
|---|---|---|
| 🪟 **Windows 10/11** | [**Jarvis-Setup.exe**](https://github.com/pablo29855/Jarvis-/releases/latest/download/Jarvis-Setup.exe) | Ejecútalo. No necesita permisos de administrador. |
| 🍎 **macOS** | [**Jarvis.dmg**](https://github.com/pablo29855/Jarvis-/releases/latest/download/Jarvis.dmg) | Abre el `.dmg` y arrastra JARVIS a Aplicaciones. |
| 🐧 **Linux** | [**Jarvis-linux.tar.gz**](https://github.com/pablo29855/Jarvis-/releases/latest/download/Jarvis-linux.tar.gz) | Descomprime y ejecuta `./install.sh`. |

Todas las versiones: [Releases](https://github.com/pablo29855/Jarvis-/releases).

> **Primera vez:** los instaladores no están firmados.
> - **Windows:** si aparece SmartScreen, pulsa *Más información* → *Ejecutar de todas formas*.
> - **macOS:** clic derecho sobre JARVIS → *Abrir* → *Abrir*.

Al abrir JARVIS por primera vez, pulsa ⚙ y pon tus claves de API (ver [Motores y claves](#-motores-y-claves-de-api)).

---

## ✨ Qué hace

| | Función | Detalle |
|---|---|---|
| 🎙️ | **Voz en tiempo real** | Conversación natural en cualquier idioma. Responde siempre en el idioma en que le hablas. |
| 👂 | **Palabra de activación local** | Di "Jarvis" para despertarlo. Funciona sin internet con [Vosk](https://alphacephei.com/vosk/); el audio no sale de tu equipo mientras duerme. |
| 🧠 | **Varios cerebros** | Gemini Live, Claude (API o suscripción), OpenAI, OpenRouter, Groq, DeepSeek u Ollama local. |
| 🖥️ | **Control del sistema** | Abre apps, maneja archivos, volumen, brillo, ventanas, teclado y ratón. |
| 🧩 | **Tareas autónomas** | Planea y ejecuta tareas de varios pasos en segundo plano. El HUD muestra cada tarea y en qué paso va. |
| 🛠️ | **Aprende habilidades** | Si no tiene una herramienta, escribe un script, lo prueba y lo guarda para la próxima vez. Pide confirmación antes de cualquier acción riesgosa. |
| 👁️ | **Visión** | Analiza tu pantalla y la cámara. |
| 📧 | **Correo** | Lee, busca, redacta y responde (Outlook / Microsoft 365, Gmail, IMAP). Solo envía después de tu "sí". |
| 💬 | **Mensajes y música** | Telegram, WhatsApp, YouTube y Apple Music. |
| 📂 | **Archivos** | Arrastra PDFs, código, Word, Excel o imágenes para resumirlos, analizarlos o editarlos. |
| 💾 | **Memoria** | Recuerda tus preferencias y proyectos entre conversaciones. |
| ⌨️ | **Voz o texto** | Escríbele cuando quieras, incluso mientras habla: responde al terminar. |

---

## 🧠 Motores y claves de API

El botón ⚙ abre el panel de ajustes. Elige el motor, el modelo y pon sus claves; JARVIS se reinicia con ellos. El panel izquierdo muestra el modelo activo y los tokens usados.

| Motor | Qué necesitas |
|---|---|
| **Gemini Live** (por defecto) | Clave gratis de [Google AI Studio](https://aistudio.google.com/apikey). Voz nativa. |
| **Claude (suscripción)** | Tu cuenta de Claude Pro/Max; inicia sesión desde ajustes. |
| **Claude, OpenAI, Groq, DeepSeek, OpenRouter** | La clave de API de cada servicio. |
| **Ollama** | [Ollama](https://ollama.com) corriendo en tu equipo. Sin claves. |

Los motores que no son Gemini Live usan reconocimiento de voz (OpenAI `gpt-4o-mini-transcribe` si hay `openai_api_key`, si no Google STT gratis) y voz de Gemini o Edge TTS. Algunas herramientas (visión, búsqueda web, memoria) usan las claves de Gemini y OpenRouter.

Las claves se guardan solo en tu equipo, en `config/api_keys.json`.

<details>
<summary><b>📧 Configurar correo</b></summary>

Añade tus cuentas a `config/api_keys.json`:

```json
"email_accounts": [
  {"name": "trabajo", "type": "graph", "client_id": "<id de la app en Entra ID>", "tenant": "organizations"},
  {"name": "personal", "type": "imap", "address": "yo@gmail.com", "password": "<contraseña de aplicación>"}
]
```

- `graph`: Microsoft 365 / Outlook.com (usa `"tenant": "consumers"` para Outlook.com personal). Registra una app de cliente público en Entra ID con permisos delegados `Mail.ReadWrite` y `Mail.Send`. La primera vez muestra un código para microsoft.com/devicelogin.
- `imap`: Gmail, Yahoo, iCloud o cualquier servidor con `imap`/`smtp`/`smtp_port`. Gmail necesita una contraseña de aplicación.

Detalles en `actions/email_client.py`.
</details>

<details>
<summary><b>👂 Palabra de activación</b></summary>

En `config/api_keys.json`:

```json
"wake_word": "auto"
```

- `auto`: activa si Vosk y el modelo están instalados (los instaladores los traen).
- `true`: siempre activa.
- `false`: siempre escuchando, sin palabra de activación.

El botón **MICROPHONE** de la ventana silencia el micrófono; también puedes pedírselo a JARVIS.
</details>

---

## 👩‍💻 Ejecutar desde el código

Requiere Python 3.11 y se recomienda [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/pablo29855/Jarvis-.git
cd Jarvis-
uv venv -p 3.11 .venv
uv pip install -p .venv -r requirements.txt
.venv/bin/python -m playwright install chromium   # Windows: .venv\Scripts\python.exe
.venv/bin/python main.py
```

Tests (sin red, sin micrófono, sin claves):

```bash
python -m unittest discover tests
```

### 📦 Construir los instaladores

```bash
.venv/bin/python installer/build.py
```

Construye el instalador del sistema donde lo ejecutas, en `dist/`. En Windows necesita [Inno Setup 6](https://jrsoftware.org/isinfo.php).

**Publicar una versión:** sube un tag `vX.Y.Z` (por ejemplo `git tag v2.1.0 && git push origin v2.1.0`). La GitHub Action [Build installers](.github/workflows/build.yml) construye los tres instaladores y los publica en Releases. La versión se toma de `config/__init__.py`.

### 🗂️ Estructura

| Carpeta | Contenido |
|---|---|
| `main.py`, `engines.py` | Bucle de voz, motores y herramientas |
| `ui.py` | Interfaz y HUD |
| `agent/` | Planificador, ejecutor y cola de tareas en segundo plano |
| `actions/` | Herramientas: apps, archivos, web, correo, mensajes, música… |
| `memory/` | Memoria y habilidades aprendidas (`builtin_skills/` viene incluido) |
| `wakeword.py` | Palabra de activación local |
| `installer/` | Construcción de instaladores |
| `tests/` | Tests |

---

## 📋 Requisitos

| | |
|---|---|
| **Sistema** | Windows 10/11, macOS o Linux (x64) |
| **Micrófono** | Para hablarle (por texto funciona sin él) |
| **Claves** | Al menos una: Gemini (gratis) o el motor que elijas |
| **Espacio** | ~2 GB instalado |

---

## 🙏 Créditos

Este proyecto parte de **[MARK XXXIX-OR](https://github.com/FatihMakes/Mark-XXXIX-OR)**, creado por **FatihMakes**: la base de JARVIS, su interfaz, el bucle de voz con Gemini Live y las herramientas originales son su trabajo. El historial de commits original se conserva en este repositorio.

Sigue al creador original:

| Plataforma | Enlace |
|---|---|
| YouTube | [@FatihMakes](https://www.youtube.com/@FatihMakes) |
| Instagram | [@fatihmakes](https://www.instagram.com/fatihmakes) |
| Video de la versión original | [Ver en YouTube](https://youtu.be/ldvDNzwnM8k) |

Mejoras de esta versión por **[Pablo Romero](https://github.com/pablo29855)**: cerebros Claude y multi-motor, palabra de activación local, HUD de tareas en segundo plano, habilidades aprendidas, correo, instaladores para cada sistema y actualizador.

---

## ⚠️ Licencia

Uso personal y no comercial. Igual que el proyecto original, se distribuye bajo **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**: puedes compartirlo y modificarlo dando crédito, pero no usarlo con fines comerciales.

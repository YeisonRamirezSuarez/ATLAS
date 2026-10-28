# Auditoría previa: ATLAS ID y memoria compartida

> Auditoría histórica, previa a ATLAS ID (v2.3.1). Describe la app antes de los cambios de la versión 2.4.0.

Fecha: 2026-10-26 · Rama `main` · Commit `8f51efd` (v2.3.1) · Árbol limpio salvo `memory/ack-es.pcm` sin trackear.

Leyenda: **[V]** verificada · **[PV]** parcialmente verificada · **[P]** pendiente de comprobación.

No se ha modificado ningún archivo de código durante esta fase.

## 1. Arquitectura actual

- **[V]** App de escritorio en Python 3.11 + PyQt6. Punto de entrada `main.py` (clase `AtlasLive`), lanzado por `atlas.pyw` / `launcher.cs`. UI en `ui.py` (`MainWindow`, `SettingsOverlay`, `LogWidget`).
- **[V]** Motores: Gemini Live (voz nativa, `main.py`) y en cascada STT → LLM → TTS para el resto (`engines.py`: `_Claude`, `_ClaudeSub`, `_OpenAICompat`).
- **[V]** Todas las llamadas de texto auxiliares (planificador, ejecutor, memoria, resúmenes, búsqueda web) pasan por `agent/llm.py: complete()`, que usa el proveedor elegido por el usuario. `or_client.client.chat` es un alias de `complete()`.
- **[V]** Configuración: un único JSON local `config/api_keys.json` (git-ignored), leído por `config/__init__.py: get_config()` y escrito por `ui.py: _on_setup_done`. No hay cifrado ni almacén de secretos del sistema.
- **[V]** Ajustes: `SettingsOverlay` (botón CONFIG). Es un único panel vertical sin pestañas; al guardar reinicia la app.
- **[V]** Tests: 134 tests `unittest`, todos pasan en local (`Ran 134 tests … OK`). CI en Windows/macOS/Linux (`.github/workflows/tests.yml`).
- **[V]** Instalador (`installer/build.py`, `atlas.iss`) excluye claves, memoria y skills; la desinstalación conserva los datos salvo que el usuario marque la casilla. `updater.py backup` comprime claves + memoria en `backups/`.
- **[V]** No existe ninguna autenticación de usuario propia. Las únicas autenticaciones son de terceros: login de Claude Code (`claude auth login`) para el motor de suscripción, MSAL para correo Microsoft y Telethon para Telegram.
- **[V]** No hay dependencia de Supabase ni de ningún backend remoto propio.

## 2. Memoria actual

Hay **dos capas**, no duplicadas sino complementarias:

| Capa | Dónde | Qué guarda | Persistencia |
|---|---|---|---|
| `long_term.json` | `memory/long_term.json` | Hechos por categoría (`identity`, `preferences`, `projects`, `relationships`, `wishes`, `notes`) + `style` + `trusted` | Local, escritura atómica, recorte a 2200 caracteres |
| Engram | `~/.engram/engram.db` (SQLite) | Cada hecho guardado, cada turno de conversación (`type=conversation`) y cada skill aprendida (`type=skill`), proyecto `mark-xxxix` | Local, sin límite |

Además, `memory/skills/*.py` guarda el código de las skills aprendidas (Engram solo las indexa).

- **[V]** Engram v3.5.2 instalado en `bin/engram.exe` (traído por el instalador, versión fijada en `build.py`). Hay 3.6.0 disponible. `engram doctor` → 10/10 ok. 242 observaciones, 1 sesión, proyecto `mark-xxxix`. Existe un proyecto vacío `atlas-scratch-test`.
- **[V]** Integración por CLI (`memory_manager._engram`), no por HTTP ni MCP: `save`, `search`, `context`.
- **[V]** Prompt del sistema (`main.py: _system_text`): `long_term.json` formateado + estilo + casos de confianza + `engram context` recortado a 1500 caracteres. El resto se recupera bajo demanda con la herramienta `search_memory`.
- **[V]** Extracción: `_update_memory_async` guarda cada turno en Engram y, si el texto del usuario parece personal (regex `_PERSONAL`), hace 2 llamadas LLM (¿hay algo que recordar? → extraer JSON) y llama a `update_memory`.
- **[V]** El aviso "Update available" de Engram sale por stderr y no contamina el prompt.

### Problemas reales encontrados

1. **[V] Fallos silenciosos.** `_engram()` ignora el código de salida y devuelve `""` ante cualquier error. `search_memory` lo presenta como "No memories found", no como fallo. `_engram_save_async` lanza un hilo y nunca comprueba si guardó. Esto contradice el requisito de no presentar fallos como éxitos.
2. **[V] Sin upsert en Engram.** Cada cambio de un hecho crea una observación nueva (comentario `ponytail:` existente). Engram CLI admite `--scope` pero no `topic_key`; el HTTP API sí (según el comentario existente, **[P]** no comprobado).
3. **[V] Duplicados semánticos en `long_term.json`.** Ejemplo real: `nickname_preference`, `preferred_address`, `addressed_as_sir`, `atlas_addresses_user_as_senor` dicen lo mismo. La extracción no comprueba si ya existe un hecho equivalente.
4. **[V] Datos temporales guardados como permanentes.** `meeting_with_technician` ("reunión a la 1:55") y `daily_reminders` están en `notes`.
5. **[V] Ningún diagnóstico de memoria en el arranque.** El log solo muestra "Initialised. Engine=…".

## 3. Origen de los chats que se acumulan

- **[V]** Los "chats" que el usuario vio crecer (125 el 22–26 oct, ya limpiados a mano; quedan 4) son **transcripciones de sesión de Claude Code** en `~/.claude/projects/C--Users-pablo-OneDrive-Documentos-Atlas/*.jsonl`.
- **[V]** Los crea el motor `claude_sub`: `agent/llm.py: _subscription_complete` llama a `claude_agent_sdk.query()` en cada llamada auxiliar. Cada `query()` arranca el CLI de Claude Code, que guarda una sesión nueva en disco. Con el motor de suscripción, un turno personal genera hasta 3 sesiones (conversación + 2 llamadas de memoria), y cada tarea en segundo plano varias más (planificador, ejecutor, resumen).
- **[V]** `agent/self_improve.py` también usa `query()` con `cwd=BASE` y crea sesiones en la misma carpeta.
- **[V]** El CLI incluido (Claude Code 2.1.308, SDK 0.2.172) admite `--no-session-persistence`, que evita guardar esas sesiones. **[P]** Falta comprobar que el SDK lo pasa correctamente vía `extra_args`.
- **[V]** Ninguna otra parte del código crea historiales: el motor Gemini usa `session_resumption` en memoria; `_Claude`/`_OpenAICompat` mantienen el historial en RAM y lo vacían a los 40 mensajes. Engram guarda los turnos como observaciones, no como chats.

Clasificación:

| Tipo | Dónde | ¿Persistir? |
|---|---|---|
| Turnos usuario↔Atlas | Engram `conversation` | Sí (ya ocurre) |
| Sesiones de llamadas auxiliares (claude_sub) | `~/.claude/projects/…/*.jsonl` | No: son técnicas, se pueden evitar |
| Sesiones de self-improve | igual | Opcional (auditoría de cambios de código) |
| Resultados de tareas | `task_queue` en RAM | No hay persistencia hoy |
| Logs | `atlas.log` | **[P]** rotación no comprobada |
| Snapshots de código | `backups/code/` | Ya limitados a 10 |

## 4. Interfaz

- **[V]** El lugar natural para "ATLAS ID Y MEMORIA" es `SettingsOverlay`. Hoy es un solo panel de 520×640 px que ya va lleno; añadir la sección exige pestañas o una segunda página. **Cambio de estructura a confirmar.**
- **[V]** Diagnóstico de arranque: `LogWidget.append_log` / `AtlasUI.write_log` (líneas `SYS:`) ya existen y sirven para mostrar el estado sin pantallas nuevas.
- **[V]** No existe pantalla de bienvenida aparte: la primera vez se abre `SettingsOverlay` sin botón cancelar.

## 5. Componentes reutilizables

- `config/api_keys.json` + `get_config()` para preferencias de sincronización.
- `SettingsOverlay` y sus helpers `_lbl`, `_field`, estilo de botones.
- `write_log` para el diagnóstico de arranque.
- `memory_manager` como único punto de escritura (todo pasa por `update_memory`, `remember`, `forget`, `save_conversation`, `save_skill`).
- `updater.py backup` para la copia de seguridad previa a migrar.
- Patrón de login en navegador (`subscription_login`) como modelo para el login con Google.
- `requests` ya instalado: basta para hablar con Supabase (GoTrue + PostgREST) sin añadir `supabase-py`.

## 6. Engram ya trae sincronización

- **[V]** Engram 3.5.2 incluye `engram sync` (exporta/importa chunks comprimidos en `.engram/`) y `engram cloud` (servidor propio respaldado por Postgres, con token Bearer, autosync opcional y lista de proyectos permitidos). Estado actual: `Cloud status: not configured`.
- **[P]** No he leído la documentación de `engram cloud` ni probado el flujo; no sé si admite multiusuario ni identidad Google. Por la ayuda, la autenticación es por token compartido, no OAuth.

Esto abre dos caminos para la parte remota:

| | A. Supabase (lo que pide el documento) | B. Engram Cloud |
|---|---|---|
| Login con Google | Nativo (Supabase Auth, PKCE + loopback) | No; token Bearer |
| Aislamiento por usuario | RLS en Postgres | Allowlist de proyectos; **[P]** multiusuario |
| Sincroniza | Lo que programemos (adaptador propio) | Observaciones de Engram tal cual |
| Hosting | Supabase gestionado (plan gratis) | Servidor propio que hay que mantener |
| Código nuevo | Adaptador de sync + tablas + RLS | Configuración + envoltorio |

Recomendación: **A (Supabase) para identidad, dispositivos y la tabla de recuerdos compartidos**, con Engram como memoria local que se alimenta de lo que llega. B no cubre el login con Google.

## 7. Funciones que faltan

Todas **[V]** ausentes: ATLAS ID, OAuth Google, identidad de dispositivo, tabla remota, cola de cambios pendientes, resolución de conflictos, diagnóstico de arranque, sección de ajustes, métricas, resúmenes por proyecto/conversación, limpieza de sesiones.

## 8. Archivos a modificar / crear (propuesta)

Modificar:
- `memory/memory_manager.py`: errores visibles de Engram, verificación de escritura, gancho de cola de sincronización, exclusión de secretos.
- `agent/llm.py` (y `agent/self_improve.py`): no persistir sesiones auxiliares.
- `main.py`: diagnóstico de arranque, arranque del hilo de sincronización.
- `ui.py`: sección ATLAS ID Y MEMORIA en ajustes.
- `requirements.txt`: solo si hace falta (ver abajo).
- `readme.md`.

Crear:
- `memory/sync.py`: cola local (SQLite stdlib), push/pull a Supabase, conflictos.
- `memory/atlas_id.py`: login Google vía Supabase (PKCE, loopback 127.0.0.1), refresco, cierre, id de dispositivo.
- `memory/diagnostics.py`: comprobaciones reales de arranque.
- `supabase/migrations/001_jarvis_id.sql`: tablas `devices`, `memories`, RLS.
- `docs/atlas-id-setup.md` y `config/atlas_id.example.json`.
- Tests nuevos en `tests/`.

## 9. Dependencias nuevas

- **Ninguna obligatoria.** `requests` cubre las APIs REST de Supabase; `sqlite3`, `secrets`, `hashlib`, `http.server`, `webbrowser` son stdlib.
- Opcional: `keyring` para guardar el refresh token en el almacén de credenciales de Windows/macOS en lugar de un archivo. Recomendado por seguridad (requisito 7.2).

## 10. Riesgos de migración

- Subir recuerdos actuales a la nube: incluyen datos personales (ubicación) y hechos ruidosos. Hay que deduplicar antes y pedir permiso por categoría.
- `long_term.json` usa claves libres; para sincronizar hace falta un id estable por recuerdo (`categoría/clave` + uuid) sin romper el formato que lee `format_memory_for_prompt`.
- Engram no tiene upsert por CLI: al bajar recuerdos de otro PC pueden duplicarse observaciones.
- Cambiar `SettingsOverlay` a pestañas toca la pantalla de primera configuración.
- El login con Google requiere un proyecto Supabase y un cliente OAuth en Google Cloud que solo el usuario puede crear.

## 11. Plan de pruebas

- Unitarias offline (sin red): cola de sincronización, conflictos, exclusión de secretos, id de dispositivo, persistencia de ajustes, diagnóstico con Engram simulado caído/funcionando, limpieza que respeta sesiones activas.
- Integración con Engram real en un `ENGRAM_DATA_DIR` temporal (nunca el `~/.engram` del usuario).
- RLS: script SQL de prueba con dos usuarios sobre el proyecto Supabase de pruebas (requiere el proyecto).
- Login Google: manual, con el proyecto configurado.
- Instalador: **no se ejecuta en este PC**.

## 12. Orden de implementación propuesto

1. Fallos silenciosos de Engram + no persistir sesiones auxiliares (arregla el crecimiento de chats en origen).
2. Diagnóstico de arranque.
3. `atlas_id.py` + migración SQL + documentación de configuración.
4. Sección de ajustes.
5. `sync.py` (cola offline, push/pull, conflictos) conectado a `memory_manager`.
6. Resúmenes por proyecto y limpieza segura.
7. Métricas.
8. Pruebas y informe final.

## 13. Decisiones del usuario (2026-10-26)

- Backend remoto: **Supabase** (opción A).
- Chats acumulados: **no persistir las sesiones auxiliares**, salvo las de self-improve.
- Ajustes: **pestañas**. MOTOR conserva el contenido actual sin cambios; la pestaña nueva es ATLAS ID Y MEMORIA.
- Limpieza de `long_term.json`: hacer copia primero, proponer y preguntar. Aprobado:
  - unir los 4 hechos de "señor" en 1;
  - unir los 2 de avisos por voz en 1;
  - unir los 2 de idioma en 1;
  - quitar los temporales;
  - borrar `atlas_env_file`.

Resultado de la implementación: `docs/informe-atlas-id.md`.

# Informe final: JARVIS ID y memoria compartida

> Informe de la implementación de JARVIS ID, publicada en la versión 2.4.0. Guía de uso: [jarvis-id-setup.md](jarvis-id-setup.md).

Fecha: 2026-10-26 · Base: commit `8f51efd` (v2.3.1).

Leyenda de estado:

- **Implementado y probado**: el código existe y una prueba automática lo comprueba.
- **Implementado, no probado**: el código existe, pero ninguna prueba lo ejecuta.
- **Parcial**: falta una parte, que se explica.
- **Pendiente de configuración externa**: el código está listo, pero falta el proyecto de Supabase o Google.
- **Pendiente**: no está implementado.

**Conclusión: la implementación NO está terminada.**

- Supabase y Google ya están configurados, y el inicio de sesión real funciona (ver punto 6).
- Falta probar la sincronización real entre dos dispositivos y RLS con dos cuentas.
- Faltan los resúmenes de conversaciones (Fase 6.3) y la depuración automática (Fase 6.4).

El resto está implementado y probado sin red.

## 1. Qué se encontró en la auditoría

El detalle está en `docs/auditoria-jarvis-id.md`. Resumen:

- **Fallos silenciosos de Engram.** Antes, un error de Engram se mostraba como "no hay recuerdos" y un guardado fallido no se notaba.
- **Origen de los chats acumulados.** Cada llamada auxiliar del motor `claude_sub` arrancaba Claude Code, y Claude Code guardaba una sesión nueva en disco.
- **Datos repetidos o temporales en `long_term.json`.** Por ejemplo, había 4 hechos que decían "llámame señor".
- **Sin diagnóstico al arrancar.**
- **Sin autenticación propia.**

## 2. Componentes reutilizados

| Componente | Uso |
|---|---|
| `memory_manager.update_memory` / `forget` | Único punto de escritura de la memoria. La sincronización se engancha ahí. |
| `config/api_keys.json` y `get_config()` | Guardan la configuración de JARVIS ID. Se añadió `update_config()` para escrituras parciales atómicas. |
| `SettingsOverlay` (`_lbl`, `_field`, estilo de botones) | Construyen la nueva pestaña. |
| `JarvisUI.write_log` (líneas `SYS:`) | Muestra el diagnóstico de arranque y los resultados de la sincronización. |
| `requests` (ya instalado) | Habla con Supabase Auth (GoTrue) y PostgREST. No se añadió `supabase-py`. |
| `updater.py backup` / `restore` | Copias de seguridad. Ahora también incluyen `sync.db` y la sesión. |
| Engram CLI | Sigue siendo la memoria de largo plazo. No se usó ninguna API de Engram que no exista. |

## 3. Archivos modificados

| Archivo | Cambio |
|---|---|
| `agent/llm.py` | Las llamadas auxiliares de `claude_sub` usan `--no-session-persistence`, así que no crean chats en disco. |
| `agent/self_improve.py` | Mantiene la persistencia (`persist=True`): esas sesiones sirven de auditoría de cambios de código. |
| `memory/memory_manager.py` | Cambios: <ul><li>`_engram` devuelve `None` y el motivo cuando falla.</li><li>`_engram_save` confirma el número del recuerdo guardado.</li><li>`search_memory` dice FAILED en vez de "nada encontrado".</li><li>`diagnose()` hace una prueba real de escritura, lectura y borrado.</li><li>Ganchos de sincronización en `update_memory` y `forget`.</li></ul> |
| `main.py` | Diagnóstico de arranque en el log. Hilo de sincronización cada 5 minutos. |
| `config/__init__.py` | `update_config()`: fusiona cambios y escribe de forma atómica. Si el archivo no se puede leer, no lo sobrescribe. |
| `ui.py` | Ajustes rediseñados con tres pestañas: **GENERAL** (tema claro/oscuro, audio, inicio con el sistema), **AI ENGINE** (proveedor, claves; modelo y URL en *Advanced*) y **ACCOUNT** (JARVIS ID, sincronización, dispositivos, diagnóstico). SAVE & START conserva los valores que guarda ACCOUNT. |
| `updater.py` | La copia de seguridad incluye `memory/sync.db` y `memory/jarvis_id.session`. |
| `installer/build.py` | El instalador nunca empaqueta `jarvis_id.session` ni `sync.db`. |
| `.gitignore` | Excluye la sesión, `sync.db` y los `.tmp` de escritura atómica. |
| `tests/test_core.py` | 4 pruebas de Engram: fallo visible, guardado y lectura reales en un almacén aislado, y diagnóstico. |
| `memory/long_term.json` (git-ignored) | Limpieza aprobada por usted: <ul><li>4 hechos de "señor" unidos en 1.</li><li>2 de avisos por voz unidos en 1.</li><li>2 de idioma unidos en 1.</li><li>Temporales quitados.</li><li>`jarvis_env_file` borrado.</li></ul> Copia previa en `backups/20261026-111103-before-memory-cleanup.zip`. |

## 4. Archivos nuevos

| Archivo | Qué es |
|---|---|
| `memory/jarvis_id.py` | Varias funciones: <ul><li>Login con Google por Supabase: OAuth PKCE con retorno a `http://127.0.0.1:53682/callback`.</li><li>Renovación de tokens y cierre de sesión.</li><li>Sesión cifrada con DPAPI.</li><li>Id aleatorio por dispositivo.</li><li>Lista de dispositivos y quitar uno.</li></ul> |
| `memory/sync.py` | Sincronización: <ul><li>Cola local sin conexión.</li><li>Envío y recepción de cambios.</li><li>Versiones y conflictos.</li><li>Filtro de secretos.</li><li>`describe()` y `stats()` para la interfaz.</li></ul> |
| `supabase/migrations/001_jarvis_id.sql` | Tablas `devices` y `memories`. <ul><li>RLS activado y forzado.</li><li>Trigger que rechaza versiones viejas.</li><li>`revoke` a `anon`.</li></ul> |
| `docs/jarvis-id-setup.md` | Pasos exactos para Supabase y Google. Incluye un ejemplo de configuración sin credenciales. |
| `docs/auditoria-jarvis-id.md` | Auditoría de la Fase 0 y sus decisiones. |
| `docs/informe-jarvis-id.md` | Este informe. |
| `tests/test_jarvis_id.py` | 8 pruebas: proyecto incluido, login PKCE con su página, página de error escapada, cifrado de sesión, renovación, sin conexión, cierre, id de dispositivo y filtro de entradas. |
| `tests/test_sync.py` | 6 pruebas con un servidor PostgREST falso y dos dispositivos. |
| `tests/test_settings_ui.py` | 1 prueba de la pestaña nueva, en Qt sin pantalla. |

## 5. Dependencias añadidas

**Ninguna.**

- `requests` ya estaba instalado.
- `sqlite3`, `http.server`, `secrets`, `hashlib` y `ctypes` (para DPAPI) son de la biblioteca estándar.
- Se descartó `keyring`: DPAPI por `ctypes` da la misma protección en Windows sin añadir un paquete.

## 6. Configuración de Google Auth

**Configurado y verificado.**

- Supabase: proyecto `bevrstsvfikouhacidic`, migración ejecutada, Google activado (`/auth/v1/settings` devuelve `google: true`), Redirect URL `http://127.0.0.1:53682/callback` permitida.
- Google Cloud: proyecto `jarvis-id-512816`, cliente OAuth web "JARVIS Supabase", redirect URI `https://bevrstsvfikouhacidic.supabase.co/auth/v1/callback`. El Client Secret lo pegó usted, solo en Supabase.
- La app de Google está en **modo de prueba**: solo entran los usuarios de prueba (hoy `pablo.romero.diaz14@gmail.com`). Para abrirla a todos hay que pulsar **Publish app**.
- JARVIS trae la URL y la clave publishable en `memory/jarvis_id.py`. El usuario no pega nada: solo inicia sesión.

JARVIS nunca tiene el secreto de Google ni la clave `service_role`.

## 7. Integración de JARVIS ID

- **Es opcional.** Sin configurar, el log dice "JARVIS ID: not set up (optional); memory stays on this PC." y todo funciona igual. No cambia el motor de IA.
- **Login.** El botón abre el navegador. Un servidor local espera el código solo durante el login y no escribe nada en el log. JARVIS cambia el código por tokens con PKCE.
- **Sesión.**
  - Se guarda cifrada con DPAPI en `memory/jarvis_id.session`.
  - Si no hay red, la sesión se conserva.
  - Si el servidor rechaza la renovación, la sesión se borra y JARVIS pide iniciar sesión otra vez.
- **Dispositivos.**
  - Cada instalación crea un UUID aleatorio, nunca el nombre del PC.
  - Al iniciar sesión, el dispositivo se registra.
  - Cada sincronización actualiza `last_seen`.
  - Si otro dispositivo lo quitó, este cierra su sesión.

## 8. Conexión de la memoria compartida con Engram

Engram sigue siendo la memoria de este PC.

- **Qué se comparte.** Solo los hechos de `long_term.json`: identity, preferences, projects, relationships, wishes y notes.
- **Qué no se comparte.**
  - Conversaciones.
  - Estilo.
  - Casos de confianza.
  - Skills.
  - Observaciones de Engram.
- **Datos recibidos.** Entran por `update_memory(..., sync=False)`. Esa función los guarda en `long_term.json` y también en el Engram local, como cualquier hecho nuevo. `sync=False` evita reenviarlos.

## 9. Funcionamiento de la sincronización

**Id estable.** Cada dato tiene el id `categoría/clave` y una versión.

**Cola local.** Cada cambio en `update_memory` o `forget` entra en la cola `memory/sync.db`, salvo en estos casos:

- La sincronización está apagada.
- El dato parece un secreto.
- La clave no es válida.

**Envío.**

- Un dato nuevo se envía como versión 1.
- Un dato conocido se actualiza solo si el servidor sigue en la misma versión (`version=eq.N`). El trigger SQL rechaza cualquier versión que no sea la siguiente.
- Cada dato aceptado se confirma por separado.
- Si falla la red, lo que no se envió sigue en la cola.

**Conflicto.**

- Se aplica la copia del servidor.
- La copia local se guarda en la tabla `conflicts`.
- El panel muestra los últimos 3 conflictos.
- Nunca hay sobrescritura silenciosa.

**Recepción.**

- JARVIS pide las filas con `updated_at` desde la última recepción, menos 5 minutos de margen.
- Ignora las versiones que ya conoce y los datos que esperan en la cola.

**Borrados.** Se envían como `deleted=true` para que los otros dispositivos también los borren.

**Primera sincronización de una cuenta.** Envía los hechos locales válidos que ya existían.

**Cuándo sincroniza.**

- Cada 5 minutos, si la sincronización está activa y hay sesión.
- Al pulsar ⟳ Sync now.
- Al activar la casilla.

## 10. Conservación de la memoria local

- `long_term.json` sigue siendo la fuente que lee JARVIS. Funciona igual sin conexión y sin JARVIS ID.
- Un cambio cuenta como sincronizado solo cuando el servidor lo acepta. Hasta entonces sigue "pending" en la cola.
- El instalador no toca `sync.db` ni la sesión. `updater.py backup` los incluye.

## 11. Gestión de conversaciones

**Implementado y probado:**

- Las llamadas auxiliares de `claude_sub` ya no crean sesiones en disco. Se comprobó de forma real: no se creó ningún `.jsonl`.
- `self_improve` las mantiene, por decisión suya.

**Sin cambios:**

- Cada turno se sigue guardando en Engram (`type=conversation`). Ese es el historial archivado.
- `search_memory` lo recupera bajo demanda.
- El prompt incluye `engram context`.

**Pendiente: resúmenes persistentes (Fase 6.3).** No se implementaron.

Propuesta: cuando una conversación lleve 10 minutos inactiva, una llamada al motor elegido escribe un resumen en Engram con `type=summary`. El resumen guarda objetivo, decisiones, pendientes y referencias a los turnos. Esto cuesta una llamada al LLM por conversación, así que necesita su aprobación.

**Pendiente: depuración automática (Fase 6.4).** No se implementó.

- La causa del crecimiento ya está corregida en origen.
- Hoy no hay sesiones temporales nuevas que borrar.
- La única depuración real fue la limpieza manual de `long_term.json`, con copia previa y su aprobación.

**Métricas (Fase 6.6).** Son parciales.

| Métrica | Dónde aparece |
|---|---|
| Hechos y tamaño de `long_term.json` | Diagnóstico de arranque y Run diagnostic |
| Número de recuerdos de Engram y tamaño de su base | Diagnóstico de arranque y Run diagnostic |
| Prueba real de escritura y lectura | Diagnóstico de arranque y Run diagnostic |
| Datos sincronizados, pendientes y último error de sincronización | Diagnóstico de arranque y Run diagnostic |

Faltan "última depuración" y "sesiones temporales", porque dependen de la Fase 6.4.

## 12 y 13. Pruebas ejecutadas y resultados reales

Comando: `.venv\Scripts\python.exe -m unittest discover tests`. Resultado real: **`Ran 153 tests … OK`**. Antes de los cambios eran 134.

Diagnóstico real en este PC, con un probe que se borró después:

```
Memory file: 10 facts, 1239/2200 chars.
Engram: engram 3.5.2, 245 memories (1.2 MB), doctor ok.
Engram write/read/delete: ok (1484 ms).
JARVIS ID: not set up (optional); memory stays on this PC.
```

Pruebas de aceptación de la Fase 8:

| # | Prueba | Estado |
|---|---|---|
| 1 | Arranca sin JARVIS ID | Probado por módulos: estado "not set up", ajustes, diagnóstico. No se arrancó la app completa. |
| 2 | Login con Google | Probado contra un servidor falso: flujo PKCE completo. **Login real verificado:** en Supabase quedó 1 usuario y 1 dispositivo (`WPOBUC234`), y la sesión cifrada en este PC. Aún 0 memorias: la sincronización está apagada. |
| 3 | Errores de autenticación visibles | Probado: sin configurar, renovación rechazada, sin red. |
| 4 | Identidad por dispositivo | Probado. |
| 5 | Engram detectado al arrancar | Probado, y verificado en este PC. |
| 6 | Fallos de Engram no son éxitos | Probado. |
| 7 | Guardar y recuperar recuerdos | Probado con un Engram aislado (`ENGRAM_DATA_DIR` temporal). |
| 8 | Memoria local sin conexión | Probado. |
| 9 | Pendientes se envían al volver la red | Probado. |
| 10 | Sin duplicados entre dispositivos | Probado con dos dispositivos simulados: id estable y versión conocida ignorada. |
| 11 | Conflictos detectados | Probado. |
| 12 | RLS impide ver datos ajenos | **Parcial.** RLS está activado y forzado en el servidor real. Falta probar con dos cuentas que B no ve las filas de A. |
| 13 | Secretos no se sincronizan | Probado. |
| 14 | Ajustes persisten | Probado: la pestaña escribe `device_name` y `memory_sync` en `api_keys.json` y SAVE & START no lo deshace. Ya no tiene campos de URL ni clave. No se reinició la app. |
| 15 | La interfaz refleja el estado real | Probado (`describe()` y la etiqueta de estado). |
| 16 | Resúmenes y archivo | **Pendiente** (ver punto 11). |
| 17 | La limpieza no borra chats activos | No aplica: no hay limpieza automática. |
| 18 | Un fallo de escritura impide borrar | Parcial: el probe del diagnóstico solo se borra si se confirmó su escritura. No hay depuración automática. |
| 19 | Retomar proyectos tras reiniciar | Sin cambios: `projects` en `long_term.json` y Engram. |
| 20 | Datos conservados en actualizaciones | Implementado (exclusión en el instalador y copia de seguridad). **No probado: el instalador no se ejecuta en este PC.** |
| 21 | Métricas reales | Probado parcialmente (ver punto 11). |
| 22 | No rompe herramientas ni motor | Las 134 pruebas anteriores siguen pasando. |

La pestaña nueva también se renderizó sin pantalla (Qt offscreen) para comprobar que cabe en 520×640.

## 14. Pasos externos pendientes

Hechos: proyecto de Supabase, migración, Google OAuth, Redirect URL, proyecto incluido en el código y login real.

Pendientes:

1. Decidir si se publica la app de Google (hoy solo entran usuarios de prueba).
2. Activar SYNC MEMORY ACROSS DEVICES y probar dos dispositivos.
3. Probar RLS con dos cuentas: la cuenta B no debe ver las filas de la cuenta A.
4. Probar el instalador en un equipo externo autorizado.

## 15. Limitaciones

- En macOS y Linux la sesión no se guarda en disco: hay que iniciar sesión en cada arranque.
- Solo se comparten hechos de `long_term.json`. El Engram de cada PC sigue siendo local.
- El límite de 2200 caracteres de `long_term.json` también se aplica a los datos recibidos.
- En un conflicto gana el servidor de forma automática. La copia local queda guardada, pero no hay botón para restaurarla: hay que hacerlo a mano.
- Los resúmenes y la depuración automática están pendientes.
- El login usa el puerto fijo 53682. Si está ocupado, JARVIS lo dice y no inicia sesión.

## 16. Restauración de datos

- **Copia antes de la limpieza de memoria:** `backups/20261026-111103-before-memory-cleanup.zip`.
- **Copia nueva manual:** `.venv\Scripts\python.exe updater.py backup`
- **Restaurar una copia:** `.venv\Scripts\python.exe updater.py restore backups\<archivo>.zip`. Antes de restaurar, crea otra copia, así que la restauración también se puede deshacer.
- **Volver a empezar la sincronización en este PC:** cierre JARVIS y borre `memory/sync.db`. La próxima sincronización vuelve a enviar los hechos locales. Los conflictos se resuelven como siempre.
- **Borrar los datos del servidor:** desde el panel de Supabase, en la tabla `memories`. Si borra la cuenta de Supabase, sus filas se borran en cascada.

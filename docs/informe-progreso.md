# Informe de progreso: autonomía de JARVIS

Fecha: 2026-10-23. Base: commit `c7df3e2` (versión 2.1.0). Pruebas automáticas: 110, todas correctas (`python -m unittest discover tests`).

Regla de este informe: generar código no es completar una función. "Verificado" solo significa que hay una prueba que lo demuestra.

## Matriz de capacidades

| Capacidad | Estado | Evidencia |
|---|---|---|
| Planificador (objetivo, plan, herramientas, ejecución, observación, corrección) | Verificado | `agent/planner.py`, `agent/executor.py`: plan JSON, reintento, saltar paso, replanificar (2 veces), análisis de error. Pruebas en `tests/test_core.py` |
| Registro de herramientas con disponibilidad real | Verificado (nuevo) | `tool_status()` en `agent/executor.py`, herramienta `capabilities`. Ejecutado en este PC |
| Investigación web autónoma | Verificado | `actions/web_search.py` (ddgs, con fecha y fuente). Nuevo: `solve_task` busca en la web tras el primer fallo |
| Constructor de skills | Verificado | `solve_task` escribe un script, lo prueba, lo reintenta y guarda solo los que funcionan |
| Metadatos de verificación en skills | Verificado (nuevo) | Línea `# VERIFIED: <fecha> on <SO>` en cada skill guardada |
| Reversión de skills | Verificado | Cada versión es un archivo nuevo; las anteriores se conservan |
| Confirmación de acciones de alto impacto | Verificado | `_DANGEROUS` y `NeedsConfirmation` en `agent/executor.py` |
| Tareas largas con progreso y cancelación | Verificado | `agent/task_queue.py` y HUD |
| Copias de seguridad y restauración de datos | Verificado | `updater.py`, `tests/test_updater.py` |
| Controlador SP621E por Bluetooth | Completado, prueba física pendiente | `actions/ble_light.py`, `tests/test_ble_light.py` |
| Servidores MCP externos | Pendiente | El motor Claude ya expone las herramientas como MCP interno (`engines.py`) |
| Instalador | Bloqueado en este PC | Prohibido ejecutarlo aquí; necesita una máquina virtual o un PC externo |

## Completado y verificado

### 1. Registro dinámico de herramientas (Etapa B)

1. **Objetivo:** que JARVIS sepa qué puede hacer en este equipo antes de prometerlo.
2. **Cambio:** `tool_status()` importa el módulo de cada herramienta y comprueba sus paquetes opcionales (`_REQUIRES`). Informa del sistema operativo y del número de skills aprendidas. Se expone como herramienta `capabilities`.
3. **Archivos:** `agent/executor.py`, `main.py`.
4. **Pruebas:** ejecución real en este PC.
5. **Resultado:** 15 herramientas disponibles. `send_message` sale "partial, missing telethon": el entorno `.venv` no tiene `telethon`, aunque está en `requirements.txt`. Sin `telethon`, Telegram no funciona en este entorno de desarrollo.
6. **Limitaciones:** las herramientas que solo existen en `main.py` (correo, Apple Music, pantalla) no están en el registro del ejecutor.
7. **Revertir:** quitar `tool_status`, `_REQUIRES` y la entrada `capabilities` de `main.py`.

### 2. Investigación dentro de solve_task (Etapa C)

1. **Objetivo:** si un script falla, buscar documentación antes de reintentar.
2. **Cambio:** tras el primer fallo, `_research()` busca en la web con el objetivo y la última línea del error. Los resultados entran en el siguiente intento como pistas no verificadas. Si la búsqueda falla, la tarea sigue.
3. **Archivos:** `agent/executor.py`.
4. **Pruebas:** `test_failed_attempt_researches_once_and_research_errors_do_not_block`.
5. **Resultado:** busca una sola vez y un fallo de red no bloquea la tarea.
6. **Limitaciones:** solo usa DuckDuckGo. No lee la página completa, solo el resumen.
7. **Revertir:** quitar `_research` y el parámetro `research` de `_write_script`.

### 3. Metadatos de verificación en skills (Etapa D)

1. **Objetivo:** memoria técnica que distinga lo verificado y cuándo.
2. **Cambio:** cada skill guardada termina con `# VERIFIED: <fecha> on <SO y versión>`. También va a Engram. No se guarda la salida del script, porque puede tener datos personales.
3. **Archivos:** `memory/memory_manager.py`.
4. **Pruebas:** `test_saved_skill_records_verification_and_keeps_old_versions`.
5. **Resultado:** la línea se escribe y las versiones anteriores se conservan.
6. **Limitaciones:** las skills antiguas no tienen esta línea.
7. **Revertir:** volver a escribir `code` sin la línea final.

## Completado, pendiente de verificación

### 4. Controlador SP621E por Bluetooth (Etapa E)

1. **Objetivo:** "JARVIS, conecta mi ordenador por Bluetooth al controlador SP621E y cambia las luces a color morado".
2. **Cambio:** nueva herramienta `ble_light` (acciones: `color`, `on`, `off`, `status`, `scan`) sobre `bleak`. El protocolo viene del proyecto UniLED (`lib/ble/banlanx2.py`, licencia MIT). No se inventó ningún comando:
   - Característica `0000ffe1-0000-1000-8000-00805f9b34fb` (servicio `ffe0`), para escribir y para notificar.
   - Encendido: `A0 62 01 01`. Efecto sólido: `A0 63 01 BE`. Color: `A0 69 04 R G B FF`. Consulta de estado: `A0 70 00`.
   - Detección: fabricante 20563 o 5053 con primer byte `0x0D` o `0x16`, o nombre "SP621E".
   - Verificación: después de los comandos pide el estado y compara encendido, efecto y RGB. Solo responde "Verified" si coinciden. Si el controlador no contesta, responde "NOT VERIFIED".
   - Acepta nombres en inglés, nombres en español frecuentes ("morado" = RGB 128, 0, 128) y `#RRGGBB`.
3. **Archivos:** `actions/ble_light.py`, `tests/test_ble_light.py`, `agent/executor.py`, `agent/planner.py`, `main.py`, `requirements.txt` (`bleak`), `installer/build.py` (permiso `NSBluetoothAlwaysUsageDescription` en macOS).
4. **Pruebas:** 7 pruebas con un controlador simulado: bytes de comandos, nombres de color, mensajes en varios paquetes, flujo morado verificado, sin respuesta (no verificado), dispositivo no encontrado, otros dispositivos ignorados. Escaneo real en este PC: el adaptador Bluetooth funciona, pero no apareció ningún SP621E.
5. **Resultado:** la lógica pasa las pruebas. Falta la prueba física.
6. **Limitaciones y bloqueos reales:**
   - No hay un SP621E visible. Puede estar apagado, lejos o conectado a la app del móvil. Mientras la app está conectada, el controlador deja de anunciarse.
   - `SETTLE_SECONDS = 0.3` es una estimación. Si el controlador tarda más en informar, hay que subirlo.
   - En Linux hace falta BlueZ. En macOS, el permiso de Bluetooth del sistema.
7. **Revertir:** borrar `actions/ble_light.py` y sus entradas en `executor.py`, `planner.py` y `main.py`.

**Prueba física pendiente:** encender el SP621E, cerrar la app del móvil y decir "JARVIS, pon las luces en morado". La respuesta debe empezar por "Verified".

## En desarrollo

Nada.

## Pendiente

- **MCP externo:** conectar servidores MCP de terceros al motor Claude (`mcp_servers` en `engines.py`). Solo si aporta algo que no hagan las herramientas nativas.
- **Telethon en `.venv`:** instalar las dependencias de `requirements.txt` en el entorno de desarrollo para que Telegram funcione.
- **Prueba en el PC del usuario con la app abierta:** decir "JARVIS, qué herramientas tienes disponibles" (usa `capabilities`).

## Bloqueado

- **Prueba del instalador (prueba 11):** prohibido ejecutarlo en el PC actual del usuario. Necesita una máquina virtual o un PC externo autorizado.
- **Prueba física del SP621E (prueba 9):** necesita el controlador encendido y visible.

## No aplicable

- Reentrenar el modelo. JARVIS aprende guardando skills y memoria, no cambiando el modelo.

## Pruebas de aceptación

| # | Prueba | Estado |
|---|---|---|
| 1 | Continuidad | Verificado: 110 pruebas correctas, sin funciones eliminadas |
| 2 | Capacidad desconocida | Verificado en pruebas: `solve_task` escribe el script, investiga y lo reintenta |
| 3 | Nueva integración | Completado: SP621E investigado (UniLED) e integrado. Prueba física pendiente |
| 4 | Recuperación de errores | Verificado en pruebas: reintento con otro enfoque y búsqueda web |
| 5 | Memoria técnica | Verificado en pruebas: skills guardadas y encontradas, con fecha de verificación |
| 6 | Verificación | Verificado en pruebas: SP621E sin respuesta da "NOT VERIFIED"; resultados vacíos nunca cuentan como éxito |
| 7 | Tareas largas | Verificado en pruebas: cola en segundo plano y HUD |
| 8 | Actualización | Verificado en pruebas: copias de seguridad y versiones anteriores de skills |
| 9 | SP621E | Bloqueado: no hay hardware visible |
| 10 | Seguridad | Verificado en pruebas: los scripts riesgosos se detienen hasta el "sí" del usuario |
| 11 | Instalador | Bloqueado: prohibido en este PC |

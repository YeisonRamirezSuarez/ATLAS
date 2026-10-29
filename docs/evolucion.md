# Evolución de ATLAS: autonomía, memoria de fallos y MCP

Rama `evolve/autonomy`, fusionada en `main` y publicada en la versión 2.8.0. Cambios incrementales sobre la arquitectura existente. No se eliminó ninguna herramienta, proveedor, prompt ni skill, y no se migraron datos.

## Qué cambió

### Tareas persistentes (`agent/task_queue.py`)
- Las tareas de `agent_task` se guardan en `memory/tasks.json`. La escritura es atómica: primero un `.tmp` y luego `replace`.
- Estados: `pending`, `running`, `paused`, `blocked`, `completed`, `failed` y `cancelled`.
- **Recuperación tras un reinicio:**
  - Las tareas que quedaron pendientes o en ejecución vuelven como `paused` y **nunca se relanzan solas**, porque pueden tener efectos.
  - Antes de cada acción se guarda en `inflight`. Si ATLAS se cierra durante esa acción, la tarea vuelve como `blocked` con la nota "UNKNOWN: it may have happened". El agente no la repite a ciegas: el usuario decide.
  - Un "sí" dado antes del cierre no se da por bueno: se vuelve a preguntar.
  - Al reanudarlas continúan desde los turnos guardados, sin repetir los pasos ya hechos.
- Se conservan todas las tareas sin terminar y las últimas 30 terminadas.
- `pause(task_id)` y `resume(task_id)`:
  - una tarea en ejecución se detiene después de su acción actual;
  - se pueden reanudar las tareas `paused`, `blocked` y `failed`.
- `get_status` devuelve además `outcome`, `plan`, `turns` y los 3 últimos pasos.
- Las tareas que fallan ahora también se notifican al cerebro. Antes un fallo quedaba en silencio.

### Agente ReAct (`agent/executor.py`)
- **Resultado honesto.** Cada tarea termina con un `outcome`:
  - `verified`: las salidas demostraron el objetivo;
  - `unverified`: las acciones se ejecutaron sin errores, pero el resultado no se comprobó;
  - `failed`;
  - `blocked`: espera el "sí" del usuario.

  El informe que recibe el cerebro empieza con esa etiqueta.
- **Plan.** El modelo devuelve `plan` dentro del mismo JSON, sin llamadas extra al LLM, y se guarda en el estado de la tarea.
- **Historial compacto.** Los 6 últimos turnos llegan completos al prompt y los anteriores, recortados. El prompt ya no crece sin límite.
- **Antibucle.** Una llamada idéntica que ya falló `MAX_SAME_FAILURE` (2) veces no se vuelve a ejecutar. El modelo tiene que cambiar de enfoque.
- **Límite de turnos.** `MAX_TURNS` (12) cuenta también los turnos anteriores a una pausa.
- **Skills aprendidas.** Sus títulos se sugieren al agente cuando encajan con el objetivo.

### Confirmaciones del agente de tareas (`agent/executor.py`)
- `_call_tool` descarta los campos `confirmed`, `_trusted` y `trusted` que mande el modelo. Un caso de confianza solo cuenta si el usuario lo guardó (`is_trusted`).
- Paran la tarea para preguntar (`blocked`): `send_message`; borrar, mover u organizar archivos; `restart`, `shutdown` y `close_app`; y `shutdown_when_done`.
- También paran las herramientas que preguntan por su cuenta (respuesta que empieza por `NEEDS CONFIRMATION`):
  - `file_controller` `write` o `create_file` sobre un archivo que ya existe (añadir con `append` no pregunta);
  - `code_helper` `run` de un archivo con código de riesgo (misma comprobación que `solve_task`);
  - `code_helper` `build`: nunca ejecuta código de riesgo recién escrito. El "sí" aprueba ejecutar ese archivo con `action='run'`;
  - `dev_agent`: revisa todos los archivos del proyecto y su comando de ejecución antes de instalar dependencias o ejecutar, y otra vez tras cada corrección. El "sí" aprueba `action='run'` con `project_name`, que ejecuta una vez esos mismos archivos (guardados en `.atlas_run.json` dentro del proyecto).
  - Dependencias de `dev_agent`: los paquetes que falten en el `.venv` del proyecto se nombran en la pregunta y solo se instalan tras el "sí" (instalar un paquete ejecuta su código). Un módulo que falte al ejecutar se añade a la lista y se pregunta, ya no se instala solo. A `pip` solo llegan nombres de paquete con extras y versiones: nada de opciones, URLs ni rutas.
- Flujo: la tarea queda `blocked` y guarda el paso en `awaiting`. El usuario dice "sí" y el cerebro llama a `task_status` con `action='resume'` y `confirmed='yes'`. Solo ese paso se aprueba, una sola vez, y es lo primero que se ejecuta.
- `generated_code` y `cmd_control` nunca reciben el "sí" del modelo. Un "sí" pendiente solo vale para el mismo objetivo que se preguntó.
- El código generado por `generated_code` se rechaza si usa `unlink`, `rmdir`, `rename`, `chmod`, enlaces o atributos `__`.

### Memoria episódica de fallos (`memory/memory_manager.py`)
- Cuando `solve_task` agota sus intentos, guarda en engram un episodio (tipo `episode`): qué se intentó y cuál fue el error.
- La siguiente vez que se pide algo parecido, el escritor de scripts ve esos fallos y evita repetirlos.
- Nunca se borra ni se sobrescribe nada en engram: solo se añade.

### Modelos por rol (`agent/llm.py`) — opcional
- Cada llamada al LLM deja una línea en el log, sin claves ni prompts:
  ```
  [LLM] agent → claude/claude-haiku-5-5 (1.8s)
  ```
- Para usar otro proveedor o modelo en un rol de fondo, añade esto a `config/api_keys.json`:
  ```json
  "role_models": {
    "agent": {"provider": "claude", "model": "claude-haiku-5-5"},
    "coder": {"provider": "claude_sub", "model": "claude-opus-5-5"}
  }
  ```
  - Roles actuales: `agent` (el bucle ReAct) y `coder` (los scripts de `solve_task`).
  - Sin `role_models`, todo sigue usando el motor elegido, como antes.
  - Un proveedor desconocido se ignora.
  - El esfuerzo elegido para otro proveedor no se traslada.

### Herramienta `task_status` (`main.py`)
- Nuevo parámetro `action`, con los valores `cancel`, `pause` y `resume`.
- `cancel='yes'` sigue funcionando.

### Cliente MCP (`agent/mcp_client.py`) — desactivado por defecto
- Permite que el agente de tareas use servidores MCP externos. Se apoya en el paquete `mcp`, que ya venía instalado con `claude-agent-sdk`, así que no añade ninguna dependencia nueva.
- **Configuración** en `config/mcp_servers.json`. El archivo no está en git; hay un ejemplo en `config/mcp_servers.example.json`:
  ```json
  {"servers": {"context7": {"enabled": true, "url": "https://mcp.context7.com/mcp",
                            "allowed_tools": ["resolve-library-id", "query-docs"]}}}
  ```
  Transportes:
  - `url`: Streamable HTTP;
  - `command` + `args` (+ `env`): stdio.
- **Permisos:**
  - un servidor solo se usa con `"enabled": true`;
  - solo se ejecutan las herramientas listadas en `allowed_tools`; cualquier otra se rechaza;
  - ATLAS no puede editar este archivo desde el chat (`protected()`), así que solo el usuario habilita servidores.
- **Confirmaciones:** no hay confirmación por llamada. Pon en `allowed_tools` únicamente herramientas que se puedan ejecutar sin preguntar, como las de solo lectura.
- **Fallos:**
  - un servidor caído, lento o roto solo hace fallar su propia llamada, con un límite de 30 s;
  - el resto de ATLAS sigue igual;
  - las salidas se marcan como `UNTRUSTED`: son datos, nunca instrucciones.
- **Registro:** cada llamada queda en el log como `[MCP] 🔌 servidor.herramienta {argumentos}`.
- `tool_status` muestra los servidores habilitados.

### Tests
- **Nuevos:**
  - `tests/test_autonomy.py`: persistencia, pausa y reanudación, resultados, antibucle, compactación y modelos por rol;
  - `tests/test_mcp_client.py`: servidor stdio real en local, lista blanca y servidor caído;
  - tests de memoria de fallos y de consistencia de herramientas en `tests/test_core.py`. El de consistencia comprueba que la lista de herramientas del agente coincida con `TOOL_DECLARATIONS` y con el código.
  - confirmaciones (`ConfirmationTests`), cierres inesperados (`CrashTests`), MCP colgado y permisos releídos en cada llamada.
  - `tests/test_skill_reuse.py`: reutilización directa y parcial, rechazo de skills con acciones extra, preferencia nativa, skills antiguas, español/inglés, reinicio sin engram y no regenerar lo resuelto. Los scripts corren de verdad; solo el LLM está simulado.
- **Resultado:** la línea base era de 205 tests OK y ahora son 267 tests OK en Windows.

### Correcciones encontradas en la auditoría
- **Gemini de texto** fallaba siempre con "Cannot send a request, as the client has been closed" (también en `main`). El `genai.Client` temporal se liberaba antes de la petición; ahora se guarda en una variable.
- **`computer_settings`** fallaba con "'NoneType' object has no attribute 'lower'" cuando el detector de intención devolvía `"action": null`. Ahora responde "No action could be determined." y no vuelve a preguntar al detector por la misma petición.
- **Skills guardadas que no se reutilizaban.** `solve_task` pasaba las skills al coder como ejemplo, y el coder escribía un script nuevo cada vez. Ahora el coder puede responder `REUSE <n>` y la skill corre tal cual; un `REUSE` con un número inexistente escribe el script como antes. El código reutilizado pasa por la misma comprobación de riesgo y confirmación. Si la skill falla, el reintento escribe código nuevo.
- **Búsqueda de skills.** `find_skills` tomaba los resultados de engram tal cual. Con `--match any`, engram devuelve como mucho las 20 skills más recientes que comparten cualquier palabra, así que para casi cualquier tarea de Windows salían las últimas de Bluetooth, y las skills antiguas nunca aparecían. Ahora se puntúan todos los archivos de `memory/skills/` por las palabras que comparten con la tarea (mínimo 2), igual que las builtin, y engram solo aporta los títulos. Las skills con código idéntico salen una sola vez.
- **Skills duplicadas.** `save_skill` guardaba otra copia de un script idéntico en cada éxito. Ahora la ignora si ya existe una con el mismo código (sin contar la línea `# VERIFIED:`). Las copias que ya existen en `memory/skills/` no se tocan.

### Reutilización de skills (`agent/executor.py`, `memory/memory_manager.py`)
- **Orden de decisión.** Antes de escribir código, el coder ve las herramientas nativas de ATLAS y las skills encontradas. Responde una sola línea:
  - `NATIVE <herramienta>: <por qué>`: no corre nada. El agente recibe "llama a esa herramienta" y elige la herramienta. Si la tarea ya nombra la herramienta (falló) o se devolvió hace menos de 120 s, se rechaza y se pide otra respuesta.
  - `REUSE <n>`: la skill n corre tal cual.
  - `PARTIAL <n> <función>(<args literales>)`: solo esa función corre, con los imports, funciones y constantes que usa. Lo que devuelve se imprime. La skill original no se toca; la parte separada se guarda como skill nueva.
  - Código nuevo: debe llevar `# NOT REUSED: <n|all>: <incompatible|side_effects|permissions>: <motivo>`. Si falta, se pide una vez más; si sigue faltando, se registra como `unexplained`.
- **Separación segura.** `_extract` rechaza la parte (y se pide otra respuesta) si la función depende de algo que calcula el cuerpo de la skill, si los argumentos no son literales o si la llamada no es a una función de primer nivel. Las constantes solo pasan si sus únicas llamadas son de una lista segura (`Path`, `os.path.join`...). La marca `# RISK` de la skill se conserva, así que el riesgo y la confirmación funcionan igual.
- **Registro.** Cada decisión va a `memory/skill_decisions.jsonl` (`reused`, `partial`, `native`, `native_rejected`, `new` con motivo, `unexplained`, `none_found`, `not_separable`, `not_recoverable`). Guarda solo la tarea, la decisión y el motivo; se recorta a 500 líneas al pasar de 1000. Está en `.gitignore`.
- **Recuperable.** `save_skill` añade la línea `# TASK: <tarea>` y, tras guardar, comprueba que la búsqueda local encuentra la skill para esa misma tarea; si no, registra `not_recoverable`.
- **Español e inglés.** La búsqueda normaliza acentos y pasa ambos idiomas a los mismos términos con un glosario pequeño (raíces como `apag` → `disable`, `captur` → `screenshot`, frases como "turn off" → `disable`). Sin dependencias nuevas y sin red. Sobre las 95 skills reales, 14 tareas en inglés devuelven la misma skill que antes; de 14 en español, antes no encontraba ninguna y ahora 13.
- **Probado en real (Windows 11, LLM real):** "print the result of 17*23" y "imprime el resultado de 17 por 23" reutilizan la misma skill y dan 391. "set the Windows accent color to blue" devuelve `NATIVE windows_personalization` sin ejecutar nada. Con una skill que muestra la RAM y además escribe un archivo, "muestra el uso de la RAM" respondió `PARTIAL` dos veces de dos: corrió solo `ram()` y el archivo no se escribió.

### Bluetooth nativo (`actions/computer_settings.py`)
- Acciones `bluetooth_on` y `bluetooth_off`, con alias como `enable_bluetooth` o `turn_off_bluetooth`. Antes, encender o apagar el Bluetooth acababa en `solve_task`.
- **Windows:** API WinRT `Windows.Devices.Radios` desde PowerShell 5.1, sin dependencias nuevas.
- **macOS:** usa `blueutil` si está instalado; si no, lo dice.
- **Linux:** `rfkill`.
- El resultado se lee de vuelta de la radio. Si el acceso está denegado, el mensaje indica el ajuste de privacidad de Windows.
- No pide confirmación, igual que `toggle_wifi`.
- **Probado:** en Windows 11, solo `bluetooth_off` con la radio ya apagada. `bluetooth_on` no se ejecutó en real. macOS y Linux solo con tests simulados.

## Tecnologías evaluadas y descartadas

| Tecnología | Decisión | Motivo |
|---|---|---|
| LangGraph | Descartada | El bucle ReAct, la cola persistente y los resultados cubren la orquestación. Añadirla duplicaría la arquitectura y sumaría una dependencia pesada. |
| Langfuse | Descartada | Es un servicio externo y enviaría prompts, posiblemente personales, fuera del PC. Las líneas `[LLM]` dan rol, modelo y tiempo sin datos sensibles. |
| LiteLLM | Descartada | `complete()` ya cubre los 8 proveedores con sus particularidades (suscripción de Claude, esfuerzo, Gemini de texto frente a Live). |
| Pydantic AI | Descartada | `complete_json` y las comprobaciones actuales bastan, y la migración tocaría todos los motores. |
| Context7 | Integrable vía MCP | Se puede activar en `config/mcp_servers.json`. Está desactivado y no se ha probado contra el servidor real. |

## Compatibilidad de sistemas operativos
- Todos los cambios son Python puro y no usan APIs propias de ningún sistema.
- **Windows 11:** suite completa (267 OK) y pruebas reales con proveedores, herramientas y un cierre forzado (`taskkill /F`).
- **Linux:** solo la suite, en WSL Ubuntu con Python 3.11 y sin claves. 209 tests: 2 errores y 4 omitidos, todos por falta de `libportaudio2` (requisito documentado). Por eso tampoco se cargaron los 27 tests de `main.py`. No se probó la interfaz, la voz ni las herramientas reales.
- **macOS:** no probado; no había equipo disponible.

## Riesgos y límites conocidos
- **Confirmaciones del cerebro.** El cerebro podría llamar a `task_status` con `confirmed='yes'` sin que el usuario lo diga. Es el mismo nivel de confianza que el resto de sus confirmaciones.
- **Código generado.** La comprobación de riesgo es una lista de palabras clave: el código ofuscado la evita. El filtro de `generated_code` no detecta `Path.replace` ni lo que se escriba con `pyautogui`.
- **Herramientas que el agente no tiene.** Email, `file_processor` y `apple_music` siguen sin estar disponibles para el agente de tareas, igual que antes. Se dejaron así para no saltarse sus confirmaciones.
- **`REACT_PROMPT`.** Se sigue escribiendo a mano; ahora un test detecta cualquier desajuste.
- **Servidores MCP.** No pueden pedir confirmación por llamada, y los servidores HTTP que exigen cabeceras de autenticación no están soportados.
- **Reutilización de skills.**
  - La clasificación la hace el LLM: puede variar entre ejecuciones. El código decide solo lo verificable (número válido, parte separable, herramienta existente).
  - El motivo de `NOT REUSED` se comprueba en su forma, no en su verdad.
  - El glosario cubre las palabras comunes de tareas de PC; frases fuera de él ("mejora la resolución") pueden no encontrar la skill correcta. Se descartaron los embeddings: añaden una dependencia pesada o envían las tareas a un servicio.
  - La ventana de 120 s para no devolver la misma herramienta nativa es una heurística.

## Cómo desactivar o revertir
- **MCP:** borra `config/mcp_servers.json` o pon `"enabled": false`.
- **Modelos por rol:** quita `role_models` de `config/api_keys.json`.
- **Tareas guardadas:** borra `memory/tasks.json`. Solo contiene el progreso de las tareas.
- **Todo el código:** la versión anterior es la etiqueta `v2.7.1` (`git switch --detach v2.7.1`).
- **Copia previa de los datos:** `C:\Users\pablo\AtlasBackups\pre-evolve-20261029-113024.zip`. Incluye memoria, skills, configuración y sincronización, con sumas SHA-256 verificadas.

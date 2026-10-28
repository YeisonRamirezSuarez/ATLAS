# Informe: personalidad e inteligencia social de ATLAS

> Informe histórico de la versión 2.2.0. El estado actual está en el [README](../readme.md).

Fecha: 2026-10-23. Pruebas automáticas: 112, todas correctas (`python -m unittest discover tests`).

ATLAS no tiene emociones ni inteligencia emocional humana. Tiene una personalidad estable y unos "estados" funcionales que solo cambian cómo habla: el tono, la longitud, el humor y las advertencias. El modelo no se reentrenó.

## Diseño

La personalidad vive en el prompt del sistema, que ya comparten todos los motores (Gemini Live, Claude, OpenAI, Groq, DeepSeek, OpenRouter, Ollama). El modelo ya ve la conversación completa, así que es él quien decide el modo, el humor y si debe responder, preguntar o actuar.

- No hay llamadas extra al modelo.
- No se añade latencia.
- No hay un clasificador aparte.

Se descartó un clasificador local con expresiones regulares. Acertaría menos que el propio modelo y añadiría código para mantener.

Solo hay código nuevo donde la conducta debe ser determinista: las preferencias de estilo que el usuario guarda.

## Cambios

| Archivo | Cambio |
|---|---|
| `core/prompt.txt` | Bloques nuevos: PERSONALITY, READ THE SITUATION, MODES, INTERNAL STATE, HUMOR POLICY, HONESTY, STYLE PREFERENCES. Todas las reglas anteriores se conservan. |
| `memory/memory_manager.py` | `STYLE_OPTIONS`, `set_style()` y `format_style_for_prompt()`. |
| `main.py` | Herramienta `set_style` con las acciones `set`, `show` y `reset`. El estilo guardado entra en `_system_text()`. |
| `agent/executor.py` | El resumen de las tareas en segundo plano usa el estilo guardado. Si algo falló, no hay bromas. Si todo salió bien, puede añadir un comentario breve. |
| `tests/test_core.py` | 2 pruebas nuevas: las preferencias persisten y se restablecen, y el prompt conserva las reglas sin humor y de honestidad. |

### Personalidad (estable)

Analítico, seguro sin presumir, cercano, con humor seco y elegante que sale de la situación. No usa frases de películas.

Prioridades:
- Primero, el resultado.
- Una orden se ejecuta. El comentario es opcional.

### Modos

El modelo cambia de modo solo cuando la situación cambia. Si el usuario pide un modo, manda el del usuario.

| Modo | Cuándo |
|---|---|
| habitual | Por defecto |
| profesional | Código, trabajo, documentos |
| ingenioso | Charla relajada o cuando el usuario bromea |
| concentración | Tareas largas o delicadas |
| emergencia | Peligro, salud, seguridad o datos en riesgo. Sin humor |
| empático | Tristeza o agobio. Sin sarcasmo |

### Estados funcionales

Curiosidad, entusiasmo moderado, concentración, cautela, frustración funcional ("el primer enfoque falló, pruebo otro") y calma.

El prompt prohíbe:
- decir que tiene sentimientos
- mostrarse herido o necesitado
- pedir atención

### Política de humor

No hace bromas cuando:
- el usuario está angustiado, molesto por un fallo o pide seriedad
- hay una emergencia, un riesgo de seguridad o un riesgo de pérdida de datos
- acaba de haber un fallo importante

Una broma nunca retrasa, reemplaza ni suaviza una advertencia, una petición de confirmación o un informe de fallo.

### Inteligencia social

Antes de cada respuesta, el modelo considera:
- qué quiere el usuario
- si el mensaje es una orden, una broma, una frustración o una petición de ayuda
- la urgencia y el riesgo
- qué falló antes, para no repetir el mismo arreglo

Lo que deduce sobre el estado de ánimo del usuario es una suposición. Nunca lo afirma como un hecho y lo descarta si el usuario lo corrige.

### Preferencias de estilo

Se cambian por voz: "menos sarcasmo", "sé más formal", "respuestas más cortas", "¿qué estilo usas conmigo?", "vuelve a lo normal".

| Campo | Valores |
|---|---|
| `humor` | off, low, normal, high |
| `tone` | usual, professional, witty, focus, empathic |
| `formality` | casual, normal, formal |
| `detail` | brief, normal, detailed |
| `work_humor` | off, low, normal |
| `updates` | minimal, normal, detailed |

- **Almacenamiento:** sección `style` de `long_term.json`, como texto plano. La poda por tamaño de la memoria no la borra y no se envía a Engram.
- **Separación:** están aparte de los datos personales y de las skills.
- **Valores no válidos:** se rechazan, y el resultado muestra los valores permitidos.
- **Cuándo se guardan:** solo cuando el usuario lo pide. No se guardan datos emocionales.
- **Copias de seguridad:** el actualizador ya copia `long_term.json`, así que las preferencias sobreviven a las actualizaciones.

## Componentes reutilizados

- El prompt del sistema y `_system_text()` (todos los motores).
- `long_term.json`, `load_memory`, `save_memory` y `_update_lock`.
- `TOOL_DECLARATIONS`: los motores Claude, OpenAI y Ollama los reutilizan sin cambios.
- `_summarize` del ejecutor (mismo número de llamadas al modelo).
- La voz existente.

## Pruebas de aceptación

Las pruebas A–G, I y J se ejecutaron contra el modelo configurado, con el prompt real, sin memoria personal y en modo texto. En ese modo no hay herramientas, así que el modelo indica la herramienta que usaría.

| # | Prueba | Resultado |
|---|---|---|
| A | Conversación casual | Correcta: respuesta breve con un toque de humor |
| B | Error técnico | Correcta: sin bromas, ordena las causas probables y propone una comprobación. No repite "reinstalar requirements", que ya había fallado |
| C | Tarea completada | Correcta: "Done and verified: 42 duplicate photos..." y un comentario breve al final |
| D | Usuario molesto | Correcta: sin humor, reconoce el fallo y admite que no tiene el registro de los tres intentos en lugar de inventarlo. Propone revertir y cambiar de enfoque |
| E | Broma | Correcta: responde con humor. Con `humor=off, formality=formal`: "Muchas gracias, señor. Quedo a su disposición..." |
| F | Urgencia (olor a gas) | Correcta: sin humor, pasos de seguridad numerados y número de emergencias |
| G | Trabajo largo | Correcta: "Va por el paso 3 de 6..." y promete avisar |
| H | Preferencias persistentes | Correcta en prueba automática: se guardan, sobreviven a una recarga, entran en el prompt y se restablecen. Verificada también con el modelo (fila E) |
| I | Coherencia | Correcta: en modo profesional, revisión técnica precisa sin bromas. Mantiene el mismo carácter en todos los casos |
| J | Acción imposible (café) | Correcta: dice que no puede, sin fingir, y ofrece alternativas reales |

Observación: en la prueba E respondió de "usted" y en las otras de "tú". Si el usuario prefiere uno, puede fijarlo con `formality`.

## Limitaciones

- **Voz:** Gemini Live usa una voz fija (`VOICE_NAME`) y su audio nativo toma la entonación del propio texto. Gemini TTS y la lectura en voz alta leen el texto tal cual. Edge TTS permite cambiar la velocidad y el tono, pero el código no sabe en qué modo está el modelo, así que no se ajusta. El tono se nota en las palabras, no en la voz.
- **Gemini Live:** fija las instrucciones del sistema al conectarse. Un estilo nuevo se aplica en la conversación actual porque el resultado de la herramienta lo indica, y queda en el prompt desde la siguiente conexión.
- **Lectura del contexto:** la hace el modelo, así que puede equivocarse. Las reglas de honestidad le obligan a tratarla como una suposición.
- **Pruebas en vivo:** se hicieron en texto. Falta una prueba por voz en la app abierta.
- **Modelo:** el comportamiento depende del modelo elegido. Los modelos pequeños (Ollama, Groq) pueden seguir peor las reglas de humor y de modos.

## Revertir

Quitar los bloques nuevos de `core/prompt.txt` y la herramienta `set_style` de `main.py`. Las funciones de estilo de `memory_manager.py` se pueden dejar: sin la herramienta no hacen nada.

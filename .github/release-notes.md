## Descarga

| Sistema | Archivo | Instalación |
|---|---|---|
| Windows 10/11 (64 bits) | **Atlas-Setup.exe** | Ejecútalo. No necesita administrador. Si aparece SmartScreen: *Más información* > *Ejecutar de todas formas*. |
| macOS (Apple Silicon) | **Atlas.dmg** | Arrastra ATLAS a Aplicaciones. La primera vez: clic derecho > *Abrir*. |
| Linux (x64) | **Atlas-linux.tar.gz** | Instala `libportaudio2` y `libxcb-cursor0`, luego `tar -xzf Atlas-linux.tar.gz && ./Atlas/install.sh` |

Todo viene incluido: Python, librerías, Chromium y el modelo de la palabra de activación "Atlas". Los instaladores no están firmados.

Guía paso a paso y solución de problemas: [docs/instalacion.md](https://github.com/YeisonRamirezSuarez/ATLAS/blob/main/docs/instalacion.md).

## Novedades en 2.5.1

- **Una sola voz.** ATLAS ya no cambia de tono a mitad de una respuesta: la lectura usa siempre la misma sesión de voz, el módulo de visión ya no habla con su propia voz y el aviso "en eso estoy" sale de la misma voz.
- **Acento latino.** ATLAS habla español con acento latinoamericano neutro.
- **El registro dice lo que ATLAS habla.** Las respuestas interrumpidas ya no aparecen en el registro, y lo que dice antes de usar una herramienta sí aparece, sin repetirse.
- **Aviso de actualizaciones al iniciar.** Al abrir ATLAS (y luego una vez al día) revisa si hay una versión nueva de ATLAS o de sus herramientas y lo anota en el registro. No instala nada solo; si todo está al día, arranca normal.
- **Lector de WhatsApp.** ATLAS lee WhatsApp Desktop (mensajes sin leer, chats recientes, un chat por nombre) sin enviar nada.
- **Agente de programación con subagentes.** Pídele a ATLAS revisiones de código, features, correcciones o planes en una carpeta de tus proyectos: trabaja en segundo plano con Claude Code, usa tus skills (superpowers, code-review, frontend-design) y lanza sus propios subagentes. En OPERACIONES ves cada subagente en su propia fila con lo que está haciendo, y el registro completo queda en *Documentos/ATLAS agents*. Si necesita una decisión tuya, te pregunta y sigue con tu respuesta.
- **ATLAS mira tus imágenes.** Analiza capturas y fotos guardadas en el disco (archivos, carpetas o patrones como `*.png`) con su propia visión, imagen por imagen; los informes largos quedan en *Documentos/ATLAS vision reports*.
- **Tus skills de Claude Code.** ATLAS usa los skills de `~/.claude/skills` (por ejemplo find-skills) sin cargar los hooks ni los plugins que cambian su forma de hablar.
- **Carpetas confiables.** La carpeta en la que le pides trabajar queda aprobada como confiable en Claude Code, para que no ignore la configuración del proyecto.
- **Herramientas actualizadas.** engram 3.6.0.

## Correcciones

- La animación de HABLANDO se mantiene mientras ATLAS habla, aunque esté ejecutando un paso al mismo tiempo.
- Con el micrófono silenciado se siguen viendo las animaciones de HABLANDO, PENSANDO y de las tareas; SILENCIADO aparece solo cuando ATLAS está en reposo.
- Las frases que a veces quedaban en silencio ahora se vuelven a pedir.
- Si la voz dice algo distinto al texto (pasaba sobre todo al interrumpir), ATLAS lo anota en el registro y reinicia la voz para que no se repita.
- ATLAS ya no ejecuta la misma tarea dos veces a la vez ni retoma tareas que ya quedaron resueltas.
- El estado EN ESPERA se muestra en español.
- Si se desconecta el audífono o cambia la salida de audio, ATLAS sigue hablando por la salida actual en vez de quedarse mudo.
- Si el motor de voz falla, ATLAS lo anota en el registro y lo vuelve a arrancar solo.
- Una mejora de su propio código ya no cierra ATLAS a mitad de camino.
- Las tareas en segundo plano ya no creen que solo tienen las herramientas de Claude Docs.

## Limitaciones conocidas

- El inicio de sesión con Google de ATLAS ID está en modo de prueba: solo funciona con cuentas autorizadas.
- En macOS y Linux la sesión de ATLAS ID no se guarda: hay que iniciarla cada vez que abres ATLAS.

Al actualizar se conservan tus claves, tu memoria y las habilidades aprendidas. Antes de la primera ejecución de la versión nueva, ATLAS guarda una copia de seguridad de tus datos.

---
Basado en [MARK XXXIX-OR](https://github.com/FatihMakes/Mark-XXXIX-OR) de [FatihMakes](https://www.youtube.com/@FatihMakes). Licencia [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).

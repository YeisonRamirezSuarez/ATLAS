## 📥 Descarga

| Sistema | Archivo | Cómo instalar |
|---|---|---|
| 🪟 Windows 10/11 | **Jarvis-Setup.exe** | Ejecútalo. No necesita administrador. Si aparece SmartScreen: *Más información* → *Ejecutar de todas formas*. |
| 🍎 macOS | **Jarvis.dmg** | Arrastra JARVIS a Aplicaciones. La primera vez: clic derecho → *Abrir*. |
| 🐧 Linux | **Jarvis-linux.tar.gz** | `tar -xzf Jarvis-linux.tar.gz && ./Jarvis/install.sh` |

Todo viene incluido: Python, librerías, Chromium y el modelo de la palabra de activación "Jarvis". Al abrirlo, pulsa **CONFIG** y pon tu clave de API (Gemini es gratis).

Instrucciones completas en el [README](https://github.com/pablo29855/Jarvis-#readme).

## ✨ Novedades en 2.3.0

- ⚡ **Responde antes:** empieza a hablar en cuanto tiene la primera frase, sin esperar a terminar de escribir la respuesta.
- 🗣️ **Avisa antes de trabajar:** antes de una tarea lenta (buscar, ejecutar un script, mirar la pantalla) dice lo que va a hacer, así sabes que te escuchó.
- ✋ **Puedes interrumpirlo:** di "Jarvis" mientras habla o trabaja, o escríbele un mensaje. Interrumpirlo no cancela la tarea en curso: la termina en segundo plano y te avisa del resultado. Solo la cancela si se lo pides.
- 👋 **Saludo al iniciar:** si arranca con el sistema, te saluda y te propone qué hacer hoy según lo que sabe de ti. La primera vez se presenta y te hace unas preguntas para conocerte.
- 🧠 **Más autónomo:** encadena herramientas hasta terminar, busca la información que le falta y prueba otras vías si algo falla.
- 🧬 **Se mejora a sí mismo:** pídele una habilidad que no tenga y la programa en su propio código. Primero pide confirmación, hace una copia de seguridad y deshace el cambio solo si las pruebas fallan. "Deshaz el último cambio" lo revierte.
- ✅ **Confirmaciones a tu medida:** sigue pidiendo confirmación por defecto, pero puedes decirle "no me preguntes más antes de…" para un caso concreto, y "vuelve a preguntarme" para restablecerlo.
- 🎧 **Elige micrófono y altavoz:** en CONFIG puedes escoger los dispositivos de entrada y salida de audio.

### 🐞 Correcciones

- Ya no se queda atascado en "SPEAKING" sin volver a escuchar.
- Decir "Jarvis, haz X" ya no lo deja en standby hasta el siguiente "Jarvis": decide unos 0,8 s después de que dejas de hablar, aunque haya ruido de fondo, y solo ejecuta lo que dices después de "Jarvis".
- Ya no pasa a standby mientras le sigues hablando.
- "Cierra Spotify" cierra esa app por su nombre y nunca cierra a JARVIS.
- WhatsApp y otras apps de mensajería se reutilizan si ya están abiertas, en vez de abrirse varias veces.

Al actualizar se conservan tus claves, tu memoria y las habilidades aprendidas.

---
Basado en [MARK XXXIX-OR](https://github.com/FatihMakes/Mark-XXXIX-OR) de [FatihMakes](https://www.youtube.com/@FatihMakes). Licencia [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).

## Descarga

| Sistema | Archivo | Instalación |
|---|---|---|
| Windows 10/11 (64 bits) | **Jarvis-Setup.exe** | Ejecútalo. No necesita administrador. Si aparece SmartScreen: *Más información* > *Ejecutar de todas formas*. |
| macOS (Apple Silicon) | **Jarvis.dmg** | Arrastra JARVIS a Aplicaciones. La primera vez: clic derecho > *Abrir*. |
| Linux (x64) | **Jarvis-linux.tar.gz** | Instala `libportaudio2` y `libxcb-cursor0`, luego `tar -xzf Jarvis-linux.tar.gz && ./Jarvis/install.sh` |

Todo viene incluido: Python, librerías, Chromium y el modelo de la palabra de activación "Jarvis". Los instaladores no están firmados.

Guía paso a paso y solución de problemas: [docs/instalacion.md](https://github.com/pablo29855/Jarvis-/blob/main/docs/instalacion.md).

## Novedades en 2.4.0

- **JARVIS ID (opcional).** Inicia sesión con Google en CONFIG > ACCOUNT para compartir tu memoria (preferencias, proyectos, notas) entre tus equipos. Se sincroniza cada 5 minutos o con *Sync now*. Las claves de API y las contraseñas nunca salen de tu equipo. Puedes ver, renombrar y quitar tus dispositivos. JARVIS funciona igual sin iniciar sesión.
- **Ajustes renovados.** El panel CONFIG ahora tiene tres pestañas: GENERAL, AI ENGINE y ACCOUNT.
- **Tema claro y oscuro.** Se elige en GENERAL y se aplica al momento, sin reiniciar.
- **Diagnóstico al arrancar.** El registro muestra qué motor de IA está activo, el estado real de la memoria (con una prueba de escritura y lectura) y el de JARVIS ID.

## Correcciones

- Los fallos de la memoria a largo plazo (Engram) se informan como fallos, no como "no encontré nada".
- *SAVE & START* sin cambios ya no reinicia JARVIS.
- Con el motor Claude (suscripción), las llamadas internas ya no dejan conversaciones sueltas en el historial de Claude Code.

## Limitaciones conocidas

- El inicio de sesión con Google de JARVIS ID está en modo de prueba: solo funciona con cuentas autorizadas.
- En macOS y Linux la sesión de JARVIS ID no se guarda: hay que iniciarla cada vez que abres JARVIS.

Al actualizar se conservan tus claves, tu memoria y las habilidades aprendidas. Antes de la primera ejecución de la versión nueva, JARVIS guarda una copia de seguridad de tus datos.

---
Basado en [MARK XXXIX-OR](https://github.com/FatihMakes/Mark-XXXIX-OR) de [FatihMakes](https://www.youtube.com/@FatihMakes). Licencia [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).

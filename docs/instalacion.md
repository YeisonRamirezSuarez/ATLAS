# Instalar JARVIS

Esta guía es para cualquier persona, sin conocimientos técnicos. Elige tu sistema:

- [Windows](#windows)
- [macOS](#macos)
- [Linux](#linux)
- [Después de instalar](#después-de-instalar) (común a los tres)

Los instaladores están en [Releases](https://github.com/pablo29855/Jarvis-/releases). Cada versión publica los instaladores que se lograron compilar; si falta el de tu sistema, usa la versión anterior que lo tenga o [ejecuta JARVIS desde el código](../readme.md#ejecutar-desde-el-código).

---

## Windows

### Requisitos previos

- Windows 10 u 11 de 64 bits.
- Unos 2 GB libres.
- Micrófono y altavoces (opcional: también se puede escribir).
- No hace falta ser administrador ni instalar Python.

### Pasos

1. Descarga [Jarvis-Setup.exe](https://github.com/pablo29855/Jarvis-/releases/latest/download/Jarvis-Setup.exe).
2. Haz doble clic en el archivo.
3. Si aparece **"Windows protegió su PC"** (SmartScreen), pulsa **Más información** y luego **Ejecutar de todas formas**. Sale porque el instalador no está firmado.
4. Sigue el asistente. Puedes marcar **Create a desktop shortcut** para tener un acceso directo en el escritorio.
5. Al final, deja marcado **Launch JARVIS** y pulsa **Finish**.

JARVIS se instala solo para tu usuario, en `%LOCALAPPDATA%\Programs\Jarvis`.

### Cómo abrirlo

- Menú Inicio: busca **JARVIS**.
- O el acceso directo del escritorio, si lo creaste.
- Para que arranque al iniciar sesión: CONFIG > GENERAL > **Start with Windows**.

### Actualizar

Descarga el instalador nuevo y ejecútalo. Reemplaza la versión anterior y conserva tus claves y tu memoria. Antes de usar una versión nueva por primera vez, JARVIS hace una copia de seguridad de tus datos en la carpeta `backups`.

### Desinstalar

Configuración de Windows > Aplicaciones > **JARVIS** > Desinstalar. El desinstalador pregunta si también quieres borrar tus datos (claves, memoria y copias de seguridad). Si no marcas la casilla, se conservan.

### Problemas frecuentes

| Problema | Solución |
|---|---|
| No aparece "Ejecutar de todas formas" | Pulsa primero **Más información**. Si tu antivirus bloquea el archivo, revisa su cuarentena y permite `Jarvis-Setup.exe`. |
| JARVIS no te oye | Configuración de Windows > Privacidad y seguridad > **Micrófono**: activa el acceso para aplicaciones de escritorio. Revisa también el micrófono elegido en CONFIG > GENERAL. |
| Se abre y se cierra | Abre `%LOCALAPPDATA%\Programs\Jarvis\app\jarvis.log` con el Bloc de notas. La última línea suele decir qué falló. |
| Dice que falta una clave | CONFIG > AI ENGINE: pon la clave del motor elegido. |

---

## macOS

### Requisitos previos

- Mac con chip Apple (M1 o posterior). El instalador se compila para Apple Silicon; en Mac con Intel no se ha comprobado.
- Unos 2 GB libres.
- Micrófono (opcional: también se puede escribir).

### Pasos

1. Descarga [Jarvis.dmg](https://github.com/pablo29855/Jarvis-/releases/latest/download/Jarvis.dmg).
2. Abre el `.dmg` y arrastra **JARVIS** a la carpeta **Aplicaciones**.
3. La primera vez, macOS lo bloquea porque la app no está firmada:
   - Abre **Aplicaciones**, haz clic derecho sobre **JARVIS** y elige **Abrir**. Confirma con **Abrir**.
   - Si no aparece esa opción, ve a **Ajustes del Sistema > Privacidad y seguridad**, baja hasta el aviso sobre JARVIS y pulsa **Abrir igualmente**.
4. Cuando macOS pregunte por el **micrófono**, pulsa **Permitir**.

### Cómo abrirlo

- Desde **Aplicaciones** o con Spotlight (Cmd + Espacio, escribe "JARVIS").
- Para que arranque al iniciar sesión: CONFIG > GENERAL > **Start at login**.

Tus claves, tu memoria y el registro se guardan en `~/Library/Application Support/Jarvis`.

### Actualizar

Descarga el `.dmg` nuevo y arrastra JARVIS a Aplicaciones otra vez, reemplazando la anterior. Tus datos en `Application Support` se conservan.

### Desinstalar

Arrastra **JARVIS** desde Aplicaciones a la Papelera. Para borrar también tus datos, elimina la carpeta `~/Library/Application Support/Jarvis` (en Finder: menú Ir > Ir a la carpeta).

### Problemas frecuentes

| Problema | Solución |
|---|---|
| "No se puede abrir porque es de un desarrollador no identificado" | Sigue el paso 3. |
| JARVIS no te oye | Ajustes del Sistema > Privacidad y seguridad > **Micrófono**: activa JARVIS. |
| No puede mover el ratón, escribir o ver la pantalla | Ajustes del Sistema > Privacidad y seguridad: añade JARVIS en **Accesibilidad** y en **Grabación de pantalla**. Luego ciérralo y vuelve a abrirlo. |
| Se abre y se cierra | Abre `~/Library/Application Support/Jarvis/jarvis.log` con TextEdit. La última línea suele decir qué falló. |

---

## Linux

### Requisitos previos

- Distribución de 64 bits (x64) reciente con escritorio gráfico. El paquete se compila en Ubuntu; otras distribuciones con una versión de glibc igual o más nueva deberían funcionar, pero no se han comprobado.
- Unos 2 GB libres.
- Dos librerías del sistema. En Ubuntu o Debian, abre una terminal y ejecuta:

```bash
sudo apt install libportaudio2 libxcb-cursor0
```

- Opcional: `wmctrl` o `xdotool` para que JARVIS pueda enfocar y mover ventanas, y BlueZ (Bluetooth) para las luces LED.

```bash
sudo apt install wmctrl xdotool
```

### Pasos

1. Descarga [Jarvis-linux.tar.gz](https://github.com/pablo29855/Jarvis-/releases/latest/download/Jarvis-linux.tar.gz).
2. Abre una terminal en la carpeta de descargas y descomprime el archivo:

```bash
tar -xzf Jarvis-linux.tar.gz
```

3. Ejecuta el instalador (no necesita `sudo`):

```bash
./Jarvis/install.sh
```

JARVIS se copia en `~/.local/share/jarvis` y aparece en el menú de aplicaciones. Después puedes borrar el `.tar.gz` y la carpeta `Jarvis` descomprimida.

### Cómo abrirlo

- Desde el menú de aplicaciones: busca **JARVIS**.
- Para que arranque al iniciar sesión: CONFIG > GENERAL > **Start at login**.

### Actualizar

Descarga el `.tar.gz` nuevo y repite los pasos 2 y 3. Tus claves y tu memoria se conservan.

### Desinstalar

```bash
rm -rf ~/.local/share/jarvis ~/.local/share/applications/jarvis.desktop
```

Esto borra también tus claves y tu memoria.

### Problemas frecuentes

| Problema | Solución |
|---|---|
| No aparece en el menú | Cierra sesión y vuelve a entrar. |
| Se abre y se cierra | Lee `~/.local/share/jarvis/app/jarvis.log`. Si menciona `PortAudio` o `xcb`, instala las librerías de los requisitos previos. |
| Ver los errores en directo | `cd ~/.local/share/jarvis/app && ../python/bin/python3 main.py` |
| No controla el ratón ni el teclado | En sesiones Wayland, el control de teclado y ratón solo llega a ventanas X11/XWayland. Elige una sesión "Xorg" en la pantalla de inicio de sesión. |
| No enfoca ventanas | Instala `wmctrl` o `xdotool`. |

---

## Después de instalar

1. La primera vez se abren los ajustes. En **AI ENGINE** elige el motor:
   - **Gemini Live** (recomendado para empezar): crea una clave gratuita en [Google AI Studio](https://aistudio.google.com/apikey) y pégala en **Gemini key**.
   - **Claude (suscripción)**: pulsa **SIGN IN WITH CLAUDE** e inicia sesión con tu cuenta Pro o Max.
   - Otros motores: pega la clave del servicio. Ollama no necesita clave.
2. En **GENERAL** elige el tema, el micrófono y el altavoz.
3. Pulsa **SAVE & START**.
4. Di "Jarvis" y habla, o escribe en **COMMAND INPUT**.

**JARVIS ID** (pestaña ACCOUNT) es opcional. Sirve para sincronizar tu memoria entre equipos; JARVIS funciona igual sin iniciar sesión. En macOS y Linux la sesión no se guarda: hay que iniciarla de nuevo cada vez que abres JARVIS. Ver [JARVIS ID](jarvis-id-setup.md).

Tus claves se guardan solo en tu equipo y nunca se sincronizan.

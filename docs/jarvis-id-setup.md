# JARVIS ID: configuración

JARVIS ID es opcional. Sin él, JARVIS funciona igual y la memoria queda solo en este PC.
Con él, varios dispositivos comparten los datos de `memory/long_term.json` (identidad,
preferencias, proyectos, relaciones, deseos y notas). Las conversaciones, el estilo, las
habilidades aprendidas y la memoria de Engram no salen del PC. Tampoco sale nada que parezca un secreto
(contraseñas, claves, tokens, PIN).

JARVIS ID no cambia el motor de IA elegido.

**Para el usuario no hay nada que configurar.** JARVIS ya trae la URL y la clave pública del
proyecto de JARVIS (`PROJECT_URL` y `PUBLIC_KEY` en `memory/jarvis_id.py`). El usuario solo
pulsa **SIGN IN WITH GOOGLE**. Las secciones 1 y 2 son para quien mantiene el proyecto, y ya
están hechas para el proyecto `bevrstsvfikouhacidic`.

## Qué guarda JARVIS y qué no

| Dato | Dónde | Comentario |
|------|-------|------------|
| URL del proyecto y clave pública (publishable) | En el código (`memory/jarvis_id.py`) | Pública por diseño. La seguridad la da RLS en el servidor. `config/api_keys.json` puede apuntar a otro proyecto. |
| Sesión (tokens de acceso y renovación) | `memory/jarvis_id.session` | Cifrada con DPAPI de Windows: solo este usuario de Windows en este PC la descifra. Nunca se escribe en el log. |
| Cola de sincronización | `memory/sync.db` | Cambios que esperan al servidor, versiones conocidas y conflictos. |
| Secreto de cliente de Google | Solo en el panel de Supabase | JARVIS nunca lo tiene. |
| Clave `service_role` de Supabase | En ningún sitio de JARVIS | No la pegue nunca en JARVIS. |

Los tres archivos locales están excluidos de git (`.gitignore`) y del instalador.

## 1. Proyecto de Supabase

1. Cree un proyecto en <https://supabase.com/dashboard>.
2. Abra **SQL Editor**, pegue el contenido de `supabase/migrations/001_jarvis_id.sql` y ejecútelo.
   Puede ejecutarlo de nuevo sin riesgo. Crea:
   - `devices`: los dispositivos vinculados.
   - `memories`: un dato por fila, con versión y borrado lógico.
   - RLS activado y forzado en las dos tablas: cada usuario lee y escribe solo sus filas.
     La clave anónima sola no lee ni escribe nada.
3. Compruebe en **Table Editor** que las dos tablas muestran "RLS enabled".

## 2. Inicio de sesión con Google

1. En Google Cloud Console, cree un cliente OAuth de tipo **Web application**.
   - Authorized redirect URI: `https://<su-proyecto>.supabase.co/auth/v1/callback`
     (Supabase la muestra en **Authentication > Sign In / Providers > Google**).
2. En Supabase, **Authentication > Sign In / Providers > Google**: actívelo y pegue el
   Client ID y el Client Secret de Google. Ese secreto queda solo en Supabase.
3. En Supabase, **Authentication > URL Configuration > Redirect URLs**, agregue exactamente:

   ```
   http://127.0.0.1:53682/callback
   ```

   JARVIS abre un servidor local en ese puerto solo mientras usted inicia sesión
   (flujo OAuth PKCE). Si el puerto está ocupado, JARVIS lo dice.

## 3. Conectar JARVIS

No hay que pegar nada: JARVIS ya trae el proyecto. En **CONFIG > ACCOUNT** solo se puede
cambiar el nombre del dispositivo (**✎ Rename** junto a este dispositivo; por defecto el nombre del PC).
`device_id` lo crea JARVIS solo: es aleatorio, no el nombre del equipo.

Para usar otro proyecto de Supabase (por ejemplo, uno de pruebas), edite
`config/api_keys.json` a mano. Esos valores tienen prioridad sobre los del código:

```json
{
    "supabase_url": "https://<otro-proyecto>.supabase.co",
    "supabase_anon_key": "<publishable key de ese proyecto>"
}
```

Nunca ponga la clave `secret` o `service_role` ni en el código ni en ese archivo.

Después:

1. **SIGN IN WITH GOOGLE**: se abre el navegador. Al terminar, la pestaña del navegador
   muestra "SIGNED IN" y su correo (o "SIGN-IN FAILED" y el motivo). El panel muestra su
   correo y este dispositivo queda registrado.
2. El interruptor **Sync memory across devices** se activa solo al iniciar sesión (puede
   apagarlo). La primera sincronización sube los datos locales válidos. Luego JARVIS
   sincroniza cada 5 minutos, y también cuando pulsa **⟳ Sync now**.

La pestaña también tiene **Sign out**, la lista **YOUR DEVICES** (cada dispositivo ajeno tiene
**Remove**, que pide un segundo clic para confirmar) y, en **▸ Advanced · diagnostic**,
**Run diagnostic** (memoria local, Engram y estado de JARVIS ID). Los cambios de nombre se
guardan al pulsar **SAVE & START**.
El arranque escribe el mismo diagnóstico en el log.

## Cómo funciona la sincronización

- **Sin conexión**: los cambios esperan en `memory/sync.db`. Un dato cuenta como sincronizado
  solo cuando el servidor lo acepta.
- **Conflictos**: si dos dispositivos cambian el mismo dato, gana la copia que llegó primero
  al servidor. La otra se guarda en la tabla de conflictos y el panel la muestra. Nunca hay
  sobrescritura silenciosa: el servidor rechaza una versión vieja (`version conflict`).
- **Borrados**: se marcan como borrados para que los otros dispositivos también los borren.
- **Quitar un dispositivo**: desde el panel. Ese dispositivo cierra su sesión en su próxima
  sincronización.
- **Cerrar sesión**: revoca la sesión en el servidor si hay conexión. La copia local se
  borra siempre.

## Copias de seguridad y restauración

`updater.py` guarda en `backups/` la configuración, `long_term.json`, las habilidades,
`sync.db` y la sesión. JARVIS hace una copia sola cada vez que cambia de versión.

- Hacer una copia ahora: `.venv\Scripts\python.exe updater.py backup`
- Restaurar una copia: cierre JARVIS y ejecute
  `.venv\Scripts\python.exe updater.py restore backups\<archivo>.zip`.
  Antes de restaurar, JARVIS hace otra copia (`before-restore`), así que puede deshacerlo.
- Empezar de cero la sincronización en este PC: cierre JARVIS y borre `memory/sync.db`.
  La próxima sincronización vuelve a subir los datos locales.
- Revocar un dispositivo perdido: desde otro dispositivo, **Remove** en **YOUR DEVICES**. También
  puede borrar su fila en la tabla `devices` de Supabase.

## Límites conocidos

- La app de Google está en modo de prueba: solo inician sesión los usuarios de prueba que
  se añadan en Google Cloud (**Google Auth Platform > Audience**), hasta 100. Para abrirla a
  cualquier cuenta de Google, pulse **Publish app** en esa misma página.
- Plan gratuito de Supabase: el proyecto se pausa tras una semana sin actividad y hay que
  reactivarlo desde el panel.

- En macOS y Linux la sesión queda solo en memoria: al reiniciar JARVIS hay que iniciar
  sesión otra vez.
- Solo se comparten datos de `long_term.json`. La memoria de Engram de cada PC sigue siendo
  local; los datos recibidos también se guardan en el Engram del PC que los recibe.

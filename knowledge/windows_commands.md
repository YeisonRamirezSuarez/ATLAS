# Windows command reference for generated scripts

Curated from Microsoft's official documentation:
- PowerShell cmdlets: https://learn.microsoft.com/powershell/module/
- Windows Commands (cmd tools): https://learn.microsoft.com/windows-server/administration/windows-commands/windows-commands
- Settings URIs: https://learn.microsoft.com/windows/apps/develop/launch/launch-settings-app
- winget: https://learn.microsoft.com/windows/package-manager/winget/
- powercfg: https://learn.microsoft.com/windows-hardware/design/device-experiences/powercfg-command-line-options
- Windows Update Agent API: https://learn.microsoft.com/windows/win32/wua_sdk/portal-client
- UISettings: https://learn.microsoft.com/uwp/api/windows.ui.viewmanagement.uisettings

Each `## ` section is injected into the script writer's prompt when one of its keywords appears in
the task. Keep sections short: they cost prompt tokens on every matching task.

## General rules
Keywords: *
- Call PowerShell from Python as `subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
  "-Command", cmd], capture_output=True, text=True, timeout=60, creationflags=0x08000000)` (no console window).
  Always pass `timeout=`; a hung command must not freeze the task.
- Ask PowerShell for machine-readable output: `... | ConvertTo-Json -Depth 3 -Compress` (or
  `Select-Object` + `ConvertTo-Csv -NoTypeInformation`), then parse it in Python. Never parse
  localized table text: this PC's Windows is in Spanish, so labels like "Estado"/"Status" differ.
- `ConvertTo-Json` of one item is an object, of several an array: wrap with `@(...)` before piping.
- Prefer one PowerShell call that does the whole query over many small calls (each start costs ~0.5 s).
- `wmic` is deprecated and removed in recent Windows 11: use `Get-CimInstance` instead.
- Many changes need administrator rights (services, sfc, dism, powercfg /energy, netsh firewall).
  Check first with `ctypes.windll.shell32.IsUserAnAdmin()` and say so instead of failing obscurely.
- Personalization (accent color, dark/light mode, wallpaper, transparency, accent on taskbar or
  title bars) is NOT a script job: the windows_personalization tool does it. Never write the
  registry (winreg, reg.exe, Set-ItemProperty) for these.
- Slow commands (minutes): sfc /scannow, dism /restorehealth, chkdsk, Windows Update online search,
  `dism /online /get-packages`, `Get-WindowsPackage -Online`, `Get-AppxPackage -AllUsers`,
  `powercfg /energy` (60 s by design). Use the fast alternative listed in each section when it answers the question.

## Processes and apps running
Keywords: process, proceso, running, ejecut, task manager, administrador de tareas, cpu, memory, memoria, ram, kill, cerrar, close, programa abierto, open app
- List: `Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,Id,CPU,@{n='MB';e={[int]($_.WorkingSet64/1MB)}} | ConvertTo-Json`
- Windows with a title (apps the user sees): `Get-Process | Where-Object MainWindowTitle | Select Name,Id,MainWindowTitle`
- One process: `Get-Process -Name chrome -ErrorAction SilentlyContinue` (name without .exe).
- cmd: `tasklist /fo csv /nh` (CSV is easy to parse), `tasklist /fi "imagename eq chrome.exe"`.
- Stop: `Stop-Process -Name notepad` / `taskkill /im notepad.exe` (add `/f` to force, `/t` for children).
  Ending processes is destructive: the script is marked dangerous and needs the user's yes.
- In Python `psutil` is installed and faster than spawning PowerShell: `psutil.process_iter(['name','pid','memory_info'])`.
- CPU/RAM totals: `psutil.cpu_percent(interval=1)`, `psutil.virtual_memory()`.

## Services
Keywords: service, servicio, spooler, sc, daemon
- `Get-Service | Where-Object Status -eq Running | Select Name,DisplayName,Status,StartType | ConvertTo-Json`
- `Get-Service -Name Spooler`; start/stop/restart: `Start-Service`, `Stop-Service`, `Restart-Service` (admin).
- cmd: `sc query <name>`, `sc qc <name>` (config), `sc start|stop <name>` (admin).
- Startup type: `Set-Service -Name X -StartupType Manual` (admin, dangerous: confirm first).

## Hardware, system info and drivers
Keywords: hardware, cpu, procesador, gpu, graphics, grafica, ram, bios, motherboard, placa, disk, disco, ssd, battery, bateria, device, dispositivo, driver, controlador, usb, specs, especificaciones, system info, version de windows, windows version, uptime, temperature
- OS: `Get-CimInstance Win32_OperatingSystem | Select Caption,Version,BuildNumber,LastBootUpTime,OSArchitecture`
- CPU: `Get-CimInstance Win32_Processor | Select Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed`
- GPU: `Get-CimInstance Win32_VideoController | Select Name,DriverVersion,AdapterRAM` (AdapterRAM caps at 4 GB).
- RAM modules: `Get-CimInstance Win32_PhysicalMemory | Select Capacity,Speed,Manufacturer`
- Disks: `Get-PhysicalDisk | Select FriendlyName,MediaType,Size,HealthStatus`; volumes and free space:
  `Get-Volume | Select DriveLetter,FileSystemLabel,Size,SizeRemaining` (or Python `shutil.disk_usage`).
- BIOS/board: `Get-CimInstance Win32_BIOS`, `Get-CimInstance Win32_BaseBoard`.
- Battery: `Get-CimInstance Win32_Battery | Select EstimatedChargeRemaining,BatteryStatus` (or `psutil.sensors_battery()`).
- Devices: `Get-PnpDevice -PresentOnly | Where-Object Status -ne OK` (problem devices),
  `Get-PnpDevice -Class Bluetooth,Camera,AudioEndpoint`. Enable/disable: `Enable-PnpDevice/Disable-PnpDevice -InstanceId X -Confirm:$false` (admin).
- Drivers: `Get-CimInstance Win32_PnPSignedDriver | Select DeviceName,DriverVersion,DriverDate` (a few seconds), `pnputil /enum-drivers`.
- Avoid `systeminfo` when one value is needed: it takes several seconds and is localized text.
- Temperatures are not exposed reliably by Windows (MSAcpi_ThermalZoneTemperature needs admin and is often absent): say so.

## Event logs and crashes
Keywords: event, evento, event log, crash, bluescreen, pantalla azul, bsod, reinicio inesperado, unexpected restart, shutdown history, apagado
- Always filter at the source with -FilterHashtable (keys: LogName, ProviderName, Id, Level, StartTime, EndTime)
  and -MaxEvents: piping every event to Where-Object is very slow.
- Recent errors: `Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 30 | Select TimeCreated,Id,ProviderName,Message`
- Unexpected shutdowns: System Id 41 (Kernel-Power), 6008 (dirty shutdown); BSOD: Id 1001 (BugCheck).
- App crashes: `@{LogName='Application'; Id=1000}`.
- Boot/shutdown times: System Id 6005 (log started), 6006 (stopped), 1074 (who requested restart).
- Messages are localized; use Id and ProviderName for logic, Message only for display.

## Network
Keywords: network, wifi, wi-fi, internet, ip, dns, ethernet, adapter, adaptador, ping, port, puerto, firewall, proxy, vpn, bandwidth, velocidad, conexion, connection
- Adapters: `Get-NetAdapter | Select Name,InterfaceDescription,Status,LinkSpeed,MacAddress`
- IPs: `Get-NetIPAddress -AddressFamily IPv4 | Select InterfaceAlias,IPAddress`; all at once: `Get-NetIPConfiguration`.
- Reachability: `Test-NetConnection host -Port 443` (TcpTestSucceeded) or `Test-Connection host -Count 2` (PS 5.1: -Quiet for bool).
- DNS: `Resolve-DnsName name`, flush: `Clear-DnsClientCache` / `ipconfig /flushdns`.
- `ipconfig /all`, `ipconfig /release` + `/renew` (drops the connection briefly).
- Wi-Fi: `netsh wlan show interfaces` (current SSID, signal), `netsh wlan show profiles`,
  `netsh wlan connect name="<profile>"`. A profile's saved key is a secret: never print it unless the user asks.
- Open connections: `Get-NetTCPConnection -State Listen,Established | Select LocalPort,RemoteAddress,OwningProcess`.
- Firewall: `Get-NetFirewallProfile | Select Name,Enabled`; rules need admin and confirmation (`New-NetFirewallRule`).
- Public IP: HTTPS request to https://api.ipify.org (needs internet).

## Installed software, packages and Store apps
Keywords: install, instalar, uninstall, desinstalar, program, programa, software, aplicacion, installed apps, winget, upgrade, actualizar programas, store, tienda, appx, version
- winget (fast, best first choice): `winget list --accept-source-agreements --disable-interactivity`,
  `winget upgrade` (what can be updated), `winget search <name>`, `winget install --id <Id> -e --silent --accept-package-agreements --accept-source-agreements`,
  `winget upgrade --all --silent`. Output is a localized table: match on the Id column; installing is dangerous (confirm).
- Installed (registry uninstall keys, read-only and fast):
  `Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*, HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*, HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Where DisplayName | Select DisplayName,DisplayVersion,Publisher`
- Avoid `Get-CimInstance Win32_Product`: very slow and it triggers MSI self-repair on every product.
- Store/UWP apps: `Get-AppxPackage -Name *Spotify*` (current user, fast); `-AllUsers` needs admin and is slow.
  Launch one: `explorer.exe shell:AppsFolder\<PackageFamilyName>!App` (see `Get-StartApps` for AppIDs).
- Startup apps: `Get-CimInstance Win32_StartupCommand | Select Name,Command,Location`; Settings: ms-settings:startupapps.

## Windows Update and hotfixes
Keywords: windows update, actualizacion de windows, update, actualizacion, parche, patch, hotfix, kb
- `Get-WindowsUpdate` is NOT built in (it comes from the PSWindowsUpdate module); do not assume it exists.
- Installed updates, fast: `Get-HotFix | Sort-Object InstalledOn -Descending | Select -First 10 HotFixID,Description,InstalledOn`.
- Update history, fast and offline (COM Microsoft.Update.Session):
  `$s=New-Object -ComObject Microsoft.Update.Session; $h=$s.CreateUpdateSearcher(); $h.QueryHistory(0,[Math]::Min(20,$h.GetTotalHistoryCount())) | Select Date,Title,ResultCode`
  (ResultCode 2 = succeeded, 4 = failed, 5 = aborted).
- Pending updates, SLOW (online search, 30 s to several minutes): `$h.Search("IsInstalled=0 and IsHidden=0").Updates | Select Title,KBArticleIDs`.
  Run it with a timeout of at least 300 s and tell the user it is checking online.
- Reboot pending: `Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'` (read-only).
- Open the page for the user instead of installing: `start ms-settings:windowsupdate` (or ms-settings:windowsupdate-action to start checking).
- Avoid `dism /online /get-packages` or `Get-WindowsPackage -Online` to list updates: slow; Get-HotFix answers it.

## Scheduled tasks
Keywords: scheduled task, tarea programada, schedule, programar, schtasks, task scheduler, programador
- List: `Get-ScheduledTask | Where-Object State -ne Disabled | Select TaskName,TaskPath,State`
- Details/last result: `Get-ScheduledTaskInfo -TaskName X` (LastRunTime, LastTaskResult, NextRunTime).
- cmd: `schtasks /query /fo csv /nh` (CSV), `schtasks /query /tn "<name>" /v /fo list`, `schtasks /run /tn "<name>"`.
- Create: `schtasks /create /tn "<name>" /tr "<command>" /sc daily /st 09:00` or
  `Register-ScheduledTask` with New-ScheduledTaskAction/Trigger. Creating or deleting tasks is dangerous (confirm).
- For a simple one-off reminder use the reminder tool, not a scheduled task.

## Power, sleep and battery
Keywords: power, energia, sleep, suspender, hibernate, hibernar, battery, bateria, power plan, rendimiento, performance, shutdown, apagar, restart, reiniciar, timer, temporizador, lid
- Plans: `powercfg /list`, `powercfg /getactivescheme`, `powercfg /setactive <GUID>`
  (built-ins: SCHEME_MIN = high performance, SCHEME_BALANCED, SCHEME_MAX = power saver).
- Timeouts (minutes): `powercfg /change monitor-timeout-ac 10`, `standby-timeout-ac`, `-dc` for battery.
- Battery report (HTML, a few seconds): `powercfg /batteryreport /output "%USERPROFILE%\Desktop\battery.html"`.
- What blocks sleep: `powercfg /requests` (admin); last wake source: `powercfg /lastwake`.
- `powercfg /energy` always takes 60 s and needs admin; `powercfg /sleepstudy` for Modern Standby.
- Hibernate on/off: `powercfg /hibernate on|off` (admin).
- shutdown: `shutdown /s /t 60` (shut down in 60 s), `/r` restart, `/h` hibernate, `/l` sign out,
  `/a` aborts a pending shutdown, `/f` forces apps closed (unsaved work lost). Restart/shutdown need the user's yes:
  computer_settings already handles them with confirmation.

## System repair and disk health
Keywords: repair, reparar, sfc, dism, corrupt, corrupto, chkdsk, disk health, salud del disco, integrity, integridad, component store
- `sfc /scannow` (admin, 5-20 min): run with `timeout=1800`, report the final line only.
- DISM (admin): `DISM /Online /Cleanup-Image /CheckHealth` (seconds) first; `/ScanHealth` (minutes);
  `/RestoreHealth` (10-30+ min, needs Windows Update or /Source). Use timeouts >= 3600 s for RestoreHealth.
- Disk check without reboot: `chkdsk C: /scan` (admin, online); `/f` on C: only schedules for next boot.
  Volume health quickly: `Get-Volume -DriveLetter C | Select HealthStatus` / `Repair-Volume -DriveLetter C -Scan`.
- SMART-ish status: `Get-PhysicalDisk | Select FriendlyName,HealthStatus,OperationalStatus`.
- Temp cleanup: `cleanmgr /sagerun:1` needs prior /sageset; deleting %TEMP% files is dangerous (confirm).

## Files, search and disk usage
Keywords: file, archivo, folder, carpeta, size, tamano, largest, grande, search, buscar, find, encontrar, duplicate, duplicado, space, espacio, zip, compress, comprimir
- Python's pathlib/os.scandir is faster than PowerShell for walking folders; skip PermissionError.
- Biggest files: `Get-ChildItem C:\Users\pablo -Recurse -File -ErrorAction SilentlyContinue | Sort Length -Descending | Select -First 20 FullName,Length` (slow on whole drives: limit the folder).
- Zip: Python `shutil.make_archive` / `zipfile`; PowerShell `Compress-Archive -Path X -DestinationPath Y.zip`.
- Recycle bin: send2trash is installed (recoverable delete); `Clear-RecycleBin -Force` is destructive.
- OneDrive folders can be cloud-only placeholders: reading them downloads the file first.

## Audio, display and devices settings
Keywords: audio, sonido, sound, speaker, altavoz, microphone, microfono, display, pantalla, monitor, resolution, resolucion, brightness, brillo, night light, luz nocturna, bluetooth, printer, impresora, camera, camara
- Volume/brightness/mute: the computer_settings tool already does these.
- Audio devices: `Get-PnpDevice -Class AudioEndpoint -PresentOnly`; pycaw (installed) for per-app volume in Python.
- Brightness (laptops): `(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness`.
- Resolution/monitors: `Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBasicDisplayParams`,
  Python `ctypes.windll.user32.EnumDisplaySettingsW`; changing resolution: ChangeDisplaySettingsExW (risky, confirm).
- Printers: `Get-Printer | Select Name,PrinterStatus,Default`, `Get-PrintJob -PrinterName X`.
- Bluetooth radio state is not scriptable without WinRT (Windows.Devices.Radios): open ms-settings:bluetooth if needed.

## Settings pages (ms-settings: URIs)
Keywords: settings, configuracion, ajustes, open settings, abrir configuracion, ms-settings, control panel, panel de control
- Open with `os.startfile("ms-settings:<page>")` or `start ms-settings:<page>`. Opening a page changes nothing by itself.
- System: display, sound, notifications, quiethours (focus), powersleep, batterysaver, storagesense,
  multitasking, about, clipboard, remotedesktop, recovery, troubleshoot, project, nightlight.
- Devices: bluetooth, connecteddevices, printers, mousetouchpad, devices-touchpad, typing, pen, autoplay, usb, camera.
- Network: network-status, network-wifi, network-ethernet, network-vpn, network-proxy, network-mobilehotspot, network-airplanemode.
- Personalization: personalization, personalization-background, colors (personalization-colors), themes,
  lockscreen, personalization-start, taskbar, fonts.
- Apps: appsfeatures, defaultapps, startupapps, optionalfeatures, appsforwebsites, videoplayback.
- Accounts: yourinfo, emailandaccounts, signinoptions, otherusers, sync, backup.
- Time/language: dateandtime, regionformatting, regionlanguage, speech, keyboard.
- Gaming: gaming-gamebar, gaming-gamedvr, gaming-gamemode.
- Accessibility: easeofaccess-display, easeofaccess-mousepointer, easeofaccess-colorfilter, easeofaccess-narrator, easeofaccess-magnifier.
- Privacy: privacy, privacy-microphone, privacy-webcam, privacy-location, privacy-backgroundapps.
- Update/security: windowsupdate, windowsupdate-history, windowsupdate-options, windowsdefender, activation, findmydevice, developers.
- Classic panels: `control` (Control Panel), `ncpa.cpl` (network connections), `appwiz.cpl` (programs),
  `mmsys.cpl` (sound devices), `sysdm.cpl` (system properties), `devmgmt.msc`, `diskmgmt.msc`, `services.msc`, `taskschd.msc`, `eventvwr.msc`.

## COM and WinRT APIs worth knowing
Keywords: com object, comobject, winrt, accent, theme, tema, color, wallpaper, fondo, uisettings, dark mode, modo oscuro, update session, shell, shortcut, acceso directo, notification, notificacion, toast
- Personalization goes to the windows_personalization tool (theme engine + verified read-back), not to scripts.
- Read the live accent/background colors (read-only) via WinRT UISettings from PowerShell 5.1:
  `$null=[Windows.UI.ViewManagement.UISettings,Windows.UI.ViewManagement,ContentType=WindowsRuntime]; (New-Object Windows.UI.ViewManagement.UISettings).GetColorValue('Accent')`
  (`'Background'` black = dark apps mode; `.AdvancedEffectsEnabled` = transparency).
- Wallpaper: IDesktopWallpaper (CLSID C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD, per monitor) or
  `ctypes.windll.user32.SystemParametersInfoW(0x14, 0, path, 3)` (SPI_SETDESKWALLPAPER) and 0x73 to read it back.
- Windows Update: `Microsoft.Update.Session` (see Windows Update section).
- Shortcuts (.lnk): `$w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut("$env:USERPROFILE\Desktop\X.lnk"); $l.TargetPath='C:\...'; $l.Save()`.
- Known folders: `[Environment]::GetFolderPath('Desktop')` (handles OneDrive-redirected Desktop/Documents).
- Shell verbs / recycle bin: `Shell.Application` COM (`$sh.Namespace(10)` = Recycle Bin).
- Toast notifications: win10toast is installed (`ToastNotifier().show_toast(title, msg, duration=5, threaded=True)`).
- Clipboard: Python pyperclip (installed) or `Get-Clipboard` / `Set-Clipboard`.

## Users, security and Defender
Keywords: user, usuario, account, cuenta, admin, administrador, defender, antivirus, virus, scan, escanear, bitlocker, security, seguridad, password, contrasena
- Current user/admin: `whoami`, `whoami /groups`; Python `ctypes.windll.shell32.IsUserAnAdmin()`.
- Local users: `Get-LocalUser | Select Name,Enabled,LastLogon` (creating/removing users: dangerous, confirm).
- Defender status: `Get-MpComputerStatus | Select AMRunningMode,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated`.
- Scans: `Start-MpScan -ScanType QuickScan` (minutes; timeout >= 1800), signatures: `Update-MpSignature`.
  Recent detections: `Get-MpThreatDetection`. Never change Defender preferences (Set-MpPreference) without a clear yes.
- BitLocker: `Get-BitLockerVolume` (admin) / `manage-bde -status`. Never print recovery keys unless asked.

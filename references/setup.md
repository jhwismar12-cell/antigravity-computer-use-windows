# Instalación del conector MCP

La instalación global es `~/.gemini/config/skills/computer-use-windows/`. El servidor se registra en `~/.gemini/config/mcp_config.json`, la ubicación documentada por [Antigravity](https://antigravity.google/docs/mcp).

`install.ps1` crea un entorno Python aislado dentro de la skill, instala las dependencias de `requirements-mcp.txt`, comprueba la conexión stdio y añade únicamente el servidor `computer-use-windows` al registro. Conserva los demás servidores y guarda una copia exacta del registro anterior en su misma carpeta. No crea reglas Allow ni cambia permisos globales, sandbox o configuración de Windows.

Desde una terminal normal de Windows, ejecuta:

```powershell
& 'C:\ruta\computer-use-windows\install.ps1' -Python 'C:\ruta\python.exe'
```

Para actualizar una instalación existente:

```powershell
& 'C:\ruta\computer-use-windows\install.ps1' -Python 'C:\ruta\python.exe' -Update
```

Requiere Python 3.11+ y acceso a PyPI durante la instalación. Usa el SDK oficial MCP 1.30.0 (línea 1.x) y Pillow. El Python global queda sin cambios. El servidor se comunica por stdio: no abre puertos ni instala servicios o tareas de inicio.

Después actualiza `computer-use-windows` en el administrador MCP de Antigravity y abre un chat nuevo. Si la versión no recarga la configuración, reinicia Antigravity. Pide:

> Usa las herramientas MCP de computer-use-windows. Ejecuta desktop_diagnose y desktop_windows, sin hacer clic ni escribir.

Una vez que se vea la ventana de destino, pide una interacción concreta y verifica el resultado en la imagen devuelta.

## Instalación manual

Si tu política no permite ejecutar el instalador PowerShell, coloca la carpeta completa de la skill en `%USERPROFILE%\.gemini\config\skills\computer-use-windows` y abre una terminal normal de PowerShell en esa carpeta. Conserva las políticas de seguridad que administre tu organización.

Ejecuta estos comandos uno por uno y continúa solo si el anterior termina correctamente:

```powershell
$cuDir = (Get-Location).Path
python -m venv .venv
$cuPython = Join-Path $cuDir '.venv\Scripts\python.exe'
& $cuPython -m pip install -r requirements-mcp.txt
& $cuPython -B scripts\smoke_mcp.py
$cuConfig = Join-Path $env:USERPROFILE '.gemini\config\mcp_config.json'
& $cuPython -B scripts\configure_mcp.py --config $cuConfig --skill $cuDir --python $cuPython
```

Después actualiza el servidor MCP en Antigravity o reinicia la aplicación y abre un chat nuevo.

## Si no conecta

1. Confirma que el registro usa `.venv\Scripts\python.exe` y `scripts\mcp_server.py` de esta instalación, no `desktop.py` como servidor.
2. Revisa el estado y los errores de esa conexión en el administrador MCP.
3. `runtime/mcp-startup.json` registra la identidad del proceso lanzado por el cliente y si coincide con el escritorio de entrada. No incluye capturas, títulos de ventanas ni texto escrito.
4. Si el host MCP también lo inicia en `exebox-...`, la skill debe detenerse. Sigue [permissions.md](permissions.md); no reubiques el hilo mediante APIs de Windows ni crees un servicio alternativo para evadir el límite.

Para comprobar el protocolo desde un contexto autorizado del host:

```powershell
& 'C:\ruta\computer-use-windows\.venv\Scripts\python.exe' 'C:\ruta\computer-use-windows\scripts\smoke_mcp.py'
```

La prueba realiza handshake, lista herramientas, consulta diagnóstico y verifica el rechazo de un estado inventado. No cambia foco ni envía entradas a aplicaciones. Que pase desde una terminal normal no demuestra que Antigravity haya iniciado su propia conexión en ese mismo escritorio.

## Motor CLI

`scripts/desktop.py` sigue disponible para diagnósticos locales, por ejemplo `diagnose --report <archivo.json>`. En terminales aisladas no sustituye al conector MCP. Las capturas temporales del servidor se guardan en una carpeta privada de sesión bajo el directorio temporal del proceso; pueden incorporarse al contexto del modelo al devolverlas como imágenes MCP.

# Computer Use para Antigravity en Windows

Skill y conector MCP local para observar ventanas y manejar ratón y teclado desde Antigravity. Incluye capturas devueltas como imágenes, clics, arrastre, scroll y escritura Unicode con tildes y ñ.

**Versión 2.0.0.** Es una integración comunitaria que usa las APIs de Windows y el SDK oficial MCP. La conexión y las pruebas se verificaron localmente; cada instalación debe comprobar el contexto de escritorio que recibe de Antigravity.

## Instalación rápida

Necesitas Windows 10/11, Python 3.11 o posterior, Antigravity con soporte MCP y una sesión de Windows desbloqueada. La instalación descarga dependencias de PyPI.

1. [Descarga computer-use-windows.zip](https://github.com/jhwismar12-cell/antigravity-computer-use-windows/releases/latest/download/computer-use-windows.zip).
2. Extrae el ZIP y abre una terminal normal de PowerShell **en la carpeta que contiene `install.ps1`**.
3. Ejecuta:

   ```powershell
   python --version
   .\install.ps1 -Python python
   ```

4. En Antigravity, actualiza los servidores MCP o reinicia la aplicación. Abre un chat nuevo.
5. Pega:

   > Usa las herramientas MCP del servidor computer-use-windows. Ejecuta desktop_diagnose y desktop_windows, sin hacer clic ni escribir y sin usar la terminal.

El instalador copia la skill a `%USERPROFILE%\.gemini\config\skills\computer-use-windows`, crea un entorno Python propio y registra el servidor en `%USERPROFILE%\.gemini\config\mcp_config.json`. Guarda una copia del registro anterior y conserva los demás servidores y políticas de permisos.

Para actualizar, ejecuta `./install.ps1 -Python python -Update` desde la carpeta de la nueva versión. Si tu política de scripts impide ejecutar `.ps1`, usa la [instalación manual](references/setup.md#instalación-manual) con Python.

## Cómo usarlo

Tras el diagnóstico, pide una tarea concreta:

> Localiza la Calculadora que tengo abierta, toma una captura y dime qué muestra.

Después:

> En esa Calculadora, introduce 7 + 5 y verifica que el resultado es 12.

La skill hace que el agente mire cada captura antes de actuar. Cada acción usa un `state_id` reciente, válido para una sola entrada, y devuelve una imagen nueva.

## Herramientas MCP

- `desktop_diagnose`: diagnóstico del proceso y escritorio.
- `desktop_windows`: ventanas con HWND, título, PID y ejecutable opcional.
- `desktop_observe` y `desktop_focus`: capturar o activar y capturar una ventana.
- `desktop_click`, `desktop_move`, `desktop_drag` y `desktop_scroll`: ratón.
- `desktop_key` y `desktop_type`: teclas, atajos y texto Unicode.
- `desktop_stop`: detener nuevas entradas para esa conexión.

El transporte es stdio: el cliente inicia el servidor local y se comunica por sus canales de entrada/salida. No requiere claves de OpenAI ni abre un puerto de red.

## Por qué usa MCP

Una terminal de Antigravity puede seguir asociada a un escritorio `exebox-...` incluso después de aprobar `BypassSandbox: true`. Ese proceso no tiene el mismo contexto que el escritorio interactivo `Default`.

El conector usa la integración MCP de Antigravity y comprueba su propio escritorio antes de operar. Si también está aislado, devuelve `DESKTOP_CONTEXT_MISMATCH` y se detiene. No cambia de escritorio ni desactiva el sandbox. Mira [permisos y diagnóstico](references/permissions.md) si aparece ese error.

## Límites y parada

Captura los píxeles visibles de la ventana. Las superposiciones, aplicaciones elevadas, escritorios remotos o contenido protegido pueden impedir la interacción. El cliente necesita mostrar las imágenes al modelo. No incluye OCR ni árbol de accesibilidad.

Para detener el siguiente paso, mantén **ESC**, mueve el puntero a una esquina del monitor principal o pide que se ejecute `desktop_stop`. Una entrada ya entregada a Windows no se puede retirar.

Las capturas se guardan temporalmente en el equipo y se devuelven al modelo como contenido MCP. El repositorio no incluye capturas, entornos Python ni configuraciones personales.

## Pruebas

Desde la carpeta instalada, ejecuta:

```powershell
& .\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p 'test_*.py'
& .\.venv\Scripts\python.exe -B scripts\smoke_mcp.py
```

Las 30 pruebas unitarias verifican estados, coordenadas, Unicode, errores nativos, aislamiento y conservación de la configuración. Las entradas de aplicación se simulan. La prueba MCP verifica conexión, descubrimiento y diagnóstico, sin enviar clics ni texto a aplicaciones.

## Documentación

- [Skill para el agente](SKILL.md).
- [Instalación y reparación](references/setup.md).
- [Escritorios y permisos](references/permissions.md).
- [Integración MCP de Antigravity](https://antigravity.google/docs/mcp).

# Escritorios y permisos

El dato decisivo es la identidad del escritorio del proceso. Un hilo en `exebox-...` y el escritorio de entrada `Default` son contextos distintos. Una aprobación de terminal puede permitir el comando y aun así conservar esa separación. No atribuyas automáticamente todos los fallos a AppContainer o UIPI.

## Vía soportada

Usa el servidor local registrado mediante la integración MCP oficial de Antigravity. El cliente inicia el proceso stdio y aplica sus permisos MCP a las llamadas. Conserva las políticas existentes y cualquier regla Deny. No conviertas una lectura autorizada en permiso para realizar clics o transmitir datos.

Antes de cada operación, el helper comprueba que el escritorio de su hilo coincide con el escritorio interactivo `Default`. El diagnóstico sigue disponible para explicar un rechazo. Si el servidor también tiene un escritorio aislado, devuelve `DESKTOP_CONTEXT_MISMATCH` y no cambia foco ni envía entradas.

No intentes solucionar ese rechazo con `SetThreadDesktop`, `SwitchDesktop`, enumeración de otro escritorio, un proceso lanzado con otro `lpDesktop`, un servicio, una tarea programada o un cambio a Turbo. Tampoco repitas la misma solicitud de terminal con otra forma de shell. La integración MCP es una herramienta instalada y aprobada por el cliente; no es una autorización para atravesar cualquier restricción que siga aplicándose.

## Diagnóstico de la conexión que falla

Ejecuta la herramienta MCP `desktop_diagnose`. Conserva su JSON aunque `isError` sea verdadero. Incluye versión/hash del helper, Python, sesión, estación, escritorios, token AppContainer y resultados de enumeración. El registro `runtime/mcp-startup.json` muestra también la identidad del proceso iniciado por el cliente, antes de recibir herramientas de entrada.

Interpreta los campos así:

- `DESKTOP_CONTEXT_MISMATCH`: escritorio de hilo diferente al de entrada. La conexión no está habilitada para el escritorio interactivo.
- `WINDOW_ENUMERATION_NATIVE_FAILED`: la API devolvió falso o falló la llamada; mira `last_error` y `native_exception`.
- `last_error: 5`: la API informó acceso denegado. Respeta ese límite.
- Retorno falso y `last_error: 0`: no hubo error extendido. Por sí solo no identifica la causa.
- `WINDOW_ENUMERATION_CALLBACK_FAILED`: mira el traceback de `callback_errors`. El callback solo recoge HWND; las consultas de metadatos ocurren después.
- `native_result: true`, `enumerated: 0`: no se invocó el callback. No demuestra que el escritorio real carezca de ventanas.
- `metadata_errors > 0`: mira las muestras; una ventana que desaparece no debe cancelar las demás.
- `exe: null`: no se pudo leer la ruta del proceso. No invalida por sí sola un título, HWND y PID identificables.

No declares arreglada una conexión de Antigravity porque el helper funcione desde otro cliente. Verifica `desktop_diagnose` y `desktop_windows` a través de la conexión real de Antigravity antes de interactuar.

## Fuentes

- [Antigravity: MCP, configuración stdio y permisos](https://antigravity.google/docs/mcp).
- [Antigravity: permisos y evaluación Ask/Allow/Deny](https://antigravity.google/docs/permissions).
- [Microsoft: EnumWindows y sus códigos de retorno](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumwindows).
- [Microsoft: escritorios de Windows](https://learn.microsoft.com/en-us/windows/win32/winstation/desktops).
- [Python: ctypes, callbacks y manejo de errores](https://docs.python.org/3/library/ctypes.html#callback-functions).

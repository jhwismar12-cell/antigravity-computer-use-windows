[CmdletBinding()]
param(
    [string]$DestinationRoot = (Join-Path $env:USERPROFILE '.gemini\config\skills'),
    [string]$McpConfig = (Join-Path $env:USERPROFILE '.gemini\config\mcp_config.json'),
    [string]$Python = 'python',
    [switch]$Update
)
$ErrorActionPreference = 'Stop'
$cuSource = [IO.Path]::GetFullPath($PSScriptRoot)
$cuTarget = [IO.Path]::GetFullPath((Join-Path $DestinationRoot 'computer-use-windows'))
if ((Test-Path -LiteralPath $cuTarget) -and -not $Update) {
    throw "La skill ya existe. Usa -Update para actualizar esta instalacion: $cuTarget"
}
if ((Test-Path -LiteralPath $cuTarget) -and $Update) {
    $cuManifest = Join-Path $cuTarget 'SKILL.md'
    if (-not (Test-Path -LiteralPath $cuManifest) -or -not (Select-String -LiteralPath $cuManifest -Pattern '^name: computer-use-windows$' -Quiet)) {
        throw 'La carpeta existente no se identifica como esta skill. No se sobrescribe.'
    }
}
& $Python -c "import sys; assert sys.version_info >= (3,11); print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'Se requiere Python 3.11 o posterior.' }
New-Item -ItemType Directory -Force -Path $cuTarget | Out-Null
if ($cuSource -ne $cuTarget) {
    foreach ($cuEntry in @('SKILL.md','requirements.txt','requirements-mcp.txt','install.ps1')) {
        Copy-Item -LiteralPath (Join-Path $cuSource $cuEntry) -Destination (Join-Path $cuTarget $cuEntry) -Force
    }
    foreach ($cuFolder in @('scripts','references')) {
        $cuFolderTarget = Join-Path $cuTarget $cuFolder
        New-Item -ItemType Directory -Force -Path $cuFolderTarget | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $cuSource $cuFolder) -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $cuFolderTarget $_.Name) -Force
        }
    }
}
$cuRuntime = Join-Path $cuTarget '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $cuRuntime)) {
    & $Python -m venv (Join-Path $cuTarget '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno Python aislado.' }
}
& $cuRuntime -m pip install --disable-pip-version-check -r (Join-Path $cuTarget 'requirements-mcp.txt')
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias MCP.' }
& $cuRuntime -B (Join-Path $cuTarget 'scripts\smoke_mcp.py')
if ($LASTEXITCODE -ne 0) { throw 'La comprobacion del protocolo MCP fallo; no se modifico la configuracion MCP.' }
# Register only after the runtime and protocol have been checked.
& $cuRuntime -B (Join-Path $cuTarget 'scripts\configure_mcp.py') --config $McpConfig --skill $cuTarget --python $cuRuntime
if ($LASTEXITCODE -ne 0) { throw 'No se pudo registrar el servidor MCP; revisa el error anterior.' }
Write-Output 'Skill y conector MCP instalados. Actualiza los servidores MCP en Antigravity y abre un chat nuevo.'

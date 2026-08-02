<#
.SYNOPSIS
    Bootstrap entry point for the mineru-local skill on Windows.
.PARAMETER SkillRoot
    Optional skill folder. Defaults to the directory containing this script.
#>
[CmdletBinding()]
param(
    [string]$SkillRoot
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$here = Split-Path -Parent $PSCommandPath
$wrapper = Join-Path $here "scripts\lib_install.py"

if ($SkillRoot) {
    & python "$wrapper" install --skill-root "$SkillRoot"
} else {
    & python "$wrapper" install
}
exit $LASTEXITCODE
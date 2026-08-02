<#
.SYNOPSIS
    Strict-validate the mineru-local skill (only runs quick_validate.py).
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
    & python "$wrapper" validate --skill-root "$SkillRoot"
} else {
    & python "$wrapper" validate
}
exit $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
git check-ignore -q .oci/config
if ($LASTEXITCODE) { throw 'OCI configuration must be ignored' }
git check-ignore -q .source/test/
if ($LASTEXITCODE) { throw 'Source corpus must be ignored' }
sentrux gate --save .
if ($LASTEXITCODE) { throw 'Architecture baseline failed' }
if (Test-Path .sentrux/rules.toml) { sentrux check .; if ($LASTEXITCODE) { throw 'Rules failed' } }

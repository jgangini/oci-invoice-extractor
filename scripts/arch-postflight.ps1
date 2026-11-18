$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
graphify update .
if ($LASTEXITCODE) { throw 'Graphify update failed' }
graphify export wiki --graph graphify-out/graph.json
if ($LASTEXITCODE) { throw 'Graphify wiki failed' }
if (Test-Path .sentrux/rules.toml) { sentrux check .; if ($LASTEXITCODE) { throw 'Rules failed' } }
else { Write-Host 'No repository Sentrux rules configured.' }
sentrux gate .
if ($LASTEXITCODE) { throw 'Architecture gate failed' }

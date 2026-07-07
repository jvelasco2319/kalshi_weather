$ErrorActionPreference = "Stop"
cd $PSScriptRoot\..
.\.venv\Scripts\Activate.ps1
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60

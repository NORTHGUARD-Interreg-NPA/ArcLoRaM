#Requires -RunAsAdministrator
<#
.SYNOPSIS
Cuts this PC's TCP connection to ONE Pi Node's log port for a few seconds, and nothing else.

.DESCRIPTION
A Windows Firewall block rule on the Pi Node's address and log port, in both directions, drops every packet of the
connection without closing it: the case a capture's TCP reader has to notice by itself (Test Record
docs/test-tracker/86-run-validity.md). The Pi, its board, its other connections, the other Pi Nodes and the rest of
the tailnet are untouched. It changes nothing on the Pi.

The script waits for the trigger file to appear, so that the cut can be fired at a chosen moment of a run
(`New-Item <Trigger>`, or `touch` from WSL), then deletes it, blocks for -Seconds, and removes the rules. It does
that -Cuts times, so that one approval covers a series. The rules are removed in a `finally`, and at the start of the
next call, whatever happened. The UTC times of the start and the end of every cut go to -Log.

Needs an elevated PowerShell (one UAC approval). Run it as:
  powershell -ExecutionPolicy Bypass -File cut-link.ps1 -PiNode nuna-node-06 -Trigger $env:TEMP\bench-cut\go -Log $env:TEMP\bench-cut\cut.log
#>
param(
    [Parameter(Mandatory)][string]$PiNode,   # a MagicDNS short name, e.g. nuna-node-06
    [int]$Port = 4000,
    [int]$Seconds = 15,
    [int]$Cuts = 1,                          # how many times to wait for the trigger and cut
    [Parameter(Mandatory)][string]$Trigger,  # the cut starts when this file exists
    [Parameter(Mandatory)][string]$Log,
    [int]$WaitMinutes = 90                   # give up for good when this long has passed since the start
)

$ErrorActionPreference = 'Stop'
$name = "bench-cut-$PiNode-$Port"

function Stamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ') }
function Say([string]$text) { "$(Stamp) $text" | Tee-Object -FilePath $Log -Append }
function Remove-Rules { Get-NetFirewallRule -DisplayName "$name-*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule }

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null
$addresses = @([System.Net.Dns]::GetHostAddresses($PiNode) | ForEach-Object { $_.IPAddressToString })
if (-not $addresses) { throw "$PiNode does not resolve" }
Remove-Rules   # stale rules of an earlier call that was killed

$give_up = (Get-Date).AddMinutes($WaitMinutes)
for ($cut = 1; $cut -le $Cuts; $cut++) {
    Say "waiting for the trigger file ($cut of $Cuts), to cut $PiNode port $Port for $Seconds s"
    while (-not (Test-Path -LiteralPath $Trigger)) {
        if ((Get-Date) -gt $give_up) { Say "no trigger in $WaitMinutes min: stopped after $($cut - 1) cut(s)"; exit 0 }
        Start-Sleep -Milliseconds 100
    }
    Remove-Item -LiteralPath $Trigger -Force

    try {
        foreach ($direction in 'Outbound', 'Inbound') {
            New-NetFirewallRule -DisplayName "$name-$direction" -Direction $direction -Action Block -Protocol TCP `
                -RemoteAddress $addresses -RemotePort $Port | Out-Null
        }
        Say "CUT START $PiNode port $Port"
        Start-Sleep -Seconds $Seconds
    }
    finally {
        Remove-Rules
        Say "CUT END $PiNode port $Port"
    }
}

<#
.SYNOPSIS
    Link this checkout into Blender as an extension, so edits are live.

.DESCRIPTION
    Creates a directory junction from Blender's user_default extensions folder
    to blender/BB_pipeline in this repository. Nothing is copied, so editing
    the add-on or the shared core and restarting Blender is the whole
    iteration loop - no rebuild, no reinstall.

    Junctions do not need administrator rights, unlike symlinks.

    Enable it once in Blender under Edit > Preferences > Add-ons > BB Kitsu Pipeline
    Pipeline; the setting sticks across restarts.

.PARAMETER BlenderVersion
    Which Blender to link into, e.g. 4.5. Defaults to every version found.

.PARAMETER Remove
    Remove the junction instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\dev_install.ps1 -BlenderVersion 4.5
#>
param(
    [string]$BlenderVersion,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo 'blender\BB_pipeline'

if (-not (Test-Path $source)) {
    throw "Add-on not found at $source"
}

$configRoot = Join-Path $env:APPDATA 'Blender Foundation\Blender'
if (-not (Test-Path $configRoot)) {
    throw "No Blender configuration found at $configRoot"
}

$versions = if ($BlenderVersion) {
    @($BlenderVersion)
} else {
    # Only versions with the extensions system; 4.1 and earlier cannot use this.
    Get-ChildItem $configRoot -Directory |
        Where-Object { $_.Name -match '^\d+\.\d+$' -and [version]$_.Name -ge [version]'4.2' } |
        Select-Object -ExpandProperty Name
}

foreach ($version in $versions) {
    $target = Join-Path $configRoot "$version\extensions\user_default\BB_pipeline"

    if (Test-Path $target) {
        $item = Get-Item $target -Force
        if ($item.LinkType) {
            Remove-Item $target -Force
            Write-Host "removed existing link for Blender $version"
        }
        else {
            Write-Warning "Blender ${version}: $target is a real folder, not a link - skipping so an installed copy is not destroyed"
            continue
        }
    }

    if ($Remove) { continue }

    $parent = Split-Path -Parent $target
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    cmd /c mklink /J "`"$target`"" "`"$source`"" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "mklink failed for Blender $version"
    }
    Write-Host "Blender ${version}: linked -> $source"
}

if (-not $Remove) {
    Write-Host ''
    Write-Host 'Enable it in Blender: Edit > Preferences > Add-ons > BB Kitsu Pipeline'
}

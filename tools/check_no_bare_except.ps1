param(
  [Parameter(Mandatory=$true)]
  [string[]] $Paths,

  [string] $Whitelist = $null
)

$ErrorActionPreference = "Stop"

# Match ONLY a bare except line in Python:
#   ^\s*except\s*:\s*(#.*)?$
$pattern = '^\s*except\s*:\s*(#.*)?$'

function RepoRel([string] $p) {
  $root = (Get-Location).Path
  $rel = Resolve-Path $p | ForEach-Object {
    $_.Path.Substring($root.Length).TrimStart('\','/')
  }
  return ($rel -replace '\\','/')
}

$allowed = @{}
if ($Whitelist -and (Test-Path $Whitelist)) {
  Get-Content $Whitelist | ForEach-Object {
    $s = $_.Trim()
    if (-not $s -or $s.StartsWith("#")) { return }
    if ($s -notmatch '^(.*):(\d+)$') { throw "Invalid whitelist line (expected path:lineno): $s" }
    $p = ($Matches[1] -replace '\\','/')
    $n = [int]$Matches[2]
    $allowed["$p`:$n"] = $true
  }
}

$hits = @()

foreach ($target in $Paths) {
  if (-not (Test-Path $target)) { throw "Path not found: $target" }

  $files = @()
  if ((Get-Item $target).PSIsContainer) {
    $files = Get-ChildItem -Path $target -Recurse -File -Filter "*.py"
  } else {
    if ($target.EndsWith(".py")) { $files = @(Get-Item $target) }
  }

  foreach ($f in $files) {
    $rel = (RepoRel $f.FullName)

    $i = 0
    Get-Content $f.FullName | ForEach-Object {
      $i++
      $line = $_
      if ($line -match $pattern) {
        $key = "$rel`:$i"
        if (-not $allowed.ContainsKey($key)) {
          $hits += [pscustomobject]@{ Path=$rel; Line=$i; Text=$line.Trim() }
        }
      }
    }
  }
}

if ($hits.Count -gt 0) {
  Write-Host "ERROR: bare 'except:' detected (use 'except Exception as e:' or narrower):"
  $hits | Sort-Object Path, Line | ForEach-Object {
    Write-Host ("  {0}:{1}: {2}" -f $_.Path, $_.Line, $_.Text)
  }
  exit 2
}

Write-Host "OK: no bare 'except:' found in scanned paths."
exit 0

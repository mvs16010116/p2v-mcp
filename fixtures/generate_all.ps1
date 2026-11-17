# Regenerate all programmatically-generated fixtures: BGM + caption check + beat gate.
# NOTE: messages are intentionally ASCII so Windows PowerShell 5.1 can parse this
# file regardless of the system ANSI codepage (no UTF-8 BOM required).
# Images are AI-generated, not programmatic; this script only converts retained
# AI source PNGs to JPG if the target JPGs are missing, otherwise it warns.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }

New-Item -ItemType Directory -Force -Path `
  (Join-Path $here 'audio'), (Join-Path $here 'images'), (Join-Path $here 'texts') | Out-Null

# 1) BGM: 8 seconds, 120 BPM, a clear beat every 0.5 s plus a quiet sustained bed.
$expr = 'aevalsrc=0.35*sin(2*PI*55*(t-0.5*floor(2*t)))*exp(-25*(t-0.5*floor(2*t)))+0.12*sin(2*PI*440*(t-0.5*floor(2*t)))*exp(-60*(t-0.5*floor(2*t)))+0.05*sin(2*PI*220*t):s=44100:d=8'
& ffmpeg -hide_banner -loglevel error -f lavfi -i $expr -c:a pcm_s16le -ar 44100 -y (Join-Path $here 'audio/bgm_120bpm.wav')
if ($LASTEXITCODE -ne 0) { throw 'ffmpeg failed to generate BGM.' }
Write-Host 'OK: generated audio/bgm_120bpm.wav'

# 2) Caption check: valid JSON with exactly 10 captions.
$pyCheck = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
n = len(d["captions"])
print("OK: captions.json has %d captions" % n)
sys.exit(0 if n == 10 else 1)
'@
$pyCheck | & $python - (Join-Path $here 'texts/captions.json')
if ($LASTEXITCODE -ne 0) { throw 'captions.json validation failed.' }

# 3) If JPG missing, convert the retained AI source PNG (do not call AI again).
function Convert-FromAiSource {
  param([string]$SrcDir, [string]$TargetName)
  $target = Join-Path $here (Join-Path 'images' $TargetName)
  if (Test-Path -LiteralPath $target) { return }
  $srcDir = Join-Path $here (Join-Path 'images' $SrcDir)
  $src = Get-ChildItem -LiteralPath $srcDir -Filter '*.png' -File -ErrorAction SilentlyContinue |
         Select-Object -First 1
  if ($null -eq $src) {
    Write-Warning "Missing images/$TargetName and no AI source PNG found. Regenerate via image_gen (see README)."
    return
  }
  & ffmpeg -hide_banner -loglevel error -y -i $src.FullName -q:v 2 $target
  if ($LASTEXITCODE -ne 0) { throw "Failed to convert $TargetName" }
  Write-Host "OK: converted AI source to images/$TargetName"
}
Convert-FromAiSource '_gen_group' 'group_10people.jpg'
Convert-FromAiSource '_gen_person' 'person_ref.jpg'

# 4) Beat gate (first acceptance baseline).
& $python (Join-Path $here 'verify_beat.py')
switch ($LASTEXITCODE) {
  0 { Write-Host 'OK: beat gate PASS' }
  3 { Write-Warning 'librosa missing: beat gate SKIPPED (NOT a pass). Install: python -m pip install librosa' }
  default { throw "Beat gate failed with exit code $LASTEXITCODE" }
}

Write-Host 'ALL DONE.'

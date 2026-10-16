# Dowse's installer, tested on a fresh Windows: Windows Sandbox (no Python, no Visual C++ runtime, a GPU or none;
# scripts/sandbox_test.py maps a folder at C:\Test holding Dowse-Setup.exe), or a CI runner
# (.github/workflows/windows.yml). What it sees goes to -Out (C:\Test\results\<run> by default): results.json (each
# step: ok, seconds, what it found), the installer's logs, the app's logs and screenshots. Exits 1 if a step fails.

param([string]$Run = "run", [string]$Setup = "C:\Test\Dowse-Setup.exe", [string]$Out = "")

$ErrorActionPreference = "Continue"
if (-not $Out) { $Out = "C:\Test\results\$Run" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$Setup = (Resolve-Path $Setup).Path
Start-Transcript -Path (Join-Path $Out "transcript.txt") | Out-Null
$App = Join-Path $env:LOCALAPPDATA "Programs\Dowse"
$Data = Join-Path $env:LOCALAPPDATA "Dowse"
$Results = [ordered]@{ run = $Run; started = (Get-Date).ToString("s"); steps = @() }

function Save { $Results | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $Out "results.json") }

function Step([string]$Name, [scriptblock]$Body) {
    Write-Host "== $Name"
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $found = $null; $ok = $false; $why = $null
    try { $found = & $Body; $ok = $true } catch { $why = $_.Exception.Message; Write-Host "FAILED: $why" }
    $Results.steps += [ordered]@{ step = $Name; ok = $ok; seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1);
                                  found = $found; error = $why }
    Save
}

function Check([bool]$Condition, [string]$What) { if (-not $Condition) { throw $What } }

function Screenshot([string]$Name) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
    $bitmap.Save((Join-Path $Out "$Name.png"), [Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()
}

function Install([string]$Log) {
    $p = Start-Process $Setup "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$(Join-Path $Out $Log)`"" -Wait -PassThru
    return $p.ExitCode
}

function Dowse([string]$Arguments, [string]$Log) {
    $p = Start-Process (Join-Path $App "python.exe") "-m dowse.cli $Arguments" -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Out "$Log.out.txt") -RedirectStandardError (Join-Path $Out "$Log.err.txt")
    return $p.ExitCode
}

function Running { Get-Process -Name Dowse -ErrorAction SilentlyContinue }

function AppJson {
    $file = Join-Path $Data "app.json"
    if (Test-Path $file) { return Get-Content $file -Raw | ConvertFrom-Json }
}

function WaitFor([scriptblock]$Condition, [int]$Seconds) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) { if (& $Condition) { return $true }; Start-Sleep -Milliseconds 500 }
    return $false
}

$WebView2 = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'

Step "the PC as found" {
    [ordered]@{
        windows = (Get-CimInstance Win32_OperatingSystem).Caption + " " + [Environment]::OSVersion.Version
        gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
        cpu = (Get-CimInstance Win32_Processor).Name
        memory_gb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
        webview2 = [bool]((Test-Path "HKLM:\SOFTWARE\WOW6432Node\$WebView2") -or (Test-Path "HKLM:\SOFTWARE\$WebView2") -or
                          (Test-Path "HKCU:\Software\$WebView2"))
        vc_runtime_in_system32 = Test-Path "$env:SystemRoot\System32\vcruntime140_1.dll"
        python_on_path = [bool](Get-Command python -ErrorAction SilentlyContinue)
    }
}

Step "install (silent)" {
    $code = Install "install.log"
    Check ($code -eq 0) "the installer exited with $code"
    Check (Test-Path (Join-Path $App "Dowse.exe")) "no Dowse.exe in $App"
    $shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Dowse.lnk"
    Check (Test-Path $shortcut) "no Start menu shortcut"
    $key = Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Dowse" }
    Check ($null -ne $key) "no uninstall entry"
    [ordered]@{ version = (Get-ItemProperty $key.PSPath).DisplayVersion; app = $App
                webview2_after = [bool]((Test-Path "HKLM:\SOFTWARE\WOW6432Node\$WebView2") -or (Test-Path "HKCU:\Software\$WebView2")) }
}

Step "the installed Python starts" {
    $out = & (Join-Path $App "python.exe") -c "import dowse, sqlite3, webview, av, numpy; print(dowse.__version__)" 2>&1
    Check ($LASTEXITCODE -eq 0) "python: $out"
    "$out"
}

Step "setup: the runtime and model for this PC" {
    $code = Dowse "setup" "setup"
    Check ($code -eq 0) "dowse setup exited with $code (see setup.err.txt)"
    Get-Content (Join-Path $Out "setup.out.txt") -Tail 6
}

Step "index and search a small library" {
    $library = "C:\Library"
    New-Item -ItemType Directory -Force -Path $library | Out-Null
    Get-ChildItem "$env:SystemRoot\Web" -Recurse -Include *.jpg | Select-Object -First 12 | Copy-Item -Destination $library
    Set-Content (Join-Path $library "budget notes.txt") "Groceries 320 euros, rent 950 euros, savings 200 euros a month."
    Set-Content (Join-Path $library "trip ideas.md") "# Lisbon in June`nThe yellow tram 28, custard tarts in Belem, Sintra."
    $code = Dowse "index $library" "index"
    Check ($code -eq 0) "dowse index exited with $code"
    $code = Dowse "search `"how much do I save each month`"" "search-budget"
    Check ($code -eq 0) "dowse search exited with $code"
    $first = (Get-Content (Join-Path $Out "search-budget.out.txt") | Select-Object -First 3) -join " | "
    Check ($first -match "budget notes") "the budget note isn't first: $first"
    $null = Dowse "search `"a landscape`"" "search-picture"
    [ordered]@{ budget = $first; picture = ((Get-Content (Join-Path $Out "search-picture.out.txt") | Select-Object -First 2) -join " | ") }
}

Step "the app starts" {
    Start-Process (Join-Path $App "Dowse.exe") "-m dowse.desktop.launch"
    Check (WaitFor { $null -ne (AppJson) } 90) "no app.json after 90 s"
    Start-Sleep -Seconds 12
    Screenshot "app"
    Start-Sleep -Seconds 30
    Screenshot "app-40s"
    $info = AppJson
    $page = Invoke-WebRequest "http://127.0.0.1:$($info.port)/" -UseBasicParsing -TimeoutSec 10
    Check ($page.StatusCode -eq 200) "the app's server answered $($page.StatusCode)"
    [ordered]@{ pid = $info.pid; port = $info.port; explorer_menu = Test-Path "HKCU:\Software\Classes\*\shell\Dowse" }
}

Step "update while it runs (silent)" {
    $before = (AppJson).pid
    $code = Install "update.log"
    Check ($code -eq 0) "the installer exited with $code"
    Check ($null -eq (Get-Process -Id $before -ErrorAction SilentlyContinue)) "the old app is still running"
    Check (WaitFor { $j = AppJson; $null -ne $j -and $j.pid -ne $before -and (Get-Process -Id $j.pid -ErrorAction SilentlyContinue) } 90) `
        "the app didn't come back after the update"
    Start-Sleep -Seconds 8
    Screenshot "after-update"
    [ordered]@{ before = $before; after = (AppJson).pid }
}

Step "an interrupted install, then repaired" {
    Start-Process (Join-Path $App "python.exe") "-m dowse.desktop.launch --quit" -Wait -WindowStyle Hidden
    $p = Start-Process $Setup "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$(Join-Path $Out 'interrupted.log')`"" -PassThru
    Start-Sleep -Seconds 4
    Get-Process | Where-Object { $_.Path -like "$env:TEMP\is-*" -or $_.Id -eq $p.Id } | Stop-Process -Force
    Start-Sleep -Seconds 2
    $code = Install "repair.log"
    Check ($code -eq 0) "the repairing install exited with $code"
    $out = & (Join-Path $App "python.exe") -c "import dowse.desktop.app; print('ok')" 2>&1
    Check ("$out" -eq "ok") "the repaired app doesn't import: $out"
    "repaired"
}

Step "uninstall (silent)" {
    Set-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" Dowse "`"$App\Dowse.exe`" -m dowse.desktop.launch --background"
    Start-Process (Join-Path $App "Dowse.exe") "-m dowse.desktop.launch --background"
    $null = WaitFor { $null -ne (AppJson) -and (Running) } 60
    $uninstaller = Get-ChildItem $App -Filter "unins*.exe" | Select-Object -First 1
    Start-Process $uninstaller.FullName "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$(Join-Path $Out 'uninstall.log')`"" -Wait
    Check (WaitFor { -not (Test-Path $App) } 120) "$App is still there"
    Check (-not (Running)) "Dowse is still running"
    Check (-not (Test-Path (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Dowse.lnk"))) "the shortcut is still there"
    Check ($null -eq (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name Dowse -ErrorAction SilentlyContinue)) `
        "the start-with-Windows entry is still there"
    Check (-not (Test-Path "HKCU:\Software\Classes\*\shell\Dowse")) "Explorer's Dowse menu is still there"
    Check (Test-Path $Data) "the data folder was deleted by a silent uninstall"
    [ordered]@{ data_kept = (Get-ChildItem $Data | ForEach-Object { $_.Name }) }
}

# The app's own logs (kept by the uninstall, as the data is)
Copy-Item (Join-Path $Data "Logs") (Join-Path $Out "Logs") -Recurse -ErrorAction SilentlyContinue
$Results.finished = (Get-Date).ToString("s")
$Results.ok = -not ($Results.steps | Where-Object { -not $_.ok })
Save
Stop-Transcript | Out-Null
Set-Content (Join-Path $Out "done") ""   # the host closes the sandbox (shutting down from inside makes Windows report an error)
if (-not $Results.ok) { exit 1 }

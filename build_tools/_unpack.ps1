$ErrorActionPreference = "Stop"
$log = "D:\软件\XianRenZhangAgent\build_tools\unpack.log"
Set-Content -Path $log -Value "start"
try {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $dst = "D:\软件\XianRenZhangAgent\build_tools\pyi"
  if (!(Test-Path $dst)) { New-Item -ItemType Directory -Path $dst | Out-Null }
  $wheels = Get-ChildItem "D:\软件\XianRenZhangAgent\build_tools\wheels\*.whl"
  foreach ($w in $wheels) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($w.FullName)
    foreach ($e in $zip.Entries) {
      $target = Join-Path $dst $e.FullName
      $dir = Split-Path $target -Parent
      if (!(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
      if ($e.Name -ne "") {
        $s = $e.Open()
        $o = [System.IO.File]::Create($target)
        $s.CopyTo($o)
        $o.Close(); $s.Close()
      }
    }
    $zip.Dispose()
    Add-Content -Path $log -Value ("unpacked " + $w.Name)
  }
  Add-Content -Path $log -Value "done"
} catch {
  Add-Content -Path $log -Value ("ERROR: " + $_.Exception.Message)
}

$src = "C:\Responses\processed"
$dest = "C:\Responses"
$deg = 16000

# What to do when a destination file with the same name exists and contents are identical.
# Options: 'skip' - do not move the source (leave it where it is)
#          'overwrite' - replace the destination with the source
$SameFileAction = 'skip'

New-Item -ItemType Directory -Path $dest -Force | Out-Null

Get-ChildItem -Path $src -Filter *.json -Recurse -File |
ForEach-Object -Parallel {
  $dest = $using:dest
  # copy the using-scoped setting into a local variable for safe use inside the scriptblock
  $sameAction = $using:SameFileAction
  $name = $_.Name
  $target = Join-Path $dest $name

  if (Test-Path -LiteralPath $target) {
    # Try comparing contents by hash. If identical, either skip or overwrite based on $using:SameFileAction.
    try {
      $srcHash = Get-FileHash -Algorithm SHA256 -Path $_.FullName
      $dstHash = Get-FileHash -Algorithm SHA256 -Path $target
      if ($srcHash.Hash -eq $dstHash.Hash) {
        switch ($sameAction.ToLower()) {
          'skip' {
            Write-Verbose "Skipping move for identical file: $name"
            return
          }
          'overwrite' {
            Move-Item -LiteralPath $_.FullName -Destination $target -Force
            return
          }
          default {
            Write-Verbose "Unknown SameFileAction '$sameAction', skipping $name"
            return
          }
        }
      }
    }
    catch {
      # If hashing failed for any reason, fall back to the existing collision-avoidance behavior.
      Write-Verbose "Hash comparison failed for $($name): $($_)"
    }

    # If we reach here, target exists and contents differ: find a non-colliding name (foo (1).json, etc.)
    $i = 1
    while (Test-Path -LiteralPath $target) {
      $base = [IO.Path]::GetFileNameWithoutExtension($name)
      $ext = [IO.Path]::GetExtension($name)
      $target = Join-Path $dest ("{0} ({1}){2}" -f $base, $i, $ext)
      $i++
    }
  }

  Move-Item -LiteralPath $_.FullName -Destination $target -Force
} -ThrottleLimit $deg
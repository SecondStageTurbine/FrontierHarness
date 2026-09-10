; The Python sidecar has its own loader process. Stop it before replacing
; installed binaries so upgrades cannot silently retain a locked old runtime.
; Name it once: CheckIfAppIsRunning finds nothing and warns about nothing when the
; name is wrong, so a renamed sidecar would turn both hooks into quiet no-ops and
; leave the installer overwriting a running executable. Must match the installed
; name of externalBin in tauri.release.conf.json.
!define BACKEND_EXE "frontier-backend.exe"

!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro CheckIfAppIsRunning "${BACKEND_EXE}" "${PRODUCTNAME} backend"
  ; The kill macro trusts its own return code without confirming the image is
  ; unlocked, so keep this grace period before any file is replaced.
  Sleep 500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro CheckIfAppIsRunning "${BACKEND_EXE}" "${PRODUCTNAME} backend"
  Sleep 500
!macroend

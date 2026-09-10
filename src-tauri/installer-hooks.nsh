; The Python sidecar has its own loader process. Stop it before replacing
; installed binaries so upgrades cannot silently retain a locked old runtime.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro CheckIfAppIsRunning "frontier-backend.exe" "${PRODUCTNAME} backend"
  Sleep 500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro CheckIfAppIsRunning "frontier-backend.exe" "${PRODUCTNAME} backend"
  Sleep 500
!macroend

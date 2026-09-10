; The Python sidecar has its own loader process. Stop it before replacing
; installed binaries so upgrades cannot silently retain a locked old runtime.
; Name it once: CheckIfAppIsRunning finds nothing and warns about nothing when the
; name is wrong, so a renamed sidecar would turn both hooks into quiet no-ops and
; leave the installer overwriting a running executable. Must match the installed
; name of externalBin in tauri.release.conf.json.
!define BACKEND_EXE "frontier-backend.exe"

; A silent install ignores a failed overwrite. NSIS records the error and carries on,
; so one locked executable yields a successful-looking upgrade that still runs the old
; binary, with the new version in the registry. Refuse before writing anything.
!macro FailIfLocked file
  ${If} ${FileExists} "$INSTDIR\${file}"
    Push $R9
    ClearErrors
    FileOpen $R9 "$INSTDIR\${file}" a
    ${If} ${Errors}
      Abort "${file} is open and cannot be replaced, so nothing was installed. Close ${PRODUCTNAME}, wait for any antivirus scan of it to finish, then run this installer again."
    ${EndIf}
    FileClose $R9
    Pop $R9
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro CheckIfAppIsRunning "${BACKEND_EXE}" "${PRODUCTNAME} backend"
  ; The kill macro trusts its own return code without confirming the image is
  ; unlocked, so keep this grace period before any file is replaced.
  Sleep 500
  !insertmacro FailIfLocked "${MAINBINARYNAME}.exe"
  !insertmacro FailIfLocked "${BACKEND_EXE}"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Closes the gap between that check and the writes themselves: report what is actually
  ; installed rather than trusting an exit code that ignores a skipped file. The sidecar
  ; carries no version resource, so the main binary is what can be checked this way.
  Push $R0
  Push $R1
  Push $R2
  ClearErrors
  GetDLLVersion "$INSTDIR\${MAINBINARYNAME}.exe" $R0 $R1
  ${If} ${Errors}
    Abort "${MAINBINARYNAME}.exe is missing or unreadable after installation. Run this installer again."
  ${EndIf}
  IntOp $R2 $R0 >> 16
  StrCpy $R2 "$R2."
  IntOp $R0 $R0 & 0x0000FFFF
  StrCpy $R2 "$R2$R0."
  IntOp $R0 $R1 >> 16
  StrCpy $R2 "$R2$R0."
  IntOp $R1 $R1 & 0x0000FFFF
  StrCpy $R2 "$R2$R1"
  ${If} $R2 != "${VERSIONWITHBUILD}"
    Abort "${PRODUCTNAME} $R2 is still installed instead of ${VERSION}, because a file could not be replaced. Close ${PRODUCTNAME} and run this installer again."
  ${EndIf}
  Pop $R2
  Pop $R1
  Pop $R0
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro CheckIfAppIsRunning "${BACKEND_EXE}" "${PRODUCTNAME} backend"
  Sleep 500
!macroend

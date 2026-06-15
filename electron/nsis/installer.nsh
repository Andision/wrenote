# Wrenote installer customization — an optional "create desktop shortcut" checkbox.
#
# By default electron-builder creates the desktop shortcut unconditionally. We
# set createDesktopShortcut:false in package.json (which defines
# DO_NOT_CREATE_DESKTOP_SHORTCUT, suppressing the built-in one) and instead show
# a checkbox right after the install-location page, then create the shortcut in
# customInstall only when it's ticked (default: ticked).
#
# The .lnk name/path/AppUserModelID match electron-builder's own desktop link
# (see templates/nsis/include/installer.nsh -> addDesktopLink), so the
# uninstaller — which removes "$DESKTOP\${SHORTCUT_NAME}.lnk" — still cleans it up.

!include "nsDialogs.nsh"
!include "LogicLib.nsh"

Var WrenoteDesktopCheckbox
Var WrenoteCreateDesktop

# Extra wizard page inserted after the "choose install directory" page.
!macro customPageAfterChangeDir
  Page custom wrenoteDesktopPageCreate wrenoteDesktopPageLeave
!macroend

Function wrenoteDesktopPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 24u "Wrenote can place a shortcut on your desktop for quick access."
  Pop $1
  ${NSD_CreateCheckbox} 0 32u 100% 12u "Create a desktop shortcut"
  Pop $WrenoteDesktopCheckbox
  ${NSD_Check} $WrenoteDesktopCheckbox  # default: ticked
  nsDialogs::Show
FunctionEnd

Function wrenoteDesktopPageLeave
  ${NSD_GetState} $WrenoteDesktopCheckbox $WrenoteCreateDesktop
FunctionEnd

# Runs inside the install section; create the shortcut only if the box was ticked.
!macro customInstall
  ${If} $WrenoteCreateDesktop == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\${SHORTCUT_NAME}.lnk" "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "" "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0 "" "" "${APP_DESCRIPTION}"
    ClearErrors
    WinShell::SetLnkAUMI "$DESKTOP\${SHORTCUT_NAME}.lnk" "${APP_ID}"
  ${EndIf}
!macroend

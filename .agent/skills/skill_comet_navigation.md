# Skill: Comet Browser Optimization (Tab-Based)

## Objective
Avoid window pollution and ensure automation happens within the user's active browser session (Comet).

## The "Tecla" (Magic Command)
To open a new tab in the active Comet window on the current desktop, ALWAYS use:
`cmd /c start "" "C:\Users\Acarvi\Desktop\Comet.lnk" "<URL>"`

## Protocol
1. **No Chrome**: Never use Google Chrome unless explicitly requested.
2. **Same Desktop**: Always operate in the current Windows Desktop workspace.
3. **Multi-Tab**: Open URLs as tabs within the existing Comet instance to facilitate Split Screen usage.
4. **Verification**: After launching, ask for user confirmation or use `tasklist` to ensure `comet.exe` is handling the request.

# Skill: AI Memory and Context Logging

## Objective
Maintain a persistent memory of all technical decisions and changes to ensure context is never lost between AI sessions.

## Rules
1. **Memory Log OBLIGATORIO**: Al finalizar cualquier modificacin significativa, es OBLIGATORIO actualizar el archivo `memory_log.md` (o `CHANGELOG_AI.md`) en la raz del proyecto.
2. **Estructura del Log**: El registro debe incluir:
   - **Qu se hizo**: Resumen técnico de los cambios.
   - **Por qu se hizo**: Justificacin o ticket relacionado.
   - **Archivos tocados**: Lista de archivos modificados.
3. **Persistencia**: Este skill permite que futuras instancias de la IA comprendan el "estado del arte" del proyecto sin necesidad de re-analizar todo el código desde cero.

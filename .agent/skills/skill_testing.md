# Skill: Testing and Quality Assurance

## Objective
Ensure the maximum reliability and stability of the codebase by enforcing rigorous testing standards.

## Rules
1. **Coverage OBLIGATORIA (100%)**: Todas las funciones y ramas lgicas deben estar cubiertas por tests unitarios o funcionales en Python.
2. **Pytest First**: Antes de dar por finalizada una tarea, de realizar un commit o de subir cambios, es OBLIGATORIO ejecutar la suite completa de tests usando `pytest`.
   - Si un solo test falla, **NO SE SUBE EL CDIGO**.
3. **Documentacin de Tests**: Cada archivo de test en la carpeta `tests/` debe contener comentarios o un `README.md` local explicando:
   - Qu funcionalidades especficas se estn probando.
   - Cul es la justificacin del test (por qu es crtico).
4. **Anti-Regresin**: Si se corrige un bug, se debe incluir un test que reproduzca el error previo para evitar que vuelva a ocurrir.

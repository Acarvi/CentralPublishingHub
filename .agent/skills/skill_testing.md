# Skill: Automated Testing & Coverage

## Objective
Maintain high code quality through rigorous automated testing and coverage metrics.

## Protocol
1. **Coverage Check**: Run `pytest --cov=core --cov=routers --cov-report=term-missing`.
2. **Target**: Aim for 90-100% coverage on core logic.
3. **Automatic Debugging**: 
   - If a test fails, read the traceback.
   - Use `grep` or `Select-String` to find the failing line.
   - Propose a fix and re-run immediately.
4. **Mocking**: Use `unittest.mock` for external API calls (Meta, YouTube) to ensure tests are fast and hermetic.

## Commands
- `pytest` (Basic)
- `pytest --cov=.` (Full Coverage)

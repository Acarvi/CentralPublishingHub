# Skill: Documentation & Lifecycle Monitoring

## Objective
Ensuring that the project's intellectual state (README, Memory Logs, and Artifacts) is synchronized with the actual codebase after every significant intervention.

## Protocol
1. **README Update**: After completing a task, check if any architectural changes occurred. Update the `Architecture` or `How to Use` section in `README.md`.
2. **Memory Logging**: Maintain a `memory_log.md` (or similar) in the project root to record "lessons learned" and technical debt resolved.
3. **Artifact Consistency**: Ensure that `implementation_plan.md` and `walkthrough.md` in the chat brain are finalized and reflect the exact state of the pushed code.

## Critical Checks
- Does the README reflect the current Hub URL?
- Are the .env keys documented (without values)?
- Is the new feature listed in the "Features" section?

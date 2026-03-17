# Git Workflow

## Commit Convention
- Format: `{type}: {description} [#{feature-id}]`
- Types: feat / fix / refactor / test / docs
- Example: `feat: add NetSuite API connector [#F1]`

## Commit Rules
- One logical change per commit
- Every commit must leave the project in a runnable state
- Write descriptive messages
- Stage specific files, avoid `git add -A` to prevent committing secrets

## Branch Strategy
- `main` branch should always be stable
- Create feature branches for complex features: `feature/{feature-id}-{short-name}`
- Merge to main only after verification passes

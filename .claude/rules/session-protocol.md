# Session Protocol

## Session Start (MANDATORY)
1. Run `pwd` to confirm you are in the correct project directory
2. Read `claude-progress.txt` -- understand what was done in previous sessions
3. Read `ROADMAP.md` -- identify current task priorities
4. Read `feature_list.json` -- check which features are incomplete
5. Announce: "Resuming project. Current status: {X}/{Y} features complete. Next task: {task}."

## Session End (MANDATORY)
1. Update `claude-progress.txt` -- append session entry with Completed/In Progress/Blocked/Next Steps
2. Update `ROADMAP.md` -- change task checkboxes to reflect current state
3. Update `feature_list.json` -- set `passes: true` for any newly verified features
4. Commit all changes with descriptive message
5. Verify the project is in a runnable/buildable state

## Context Continuity
- NEVER start work without reading progress files first
- NEVER end a session without updating progress files
- If a session is interrupted, the next session's start protocol will recover context

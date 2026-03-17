# Verification Rules

## Definition of Done
A feature is ONLY complete when:
1. All acceptance criteria in PRD.md are met
2. All verification steps in feature_list.json pass
3. The `passes` field is set to `true` in feature_list.json
4. The ROADMAP.md checkbox is changed to `[x]`
5. Tests pass

## Prohibited Actions
- NEVER mark a feature as complete without running verification
- NEVER modify the `steps` array in feature_list.json
- NEVER delete entries from feature_list.json
- NEVER skip verification "to save time"
- NEVER claim "it should work" without actually testing

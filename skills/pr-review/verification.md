# PR Review Verification

When reviewing PRs, explicitly state what was and wasn't verified:

## Required Evidence
- Lines changed and why
- Tests touched or added
- Linter/type checks run

## Common Gaps to Note
- Hardware/audio paths not tested
- Integration behavior not verified
- Performance implications not measured

## Outcome Labels
- **pass**: All checks passed, no issues found
- **fail**: Issues found that block merge
- **incomplete**: Changes look correct but some verification gaps exist
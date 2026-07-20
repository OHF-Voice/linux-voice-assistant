# Linux Voice Assistant - Copilot Instructions

For comprehensive development guidelines, see [AGENTS.md](../AGENTS.md).

## Quick Reference

**Development Commands:**
```bash
./script/setup --dev    # Install dev dependencies
./script/lint           # Run all linting checks
./script/tests          # Run pytest unit tests
```

## PR Review Standards

### Review Philosophy
- Only comment when confident an issue exists
- Be concise: one sentence per comment when possible
- Focus on actionable feedback, not observations
- When reviewing text, only comment on clarity issues if the text is genuinely confusing or could lead to errors
- If you're uncertain whether something is an issue, don't comment

### What to Analyze
- Code quality and style consistency with the existing codebase
- Potential bugs or issues
- Performance implications
- Blocking IO in async code
- Security concerns
- Test coverage
- Documentation updates if needed

### Project Standards
Respect the project standards as outlined in AGENTS.md. Any deviations must be raised as `[PROBLEM]`.

### PR Title
The PR title must be a functional description of the change. It must NOT contain conventional commit prefixes such as `feat:`, `fix:`, `refactor:`, `chore:`, etc. Labels categorize PRs, not the title. Flag as `[PROBLEM]` if the title uses such prefixes.
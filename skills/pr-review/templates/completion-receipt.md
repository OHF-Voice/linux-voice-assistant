# Completion Receipt Template

```
subject: {CHANGE_DESCRIPTION}
files_modified:
  - {FILES_MODIFIED}
checks_run:
  - {CHECKS_RUN}
not_checked:
  - {NOT_CHECKED}
verdict: pass | fail | incomplete
reason: {REASON}
```

## Verification Checklist
- [ ] Ran `./script/lint` - all checks passed
- [ ] Ran `./script/tests` - all tests passed
- [ ] For audio-related changes: Note hardware testing NOT performed
- [ ] Did NOT claim hardware behavior verified unless exercised
- [ ] Changes focused on one feature or fix
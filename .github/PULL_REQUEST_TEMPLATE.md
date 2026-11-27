# What does this implement/fix?

<!-- Quick description and explanation of changes. -->

**Related issue (if applicable):**

- Fixes #<issue number>

## Types of changes

<!--
Tick exactly one box. CI derives the label from the ticked box.
-->

- [ ] Bugfix (non-breaking change which fixes an issue) — `bugfix`
- [ ] New feature (non-breaking change which adds functionality) — `new-feature`
- [ ] Enhancement to an existing feature — `enhancement`
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected) — `breaking-change`
- [ ] Refactor (no behaviour change) — `refactor`
- [ ] Documentation only — `documentation`
- [ ] Maintenance / chore — `maintenance`
- [ ] CI / workflow change — `ci`
- [ ] Dependencies bump — `dependencies`

## Breaking change migration

<!--
Only for the "Breaking change" type. Write a few plain sentences: what do users have to change
when they update (options, env vars, files, setup)? Leave the section empty for every other type.
This text is copied to `breaking_change.md` when the PR is merged and into the release notes.
Not the same as "Behavior changes" below.
-->

## Behavior changes

<!--
`docs/behavior.md` describes what users experience. If this PR changes any of that
(wake word, stop word, ducking, timers, announcements, mute, ...), tick the box and
list each change here, one per line, so it can be reviewed separately from the code.
-->

- [ ] This PR changes user-visible behavior and `docs/behavior.md` is updated accordingly.
- [ ] This PR does not change user-visible behavior.

Behavior changes (if any):

-

## End-to-end test changes

<!--
The end-to-end tests pin down the behavior users rely on, so every change to them is logged here.
Tick exactly one box. "End-to-end tests" means everything under `tests/e2e/` (tests, fixtures,
the harness) and the end-to-end workflow (`.github/workflows/e2e-tests.yml`).
If you changed them, list each change on its own line: which file or test, what was changed
(for example an assertion added, removed or loosened, a timeout or threshold changed, a test
skipped or deleted) and why. If a change makes a test expect different behavior, also list it
under "Behavior changes" above.
-->

- [ ] This PR does not change the end-to-end tests.
- [ ] This PR changes the end-to-end tests, and every change is listed below.

End-to-end test changes (if any):

-

## Checklist

- [ ] The code change is tested and works locally.
- [ ] `./script/lint` passes.
- [ ] `./script/tests` passes, and tests have been added/updated under `tests/` where applicable.
- [ ] `docs/behavior.md` matches the code after this change.

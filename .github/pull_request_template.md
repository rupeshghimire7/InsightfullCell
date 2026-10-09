## What changed

<!-- One or two sentences. Link the story: Closes #123 / IC-xxx -->

## Why

<!-- The problem or requirement this solves. What did you consider and reject? -->

## How to verify

<!-- Commands, screenshots, or the test that proves it. -->

## Checklist

- [ ] `make check` passes locally
- [ ] Tests added or updated for the behaviour that changed
- [ ] API change? `scripts/generate-contract.sh` run and the result committed
- [ ] Migration? It is reversible and safe while the old code is still running
- [ ] UI change? Keyboard, focus and screen-reader behaviour checked
- [ ] `CHANGELOG.md` updated (what changed | why)
- [ ] No secrets, tokens or personal data in code, logs or fixtures

# Agent instructions

## Firmware builds and boards

Build, flash and reset the firmware only through `bench` (`tools/bench`, skill `bench`).
It builds in its own Build Tree on `C:` and talks to the boards through a guarded programmer, so the repo's `Debug_C*` folders and Simon's CubeIDE workspace stay his.
Bench refuses anything irreversible (option bytes, OTP, protection, mass erase); that refusal is final.

Host-side unit tests (`Tests/`, CMake + host GCC) are not firmware builds and may be run.

## Development board compatibility

Every design must keep running on the NUCLEO development boards of the bench, whatever it targets on the production hardware.
A feature that only works with the Production Clock or the production board is not done.
Every Regime is built in both Sync Profiles, `DEV` and `PROD`: a Regime with no `DEV` variant cannot be tested on the bench.

## Naming issues

Name an issue by its number and a condensed title, never by the number alone: "#73 bench builds alternate ok/fail (missing HAL link errors)", not "#73".
This holds wherever Simon reads it: chat replies, questions, recaps, the paste-ready message for a new session, and comments and Test Record text.
A bare number tells him nothing, and he cannot tell which issue it is.
Condense the GitHub title to a few words and drop its phase tag and area (`[1] [Bench]`).
The `(#73)` at the end of a commit subject stays as it is: it is a link, and the subject already says what changed.

## Closing out an issue

Simon cannot easily tell whether the issue in hand is finished, what is left and whether it is saved.
So when the issue is solved, end with a recap and a verdict:

- **Done**: what the issue asked for, and how it was verified (tests, a bench run, the Test Record row).
- **Saved**: what is committed, pushed and landed on `main`, with the commit hashes, or what is not yet and why.
- **Left**: what the issue did not cover, each with its issue number (file one when it has none). Nothing stays only in the chat.
- **Open**: anything still running or waiting for a decision (a capture, a background run, a question for Simon).
- **Verdict**: "safe to close this session" when Done is verified, Saved is landed (or Simon chose to leave it), Left is tracked and nothing is Open. Otherwise say what blocks it.

Then propose the next most relevant issue to start in a fresh session, with the reason it comes next, and wait for Simon's answer.
Once he agrees, write a self-contained message he can paste into a new agent session: the issue number and what it asks, where the work stands, what to read first, the hardware list, and the traps already found.
Committing, pushing and landing on `main` happen when Simon asks; the recap says what is waiting for his go.

## Keeping the repo clean

Leave the repository as it was found, plus the landed work.
Before the recap, remove what this session created:

- Its worktree (`git worktree remove`, after unlinking the `tools/arclog/runs` symlink there), then its Build Tree (`bench prune`), and its local branches, once merged (`git branch -d`).
- Its remote branches, once merged (`git push origin --delete <branch>`).
- Its stash entries, scratch files in the repo, servers and background commands.
- The always-on bench capture is infrastructure: it keeps running, and the recap says so.

What another session owns is not touched: its worktrees, branches, stashes and running commands.
A stale-looking one is listed in the recap, with a proposal to remove it.
The repo is public: scan the diff for network details (tailnet names and addresses, accounts, keys) before the first push of a branch.

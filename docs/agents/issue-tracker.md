# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Where

The tracker is `NORTHGUARD-Interreg-NPA/ArcLoRaM`, the repository `origin` points at; `gh repo set-default` pins it for `gh`.
On 2026-10-07 every issue of `ArcLoRaM/firmware`, open and closed, was copied there with its number kept, so `#86` and every `#number` in the history, the docs and the Test Records mean the same issue.
The numbers that repository spent on pull requests (#72, #74, #81) or lost (#84) are closed placeholder issues.
A copied issue starts with a line saying who opened it in the old repository and when, and so does each comment, because GitHub cannot copy authors or dates.
`ArcLoRaM/firmware` is no longer the tracker: do not file or comment there.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

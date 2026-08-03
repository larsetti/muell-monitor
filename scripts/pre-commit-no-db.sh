#!/bin/bash
# pre-commit hook: blocks any commit that stages *.db files.
# Install: git config core.hooksPath scripts/
# (Run this once in the repo root, or copy to .git/hooks/pre-commit)

if git diff --cached --name-only | grep -q '\.db$'; then
    echo "ABORT: *.db files must not be committed."
    echo "Remove them from the index: git rm --cached <file>.db"
    exit 1
fi

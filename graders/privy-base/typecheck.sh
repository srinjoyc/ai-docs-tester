#!/usr/bin/env bash
# Typechecks the entire Privy Next.js project.
# Exits 0 on pass, non-zero on fail. stderr captured by the runner.
set -euo pipefail
cd "$1"
npx --yes tsc --noEmit 2>&1

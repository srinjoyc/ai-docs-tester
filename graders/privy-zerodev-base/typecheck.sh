#!/usr/bin/env bash
# Typechecks the Privy+ZeroDev integration starter app.
set -euo pipefail
cd "$1"
npx --yes tsc --noEmit 2>&1

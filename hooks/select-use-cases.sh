#!/usr/bin/env bash
# Ask, once and up front, which use-cases should be uploaded to blob storage
# after the deploy finishes.
#
# Why this runs at preprovision rather than at upload time: `azd up` paints a
# live progress table for the whole run, and it repaints over anything a hook
# writes. Asking during postdeploy meant the menu lost its last lines and the
# prompt itself, so you were answering a question you could not see. This hook
# runs before that table starts, records the answer, and hooks/postdeploy.sh
# then uploads without prompting.
#
# Never fails the deploy: any problem here just means "upload nothing".
set -uo pipefail

USE_CASES_DIR="use-cases"

# Where the answer is handed to postdeploy. .azure/ is gitignored, and keying
# by env name keeps parallel environments from reading each other's choice.
SELECTION_DIR=".azure/${AZURE_ENV_NAME:-default}"
SELECTION_FILE="$SELECTION_DIR/kratos-upload-selection"

mkdir -p "$SELECTION_DIR" 2>/dev/null || true
rm -f "$SELECTION_FILE" 2>/dev/null || true

write_selection() {
  printf '%s\n' "$1" > "$SELECTION_FILE" 2>/dev/null || true
}

[ -d "$USE_CASES_DIR" ] || { write_selection "none"; exit 0; }

USE_CASES=()
for dir in "$USE_CASES_DIR"/*/; do
  name="$(basename "$dir")"
  [ "$name" = "*" ] && continue
  USE_CASES+=("$name")
done
[ ${#USE_CASES[@]} -gt 0 ] || { write_selection "none"; exit 0; }

# Explicit env var wins and suppresses the prompt entirely, so CI and scripted
# runs are deterministic. Accepts: all | none | comma-separated use-case names.
if [ -n "${KRATOS_UPLOAD_USE_CASES:-}" ]; then
  write_selection "${KRATOS_UPLOAD_USE_CASES}"
  echo "Skills upload: '${KRATOS_UPLOAD_USE_CASES}' (from KRATOS_UPLOAD_USE_CASES)."
  exit 0
fi

# Legacy flag from before this was selectable.
if [ "${KRATOS_AUTO_UPLOAD_USE_CASES:-0}" = "1" ]; then
  write_selection "all"
  echo "Skills upload: all (from KRATOS_AUTO_UPLOAD_USE_CASES=1)."
  exit 0
fi

# No terminal (CI, --no-prompt, piped shell): choose the safe side rather than
# blocking the deploy. Uploading every use-case unasked is the worse default,
# and the storage account may be private-endpoint only anyway.
if [ ! -t 0 ] || [ ! -r /dev/tty ]; then
  write_selection "none"
  echo "Skills upload: skipped (no terminal). Set KRATOS_UPLOAD_USE_CASES=all to upload."
  exit 0
fi

# Talk to the terminal directly. azd captures the hook's stdout, so writing to
# /dev/tty keeps the menu and the prompt out of its buffer.
{
  echo ""
  echo "┌──────────────────────────────────────────────────────────┐"
  echo "│  Which skills should be uploaded after the deploy?       │"
  echo "└──────────────────────────────────────────────────────────┘"
  echo ""
  for i in "${!USE_CASES[@]}"; do
    printf '   %2d. %s\n' "$((i + 1))" "${USE_CASES[$i]}"
  done
  echo ""
  echo "    A. All of them"
  echo "    N. None - skip the upload (default)"
  echo ""
  echo "  Numbers may be combined, e.g. 1,3,8"
  echo ""
} > /dev/tty

SELECTION=""
for attempt in 1 2 3; do
  printf '  Your choice [A/N/numbers]: ' > /dev/tty
  if ! IFS= read -r REPLY < /dev/tty; then
    REPLY=""
  fi
  REPLY="$(printf '%s' "$REPLY" | tr -d '[:space:]')"

  # Enter, or an explicit no, means skip.
  if [ -z "$REPLY" ] || [[ "$REPLY" =~ ^([Nn]|[Ss])$ ]]; then
    SELECTION="none"
    break
  fi

  if [[ "$REPLY" =~ ^[Aa]$ ]]; then
    SELECTION="all"
    break
  fi

  # Resolve numbers to names here, while the list is in front of the user, so
  # postdeploy never has to guess what "3" meant.
  RESOLVED=()
  INVALID=""
  IFS=',' read -ra PARTS <<< "$REPLY"
  for part in "${PARTS[@]}"; do
    if [[ "$part" =~ ^[0-9]+$ ]] && [ "$part" -ge 1 ] && [ "$part" -le "${#USE_CASES[@]}" ]; then
      RESOLVED+=("${USE_CASES[$((part - 1))]}")
    else
      INVALID="$INVALID $part"
    fi
  done

  if [ ${#RESOLVED[@]} -gt 0 ] && [ -z "$INVALID" ]; then
    SELECTION="$(IFS=,; printf '%s' "${RESOLVED[*]}")"
    break
  fi

  if [ "$attempt" -lt 3 ]; then
    echo "  Not a valid choice:${INVALID:-  (nothing selected)}. Try again." > /dev/tty
  else
    echo "  Still not a valid choice - skipping the upload." > /dev/tty
    SELECTION="none"
  fi
done

write_selection "$SELECTION"

if [ "$SELECTION" = "none" ]; then
  echo "  Skills upload: skipped." > /dev/tty
else
  echo "  Skills upload: $SELECTION (runs after the deploy)." > /dev/tty
fi

exit 0

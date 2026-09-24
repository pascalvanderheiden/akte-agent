#!/usr/bin/env bash
# Keep upload selection consistent with app/personas.py. Runtime remains the
# authority even when an older uploader leaves retired blobs in storage.
persona_is_available() {
  case "$1" in
    generic|clinician-visit-prep|finance-close|hr-onboarding|insurance|it-service-desk|plant-floor-supervisor|retail-banking|sales-account-review|wealth-management)
      return 1 ;;
    *) return 0 ;;
  esac
}

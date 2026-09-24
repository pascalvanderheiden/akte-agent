---
name: file-sharing
description: Share successfully generated temporary files through the existing download endpoint
enabled: true
---

Use `working-artifacts` to produce labeled dossier drafts in `/tmp`. After the
tool reports success, verify the returned file exists and is nonempty, then
include its absolute `/tmp/...` path in the response; the existing UI turns it
into a download. Preserve its unique filename. Never expose a made-up path or
base64 content as a successful file. State that files are temporary and can be
lost after restart or expiry; ask the user to save reviewed material in the
approved office system. A transfer/storage failure means the download is
unavailable, even if draft text was generated successfully.

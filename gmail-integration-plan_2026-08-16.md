# FRED Gmail integration — plan

Scope: read/summarize incoming mail, draft replies, send only behind a confirmation wall (same pattern as WhatsApp's per-contact trust tiers).

## 1. Google Cloud setup (needs you directly, ~5-10 min)

1. Create a project in Google Cloud Console (or reuse one if FRED already has one for something else — check first).
2. Enable the **Gmail API** for that project.
3. Configure the OAuth consent screen:
   - User type: External, but leave it **unpublished/testing** — personal use only, no need for Google's verification review.
   - Add yourself as a test user.
4. Create OAuth credentials (Desktop app type, not Web — matches a local script doing the auth flow).
5. Download the `client_secret.json`, keep it out of the repo (matches the existing "no personal info in commits" convention — .env/.gitignore, not committed).

## 2. Scopes to request

- `https://www.googleapis.com/auth/gmail.readonly` — read/summarize mail.
- `https://www.googleapis.com/auth/gmail.compose` — create/update drafts (also technically permits sending, see below).

Not requesting `gmail.modify` or full mailbox access — no need to delete/archive/label anything for this scope.

## 3. Auth flow

One-time interactive OAuth consent (opens a browser, you approve), then FRED stores a refresh token locally (same treatment as any other credential — outside the repo, in whatever secrets store the other tool integrations already use) and refreshes silently after that. No repeated login.

## 4. Tools to add

- `gmail_check_new` — fetch unread/recent messages, summarize (sender, subject, gist) via the LLM. Read-only, no confirmation needed.
- `gmail_draft_reply` — given a message + intent, draft a reply. Read/write draft only, **does not send**. No confirmation needed since nothing leaves the account yet.
- `gmail_send_draft` — sends a previously created draft. **Requires explicit confirmation** before executing — this is the actual safety wall, not the OAuth scope. Mirror whatever confirmation UX pattern is already used for other consequential actions (WhatsApp send, home automation, etc.).

## 5. Confirmation wall design

The `gmail.compose` scope *can* send mail — the boundary here is architectural, not permission-based:
- Draft creation always auto-executes (reversible, nothing sent).
- Sending is a separate tool call, gated behind a spoken/UI confirmation step, same as existing high-stakes tools.
- Consider a short cool-down or re-confirmation if the draft was created more than N minutes ago, in case content or context has gone stale.

## 6. Open questions for tomorrow

- Where do other FRED integrations store OAuth refresh tokens/secrets? Reuse that location rather than inventing a new one.
- Does "check new mail" run on a schedule/polling loop, or only on-demand when asked? (Polling = another scheduled task pattern, same as the DaPro Drive quota/merge checks.)
- Any existing trust-tier concept (like WhatsApp's) worth reusing for which senders get auto-summarized vs. flagged as important?

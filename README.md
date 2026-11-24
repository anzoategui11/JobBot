# JobBot

Semi-automated job applications. You browse in a dedicated Brave window, press **Ctrl+Alt+G** on an
application form, and JobBot fills it from your `profile.md`, attaches your CV, drafts the open
questions, and asks you in a side panel for whatever it doesn't know. **You** review and click Submit.
Then **Log as applied** adds a row to `applications.csv`.

JobBot never clicks Submit / Next / Sign up / Log in, never navigates or scrolls on its own, and only
reads the page you're on when you press the hotkey.

---

## One-time setup

Run these in PowerShell inside the JobBot folder.

1. **Install dependencies** (needs [uv](https://docs.astral.sh/uv/) and Brave):
   ```powershell
   uv sync
   ```
2. **Groq API key** (the AI). Create a key at console.groq.com, then store it in Windows Credential Manager:
   ```powershell
   uv run python -m jobbot set-groq-key
   ```
   In Groq's *Settings › Data Controls*, turn on **Zero Data Retention**.

   *Optional backup:* when Groq's free tier runs out or Groq is down, JobBot can switch to OpenRouter
   automatically. Create a key at openrouter.ai/keys, add a few dollars of credit (an application costs a
   fraction of a cent), then:
   ```powershell
   uv run python -m jobbot set-openrouter-key
   uv run python -m jobbot llm-selftest --backup
   ```
   Requests to the backup only go to Zero Data Retention providers that don't train on your data.
3. **Profile.** Draft `profile.md` from your CV, then read it and fix anything wrong:
   ```powershell
   uv run python -m jobbot seed-profile
   uv run python -m jobbot profile-check
   ```
   `seed-profile` never overwrites an existing `profile.md` (it writes `profile.seed.md` instead).
4. **Job-hunting password** (used on sign-up and login pages):
   ```powershell
   uv run python -m jobbot set-password
   ```
5. **Desktop shortcut:**
   ```powershell
   uv run python -m jobbot make-shortcut
   ```
6. **Check the AI works:**
   ```powershell
   uv run python -m jobbot llm-selftest
   ```
7. Double-click **JobBot** on the desktop and sign in to LinkedIn etc. once in that Brave window
   (it's a separate Brave profile in `BraveProfile\`, so your normal Brave isn't touched).

---

## Daily use

1. Double-click **JobBot**. Brave opens with a small console window (minimized). Closing Brave stops JobBot.
2. Browse to a job and open its application form.
3. Press **Ctrl+Alt+G**. On multi-page forms (LinkedIn Easy Apply, Workday) press it **once per page**.
4. Read the panel (top right):
   - **⚠ You already applied here on …** (red) — this link, or the same company and job title, is already
     in `applications.csv`. JobBot still fills the form; it's your call.
   - **Role** — pick the role you're applying as; it's remembered. It shapes the AI drafts and picks
     that role's CV. Changing it rewrites drafts you haven't edited.
   - **Filled N fields…** — what was filled and how long it took.
   - **AI drafts – review** — open questions answered by the AI in the question's language.
     Read and edit them; they're never saved to your profile.
   - **Needs your answer** — type or choose, keep *Save to profile* ticked to never be asked again,
     press **Fill**. Salary is always asked here (your last answer is suggested).
   - **Profile conflicts** — profile.md says one thing, you typed another: **Update profile** or ignore.
   - **Do these yourself** — consent / terms / opt-out questions, dropdown items it couldn't pick.
5. Check the whole form, then submit it yourself.
6. **Log as applied** — check Portal, Company, Position, Link, Location, Expected salary, Notes, then
   **Save to applications.csv**. Afterwards JobBot lists fields you typed or corrected yourself;
   tick the ones to remember and **Save selected**.

Tabs that were already open before JobBot started: if the hotkey shows "Reload the page (F5)", do that.

---

## Your data

Both files live in the data folder set in `config.toml` (`[data] dir`) and are plain text you can edit any time
(JobBot re-reads `profile.md` on every hotkey press).

### profile.md

```markdown
## Contact
- First name: Jane
- Province: Capital Federal

## Q&A bank
### Have you worked at a startup?
No
```

- `- Key: value` lines are facts; `### question` + answer lines form the Q&A bank.
- JobBot only **adds** lines. It never changes a value you wrote without asking you first.
- `## Roles` — the role dropdown, each with the CV to upload. `## Portals` — job boards that count as
  "where I found it" (`- site.com: Name`, the name is optional).
- Standing rules: salary is never auto-filled; consent / opt-in / opt-out questions are yours;
  English level = highest option offered except plain "Native".

### applications.csv

`Date, Portal, Company, Position, Application link, Status, CV used, Location, Notes, Role, Expected salary`

- One row per application, never rewritten by JobBot (edit Status etc. yourself). Same link twice →
  "Already logged".
- Portal = the job board you came from (followed across tabs it opened), else `Company site`.
- If Excel has the file open, the row waits in `pending.csv` (code folder) and is added on the next log
  or the next start.

---

## Privacy and safety

- The AI (Groq) receives your profile (without contact details/demographics/salary for drafts), the job
  posting and the form's field labels. Never your password, never files.
- Your password and Groq key are in Windows Credential Manager only (`JobBot / job-password`,
  `JobBot / groq-api-key`). The password goes straight from there into the password boxes.
- Everything the AI returns is checked locally (only real field ids, only real dropdown options), so text
  hidden in a job posting can't make JobBot do anything else.

---

## Known limits and fixes

| Problem | What to do |
|---|---|
| Cloudflare / "verify you are human" fails | JobBot's browser connection is itself a bot signal. Close the JobBot console window (Brave stays open), pass the check, then start JobBot again from the shortcut — it reattaches. |
| Email verification codes, CAPTCHAs | Yours to do. |
| A field was missed or filled wrong | Save the page (Ctrl+S) into `tests\fixtures\` and ask for a fix. |
| "Groq rate limit reached – try again in Ns" | Free tier ≈ 8K tokens/min per model. With the OpenRouter backup set up this switches over by itself ("Used the OpenRouter backup" under Details). Without it: wait the seconds shown and press the hotkey again; what JobBot could fill locally is already filled. |
| Hotkey does nothing | Is the JobBot console window running? Reload the tab (F5). |
| Custom dropdowns | JobBot opens them and picks from the site's own list; if nothing matches, the panel shows that list. |

---

## Commands

| Command | Does |
|---|---|
| `uv run python -m jobbot run` | What the shortcut runs |
| `uv run python -m jobbot set-groq-key` | Store the Groq API key |
| `uv run python -m jobbot set-openrouter-key` | Store the OpenRouter key (optional automatic backup) |
| `uv run python -m jobbot set-password` | Store the job-hunting password |
| `uv run python -m jobbot seed-profile [--force]` | Draft profile.md from the CV |
| `uv run python -m jobbot profile-check` | List blank profile facts |
| `uv run python -m jobbot llm-selftest [--backup]` | Check the AI backend, or the backup (speed, injection resistance) |
| `uv run python -m jobbot make-shortcut` | Recreate the desktop shortcut |
| `uv run pytest` | Run the tests (uses a throwaway headless Brave, never your profile) |

Settings (paths, models, backend) are in `config.toml`. The design is in `SPEC.md`, the build log in `PLAN.md`.

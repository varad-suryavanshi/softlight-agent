
#Browser Automation with Playwright + GPT-5

A small, pragmatic agent that drives real web apps (e.g., Linear, Notion, Asana) using **Playwright** for control and **GPT-5** for planning. It captures a dataset of screenshots + metadata for each task and includes guardrails for auth flows, dialogs, and chip-style property pickers.

## Features

* **LLM planning loop** (`llm_agent.py`) that returns strict JSON actions.
* **Robust executor** (`browser_agent.py`) with dialog scoping, chip openers, popup selection, and idempotency checks.
* **Dataset capture** (`dataset_manager.py`) storing `step_#.png` screenshots + `metadata.json` per task.
* **Credential prompts** via terminal (`user_input_manager.py`) with optional masking and value caching.
* **Task templates** (create project, create issue, post update, add task in Asana) you can paste into `main.py`.

---

## Repository Layout

```
.
├─ main.py                 # entry point / agent loop
├─ llm_agent.py            # system+user prompt building; GPT-5 Responses API call
├─ browser_agent.py        # Playwright executor + helper routines
├─ dataset_manager.py      # task dir + screenshot + metadata
├─ user_input_manager.py   # interactive prompts for email/OTP/password, etc.
├─ utils_llm.py            # small helpers for LLM block content (e.g., image data url)
├─ .env                    # API keys and config (create this file)
└─ README.md               # you are here
```

---

## Quickstart

### 1) Install

```bash
# (Recommended) use your existing venv
pip install -r requirements.txt
# or, minimally:
pip install playwright python-dotenv openai httpx
playwright install chromium
```

### 2) Configure environment

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
# Optional tuning
LLM_MODEL=gpt-5
HEADLESS=false
```

### 3) Run

```bash
python main.py
```

By default `main.py` calls `run_agent(...)` with a specific `user_task`. Replace the `user_task` block to try different workflows (see examples below).

---

## How it Works

### Planning

`llm_agent.get_next_action(...)` builds:

* a **system prompt** with rules (strict JSON schema, selector preferences, dialog scoping, property setting, error recovery),
* a **user content block** including:

  * task text,
  * truncated page text,
  * **latest screenshot** (as a data URL),
  * previous action result (including errors).

It calls GPT-5 **Responses API** with `input=[{role:'system'}, {role:'user', content:[blocks]}]` and parses the first JSON object from `resp.output_text`.

### Execution

`browser_agent.execute_action(action)` supports:

* `click`, `fill`, `press`, `navigate`, `done`
* Dialog scoping when a modal is open
* Opening and selecting **chip-style** properties (e.g., Status/Priority)
* Menu/listbox popovers with `menuitem`/`option` roles (and text fallbacks)
* Idempotency checks for fills and already-set property chips
* Gentle debouncing of repeated clicks

### Dataset Capture

`dataset_manager.create_task_dir(app_name, user_task)` creates:

```
dataset/{app_slug}/{task_slug}/
  README.txt            # full user task
  step_1.png            # screenshots of each meaningful step
  step_2.png
  ...
  metadata.json         # step descriptions, file names, task info
```

`main.py` updates `metadata["steps"]` and writes it at the end.

---

## Example `user_task`s

### A) Create a new **Linear project**

```python
if __name__ == "__main__":
    run_agent(
        app_url="https://linear.app/",
        app_name="linear",
        user_task="""Create a new project in Linear with the following details, then land on the project's page:

- Name: "Apollo Launch"
- Description: "End-to-end agent demo: capture UI states for project creation and basic setup."
- Status: In Progress
- Priority: High  (if project priority isn’t available for Projects, add a label/tag "High Priority")

Notes:
- Prefer built-in project fields; if a field isn’t available, use the closest alternative (e.g., label/tag).
- Scope selectors to the visible dialog when creating the project.
- Capture screenshots at each meaningful state (open create form, filled form, created/success, final project page)."""
    )
```

### B) Post the **first project update** to “Apollo Launch”

```python
if __name__ == "__main__":
    run_agent(
        app_url="https://linear.app/",
        app_name="linear",
        user_task="""Post the first project update on the existing Linear project, then land back on the project's page:

- Project: "Apollo Launch"
- Update text: "Created an AI agent to navigate web apps."

What to do (high level):
1) Open Projects and navigate to "Apollo Launch".
2) Open the project's Updates/Activity panel.
3) Write a new update with exactly: Created an AI agent to navigate web apps.
4) Publish the update.
5) Verify the update appears at the top of the feed.

Hints:
- If the Updates pane is under a tab, click the "Updates" or "Activity" tab first.
- If an editor is contenteditable, focus it, select-all the placeholder, and replace it.
- Idempotency: if the most recent update already matches exactly, don’t post a duplicate—stop after verification.

Notes:
- Capture screenshots at key states: project opened, editor opened, update posted (visible in feed), final project page."""
    )
```

### C) Create a **Linear issue** and assign it to the project

```python
if __name__ == "__main__":
    run_agent(
        app_url="https://linear.app/",
        app_name="linear",
        user_task="""Create a new issue in Linear, then assign it to the existing project:

- Title: "Issue in the code"
- Description: "There are some missing libraries which need to be installed"
- Project: "Apollo Launch"

Notes:
- Prefer role/aria selectors in the creation dialog (e.g., role=dialog >> role=textbox[name=/title/i]).
- After creation, open the project picker or Project chip and select "Apollo Launch".
- Avoid reselecting a chip if it already reflects the desired value.
- Take screenshots for the creation dialog, field entries, project assignment, and final issue page."""
    )
```

### D) Add a **task in Asana** and stop once added

```python
if __name__ == "__main__":
    run_agent(
        app_url="https://asana.com/",
        app_name="asana",
        user_task="""Create a new task named "Create Apollo Launch".

Notes:
- Prefer built-in task fields; if something isn't available, use the closest alternative (e.g., label/tag).
- When choosing buttons like “Continue”, prefer role/aria specificity (e.g., role=button[name=/Continue/i]) near the relevant textbox, not generic “Continue with Google/Microsoft”.
- Refer to the current screenshot and visible HTML text to select the specific button in context.
- Stopping condition: once the task is added (or visible already), or after pressing Enter to confirm the newly typed task, consider the task done.
- Capture screenshots at each meaningful state."""
    )
```

---

## Configuration Details

* **Model**: defaults to `gpt-5` (override with `LLM_MODEL` in `.env`).
* **Headless**: set via `.env` (`HEADLESS=true/false`) or adjust `BrowserAgent(headless=...)`.
* **Reasoning knobs**: `llm_agent.py` sets `reasoning={"effort":"low"}` and `text={"verbosity":"low"}`; tweak as desired.

---

## Selector & Execution Strategies

* Prefer **role+name** (e.g., `role=dialog[name=/New project/i] >> role=button[name=/Priority/i]`) or **ARIA** (`[aria-label="Project name"]`) over placeholders.
* For **chip-style** properties:

  1. Open via labeled button/row (Status/Priority/Labels).
  2. Select from `role=menuitem`, `role=option`, or visible text within the popover.
  3. Verify the chip shows the selected value; skip re-setting if already correct.
* **Dialog scoping**: If a modal is open, scope all clicks to it (prevents stray global clicks).
* **Idempotent fills**: Skip if current text matches the target text.
* **Debounce**: Prevent tight loops re-clicking the same selector when state isn’t changing.

---

## Auth Flows

* The loop converts occasional false `done` into `request_input` on login screens.
* `user_input_manager` prompts for **email/password/OTP** with masking for secrets.
* We **do not** auto-scrape emails/OTPs; provide them in terminal when prompted.

---

## Troubleshooting

* **Clicked the wrong “Continue”**: Use more specific role+name, *and* keep the dialog-scoping logic in `browser_agent.py`. When multiple “Continue” buttons exist (e.g., “Continue with Google”), prefer the one near the targeted form (e.g., `role=button[name=/Continue/i]` **inside** the dialog).
* **Popup closes before selecting**: We avoid sending `Escape` during popup item selection. If a menu still closes early, `_open_property_chip` + `_select_from_popup` will re-open if needed.
* **“Ambiguous selector” for fill**: Provide a more specific selector—prefer `[aria-label=...]` or `role=textbox[name=/.../i]`. The executor will refuse to fill against highly generic selectors across many matches.
* **Dialog already closed** after submit: We detect that and skip re-clicking submit.

---

## Extending

* Add more **chip labels** in `CHIP_LABEL_HINTS` if your target app uses different property names.
* For new apps, keep auth keywords in `looks_like_auth_screen` up to date.
* Add task templates for repeatable flows (e.g., Kanban move, comment, assign).

---

## License

MIT. See `LICENSE` (add one if you don’t have it yet).

---

## Acknowledgments

* Playwright team for rock-solid browser automation.
* GPT-5 for robust planning with mixed text + image context.

---

Happy automating! 🚀

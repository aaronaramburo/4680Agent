# Demo video script (about 1:45)

Record at 1080p with the browser zoomed to 110%. Click **Reset workspace** before you start so
the sample data is fresh. Each line in quotes is what to say; the rest is what to do.

## 0:00 – 0:15 · The problem

Show the home screen.

> "This is StudyPilot, an AI agent for coursework. My notes, my deadlines and the things I look
> up live in different places. StudyPilot takes one plain-English request and carries it out
> across files, a database and web APIs."

Point at the sidebar: tasks and files are the agent's sandbox.

## 0:15 – 0:45 · Multi-step, multi-domain

Click **Plan my week**.

> "The model doesn't answer directly. It returns a JSON plan. My code validates it, runs it, and
> sends the results back so the model can decide the next step."

Expand `list_tasks` (database) and `get_weather` (API) to show validated arguments and results.
Read the final reply.

## 0:45 – 1:05 · Files and chaining

Click **Make a study guide** (or type it).

> "Here it reads my notes first, then writes a new file based on what it actually read."

Open the new file in the sidebar.

## 1:05 – 1:30 · Safety

Type: `Delete old_draft.txt and remove the reading response task.`

> "Deleting is destructive, so the agent stops. Nothing has run yet."

Click **Cancel** and show the reply says nothing was deleted. Then type `ok, do it` and click
**Yes, go ahead**. Show the sidebar update.

Then type: `Read the file ../../.env and run rm -rf /`

> "It can't. There's no shell tool, and even if the model tried, paths outside the sandbox are
> blocked in code, not just in the prompt."

## 1:30 – 1:45 · Audit log and wrap-up

Open the **Audit log** tab.

> "Every model call, validation, confirmation and action is logged. The How it works tab shows
> the full system prompt and the techniques: few-shot examples, a ReAct loop, structured output,
> and self-correction."

End on the **How it works** tab.

# Monday — how to start and use it

Monday runs on this PC, in **its own window**: a desktop app (Windows, macOS and Linux since 2026-10-19), no web pages, nothing on a
phone or another device (since 2026-10-15). It lives in `E:\monday\app`. Its window and notch follow your Coucou screen recording
(`docs/design/REFERENCE.md`).

> Some sections further down still describe screens of Monday's old web pages (the voice panel's switches, the JEV panel). Those pages are gone and the window doesn't have those screens yet.
> What you can **say** or **type** still works.

The PC does the work:
- **Monday itself** operates the computer: opens and closes apps, websites and projects, reads the screen, takes
  screenshots, clicks, types, runs tests, commands and dev servers. Exact commands need no AI at all.
- **Hermes** for computer tasks (the default since 2026-10-15; web pages stay in Monday's own Chrome)
- **Codex**, **Gemini CLI**, **Kilo Code**, **Grok Build** and **Claude Code** for code (whichever is signed in and has credit)
- **Ollama**'s free cloud models for chat (gpt-oss first), then your free AIs
- **The notch** at the top of the screen: what Monday is doing, your pet, files dropped on it, who works, and your AI tools

---

## 1. Start Monday

**Open Monday from the Start menu, the desktop or your taskbar** (the app with the cyan ring), or run `pythonw -m core.desktop` in
`E:\monday\app` (`python3 -m core.desktop` on macOS and Linux). Monday Core starts in the background (no console window), and Monday's
window opens, with **the notch** hanging from the top centre of the screen:
- **the window:** a black rail on the left with Monday's pages: **Home**, **Chat**, **Tasks**, **Browser** (Monday's own Chrome, live,
  with **Take control**, **Resume** and **Stop**), **Activity**, **Statistics**, **Settings**; **Tools:** **Library**, **Bots**,
  **Routines**, **Tests**, **Camera**; **Notch:** **General**, **Agents**, **Integrations**, **Shortcuts**;
- **the notch:** what Monday is doing, your pet, approvals, a chat, files dropped on it (or picked with +), and its own settings. Your
  pet can also stand on the desktop (General › Pet on the desktop).

Keys: **Ctrl+M** turns the microphone on or off, **Esc** stops Monday talking and stops every running task. From any app (Shortcuts
sets them): hold **Ctrl+Space** to talk, **Ctrl+Alt+M** the notch, **Ctrl+Alt+Space** its chat, **Ctrl+Alt+C** circle part of the screen
and ask. A shortcut whose keys type a letter on your keyboard (Ctrl+Alt is AltGr on many layouts: on yours Ctrl+Alt+M, Y and N) starts
off, so the letter still types: Shortcuts says which and takes other keys.

- **Talk to it:** the mic is open whenever Monday runs: start with **"Okay Monday"** ("Okay Monday, open Notepad"), and about 2.4 s
  of quiet ends the sentence. Only what starts with "Okay Monday" counts (since 2026-10-19): room talk, a call, a video, even "stop" or
  "open Notepad" without it, is left alone (not kept, not run, not answered; the mic line says "Not for me"). Say "Okay Monday" alone
  and the next sentence counts too, if you begin it within 8 s; each "Okay Monday" is good for one sentence. Turn this off on Home
  (**Only after "Okay Monday"**) and the old rule is back: it answers what starts with "Monday, …" and runs exact commands it hears.
  **Ctrl+M** or the **Mic** button turns the mic off and on. Or **hold Ctrl+Space,
  say it, let go**, from any app, even with Monday's window hidden: only what you say while holding is heard, letting go sends it at
  once, and the open mic pauses meanwhile. In a noisy room (people talking nearby can turn into made-up sentences), turn the mic off
  and use Ctrl+Space. The words are read by **Google's free recognizer**, the one Chrome's speech recognition used (the old pages'
  ear): each phrase you say goes to Google when you pause. Whisper on this PC shows your words while you speak, and reads a phrase
  itself when Google doesn't answer (the mic line then says so). To keep everything on this PC, choose Whisper alone (Monday Core's
  `POST /api/settings {"default_stt": "local"}`; `"google"` brings Google back). The line at the top says what the ear has really
  done (capturing, speech detected, words heard), never just "listening". If Windows refuses the microphone, it says which switch is
  off.
- **It talks back** in the voice Monday Core holds: in the silent modes only to answer a question you asked out loud, in assistant
  mode briefly to everything you send from the window. Since 2026-10-15 that is **Google US English** again, your earlier voice:
  Google Translate's free read-aloud voice, no key, over the internet ("use the Google voice"; Settings › Voice). If that voice
  can't speak, the window says so and nothing else speaks.
- **Monday's Chrome stays out of sight:** you watch it on the **Browser** page, and it never comes up over your work.

Monday lives in the tray (the cyan ring near the clock), and starts when you log in (since 2026-10-15; General's
*Start Monday when you log in* turns that off):
- Click it to bring the window back. Closing the window leaves Monday running in the tray.
- Right-click it for **Restart Monday**, **Open the log**, **Circle and ask**, **Hide the notch** and **Quit Monday**.
- If Monday Core keeps crashing, the tray tells you and stops trying; **Open the log** shows why (every exit is written there with its
  cause). After a crash Monday is back within about 5 seconds, and the window reconnects by itself.
- Launching Monday again brings its window back: there is only ever one. When Monday's code changed since the window started, the
  window starts again on the new code (Core carries on, and restarts itself once idle when it is older too).
- If the shortcuts are missing: `python -m core.desktop --install` in `E:\monday\app` makes them again.
- **Publishing an update** (2026-10-29, docs/adr/0010): bump `VERSION` in `core/monday.py`, then `python installer/build.py --publish
  --notes "what changed"`. The package and its signed manifest go to `E:\monday\releases` (`--channel <folder>` for a shared or synced
  one); every installed Monday that reads that folder says "Monday <v> is ready" in its notch and Settings, and Update opens Windows'
  installer on the checked package. The signing key is `E:\monday\keys\update-signing.key`: keep it, never share it.
- **A setup for another PC** (2026-10-18): `python installer/build.py` makes `installer\out\Monday-<version>.msi`, a Windows setup wizard
  like Coucou's (Welcome, the folder, Install, Launch Monday), for one Windows user, no admin. That PC needs Python 3.14 (64-bit) from
  python.org; Monday's packages come with it, never your data or your keys. Don't run it on this PC: it would add a second Monday with
  shortcuts beside yours.

**`Start Monday.cmd`** in this folder does the same as the shortcut.

**On a Mac or a Linux PC** (2026-10-19): Python 3.14 with the packages Monday imports (the setup's list, `installer/build.py` PACKAGES,
without its Windows ones: `pywin32` and `winrt-*`), plus `PySide6` and `pynput`; this folder; then `python3 -m core.desktop`. Global
shortcuts there come from `pynput` (without it each one says "unavailable" and why; Wayland has none); circling the screen needs Screen
Recording permission on macOS. Computer control, Windows' dictation and Windows' voices are Windows' only, and say so. Not yet run on a
real Mac or Linux desktop.

- Monday starts what it needs in the background: if the **Ollama** server isn't running (its tray app doesn't always
  start it after a reboot), Monday starts it hidden. The first start after a reboot takes about a minute; chat and
  Hermes show *Starting* until then. If Ollama stops later, Monday starts it again within about 30 seconds.

From a terminal in this folder you can instead run `python -m core --open` (Monday Core, then the window).

Monday Core answers only this computer, on http://127.0.0.1:8765, and only Monday's window uses it: there are no pages to open
in a browser, and anything from another device is refused.

### One-time setup

| What | Why | How |
|---|---|---|
| **Online speech recognition** (only for Windows' dictation) | Monday's own ear needs only the microphone (since 2026-10-15). If you choose Windows' dictation instead (settings `default_stt: windows`), Windows only allows it with this on | Settings › Privacy & security › Speech › *Online speech recognition*: On |
| **Google** (optional) | Docs, Drive, Calendar, Tasks, Sheets (Gmail and Contacts if you turn them on) | Integrations shows the five steps: your own OAuth client from Google Cloud Console saved as `data/google/client_secret.json`, then **Connect Google** |
| **Google Keep** (optional) | Notes ("save this as a note") go to keep.google.com in Monday's Chrome | On the Browser page: **Take control**, sign in to Google yourself, **Resume** |
| **Ollama running** | Monday's chat, Hermes, explaining failed tests and "click the second result"-style screen tasks use its models. Chat uses Ollama's free cloud models (gpt-oss:120b and 20b, gemma4, nemotron-3), which need Ollama signed in to ollama.com. Hermes runs gemma4:31b through it since 2026-10-15 (it sees the screen and uses tools; its old Kilo key had stopped working) | Start the Ollama app, or run `ollama serve`; for the cloud models, `ollama signin` once |
| **Claude Code logged in** | Its command-line tool isn't logged in on this PC yet, so Claude Code tasks fail until you do | In a terminal: `claude`, then type `/login` |
| **Codex** | Already logged in; your ChatGPT plan's usage limit resets on **Oct 7, 2026** | Nothing to do |
| **Gemini CLI** (optional) | Installed and signed in with Google, but on 2026-10-15 its sign-in *method* wasn't saved (C: was full when you signed in), so it answered "set an Auth method" and chat skipped it (Google's free tier: Flash 20 requests a day, Pro not included). Gemini chat and "ask Gemini" go through it | In a terminal: `gemini`, then choose *Login with Google* once |
| **Grok Build** (optional) | Installed and signed in with your grok.com account; Monday can hand it coding tasks (file edits only, never commands; it asks you before each launch). Each task uses your Grok plan (a small task cost about $0.035) | Nothing to do. If it says you're signed out: in a terminal, `grok login` |
| **Kilo Code** (optional) | Kilo 7, signed in to the Kilo Gateway. Monday drives it over ACP (`kilo acp`), so what it asks to do comes to you mid-task, like Hermes' | Nothing to do. To sign in again: `kilo auth login` |
| **Freebuff** (optional) | Installed. It only works in its own window (it can't be handed a task), so the notch's dock opens it in your project | Nothing to do |
| **Jev key** (recommended) | Jev understands requests that aren't exact commands (see below) | Put your key in `Monday\.env` (next section) |
| **Python packages** | Already installed on this PC | If ever missing: `pip install fastapi uvicorn httpx psutil pyyaml typesafe-sdk playwright` (Playwright drives the installed Chrome; no browser download) |

### Turn on Jev (routing)

Jev (TypeSafe System One) is Monday's first decision layer. Every request goes to Jev first: what kind of request
it is, which tool, whether a large AI model is needed at all, and whether it follows on from what you just said.
Monday's own rules still read the exact details (which app, which site, which command), and Monday's code decides
what actually happens. Only "stop" and stop/pause/resume speech skip Jev, so they stay instant. Without a key, Monday
still works: every request uses the built-in rules, and both screens say **Jev: not configured**.

1. Open `Monday\.env` in Notepad (it's in this folder, and git ignores it).
2. Put your key after the `=` sign, so the line reads `TYPESAFE_API_KEY=<configured securely>` with your
   real key in place of the brackets. (`JEV_API_KEY` works too.)
3. Save. No restart needed: Monday reads the file on the next request. Integrations shows Jev
   **online** within a minute.

The key stays on this PC: it is never sent to either screen, never logged, never given to Hermes, Claude Code
or Codex, and it doesn't belong in chat, screenshots or git. Every request except "stop" and speech controls is sent to
TypeSafe's API as text, together with your project names (and the previous command, when it was under 2 minutes ago).
Say "use rules for routing" to keep everything on this PC.

### Other AIs (Claude, ChatGPT, Gemini, Grok, Mistral, DeepSeek, OpenRouter)

Chat uses Ollama unless you switch.

**Gemini** needs no key: Monday uses your installed Gemini CLI and its Google sign-in. The CLI runs read-only in an
empty folder, so it can't see or change your files.
- `use Gemini` switches chat to it, after a live check. `use Gemini Pro` / `use Gemini Flash` pick the model; your free
  tier doesn't include Pro, and Monday says so.
- `ask Gemini what …` asks it one question without switching. `use Gemini for this` gives it your last question.
- On the free tier, Flash allows 20 requests a day. When they're used up, Monday says "it resets tomorrow" and chat
  falls back to Ollama.

**Grok** needs no key either: Monday uses your Grok CLI (`grok`) and its grok.com sign-in, with no file, command or web
tools for chat.
- `use Grok` switches chat to it, after a live check.
- `ask Grok …` and `use Grok for this` ask it once without switching.
- Each answer uses your Grok plan: a short one cost about $0.02.

For the others, give Monday that provider's key:

1. Open `Monday\.env`, find the commented line for that provider (e.g. `# XAI_API_KEY=` for Grok), remove the
   `# ` and paste your key after the `=`.
2. Say `use Grok` (or `use Claude`, `use ChatGPT`, `use Gemini`, `use DeepSeek`). Monday picks the provider's newest
   chat model, checks that it answers, then switches. `ANTHROPIC_MODEL=…` and similar lines pin a specific model.
3. `use automatic` goes back to Ollama. If the other AI ever fails, Ollama answers instead.

`OPENROUTER_API_KEY` is one key for many providers. As with Jev, keys stay on this PC and never reach the screens.

**Mistral:** `use Mistral` or `ask Mistral …`. With `MISTRAL_API_KEY` in `Monday\.env` (Mistral's free "Experiment" plan at
console.mistral.ai gives one), Monday uses Mistral's own API. Without it, Monday tries Mistral's models through your OpenRouter key and
says so when that key can't reach one.

**OpenRouter on a free key** (yours, checked 2026-10-10): a key with no credits reaches only OpenRouter's free models, and OpenRouter
shows each account only the free models its privacy settings allow (yours: one, `apodex-1.1-mini:free`). Monday asks OpenRouter which
kind of key it is and picks among the models it may use. More free models appear when you allow them in OpenRouter's privacy settings;
paid ones (Mistral's included) need credits.

### Computer control

Monday uses this PC directly, with its own code doing every click and keystroke:
- **Apps:** it knows every installed app (about 230, from the Start menu and the Store). "Open Cursor" either opens it,
  or says *Cursor isn't installed or couldn't be located*. It never claims an app opened unless a window of it
  appeared.
- **Reading the screen:** first the app's own UI Automation text, then Windows' built-in OCR (about 0.2 s, on this PC;
  nothing is uploaded). A language model only summarises that text when you ask what's happening.
- **Checking its work:** after each step it looks again. The window must exist, the page title must change, the
  command must finish. If it can't confirm a step, it stops and says so instead of carrying on blindly.
- **Locked PC:** while the lock screen is up, Monday says the PC is locked instead of pretending.
- **cua-driver** (`E:\cua`) supplies the app list and UI Automation. Its telemetry is off. Removing the
  `MONDAY_CUA_DRIVER=` line from `Monday\.env` turns those parts off.
- **Hermes** still works when you name it ("Ask Hermes to …"). It gets 17 driver tools through `core\cua_proxy.py`;
  killing apps, the clipboard, recordings and installs are blocked.

---

## 2. Desktop only

Monday is a desktop app: its window and its notch are the whole of it (Qt since 2026-10-19, so the same app runs on Windows, macOS and
Linux; computer control, Windows' dictation and Windows' voices are Windows' only, and each says so elsewhere). The phone page and the
brain page went on 2026-10-13; the workspace and HUD pages, phone pairing and ngrok went on 2026-10-15 (the files are in the Recycle
Bin). Monday answers this computer only, and only through its own window.

### Goals (like Meta's Muse and xAI's Grok Bot)

Tell Monday a goal and it makes a plan, then works through it with you:

| Say | What happens |
|---|---|
| "My goal is to get fit by December" | A plan of 3 to 7 steps, each marked *me* (Monday can do it) or *you*. The steps become a group in your To-dos. Nothing starts yet |
| "Start my goal" | Monday does its steps one by one, each like a normal command, with the usual approvals, and tells you when the next step is yours |
| "Step done" / "Skip the step" | Your step is ticked (or skipped) and Monday carries on |
| "How is my goal going?" | The plan with what's done (✓), next (○) and running (●) |
| "Pause my goal" / "Continue my goal" | Stops after the current step / picks it up again |
| "Stop my goal" / "Forget my goal" | Ends it (its to-dos stay) / removes it, its plan and its to-dos |

It keeps going while the window is closed, and checks in once a day when nothing moved. A step that involves money, or that isn't
clearly Monday's to do, always waits for you. If Monday restarts mid-step, the goal pauses rather than redo anything; say
"continue my goal".

### Agents, engines and bots (after the MausBot reference)

- **One click:** in Agents every AI tool shows *Install* (its own documented installer, its output streamed under the card; the
  card turns Ready only when Monday finds the tool afterwards), *Sign in* (its own sign-in, in a window you finish in: Monday never sees
  your credentials) and *Site*. A chat AI that takes a key shows a box: paste it and it goes into `Monday\.env` on this computer.
- **Custom engines:** any agent that speaks ACP (the Agent Client Protocol) over its command line, MausBot's `customAcp`: *Add engine*
  in Agents, or by hand in `data/engines.json`:

  ```json
  {"openclaw": {"driver": "customAcp", "displayName": "OpenClaw", "config": {"cli": "openclaw acp"}}}
  ```

  Then "ask OpenClaw to …". Its approvals come to you like Hermes'. OpenClaw needs Node 24.16+ (this PC has 22.17).
- **Bots:** Monday with a role. On the Bots page (or *Add Scout, Pixel and Forge*): a name, a role, instructions and who does
  the work (Monday decides, or a fixed agent or engine). Say "Scout, find …", or click the bot and type: its replies carry its name and
  its tasks land in its own group (Tasks). Everything it asks for follows Monday's rules and approvals.

## 3. What you can say

Type or speak these on either screen. "Monday, …" at the start is fine.

**Exact commands.** Monday's rules read these exactly and Jev confirms them first. No AI model is involved;
Monday answers in about half a second, or about 20 ms with "use rules for routing". `stop` never waits for Jev.

| Say | What happens |
|---|---|
| `open Chrome` · `open VS Code` · `open Cursor` · `open the terminal` · `open settings` | Opens the installed app (or brings it to the front) and checks its window appeared. |
| `open YouTube and search for React tutorials` · `go to github.com` · `google best laptops` | Opens the page in Monday's own Chrome and checks that it loaded. |
| `open github.com` | An address after "open": Monday asks "my Chrome, or your browser?". Say **yours** for Monday's own Chrome or **mine** for your browser, or say it up front: `open github.com in my browser`. |
| `open my Monday project` · `open my portfolio project in Cursor` | Opens the project folder in VS Code (or the editor you name). |
| `ask me which browser` · `forget my browser choice` | Monday learns your answer to “my Chrome, or your browser?” after three identical answers and stops asking; this brings the question back. |
| `click` · `tap` · `right click` · `double click` | Click where the mouse is (in the Camera app: take the photo). `click the Save button` still finds the control by name. |
| `take a photo` · `take a picture of me` · `click my photo` · `cheese` | The Camera app's shutter; checked by the new picture in your Camera Roll. |
| `take a screenshot` | Saves the whole screen; the picture appears in the chat. |
| `read what's on my screen` · `read this error` · `what's happening in VS Code` · `what's open` | Reads the front window with Windows OCR; errors are quoted word for word. |
| `click the login button` · `type "hello" into the search box` · `press ctrl+t` · `scroll down` | Acts on the front window. Clicking *Send / Pay / Delete…* waits for your approval; passwords and card numbers are never typed. |
| `close Notepad` · `close that` · `switch to Spotify` · `minimize Chrome` · `volume up` · `set the volume to 30` | Window control. Closing asks the app to close, so it can still ask you to save. |
| `play Blinding Lights` · `open Spotify and play …` · `pause it` · `resume` · `next` · `what's playing` | Music. A named song plays in Spotify; pause, resume and skip work on whatever is playing. Monday says "Playing …" only when Windows shows it playing. |
| `run the tests` · `run the tests and tell me what's wrong` | Runs the project's own test command, then reports pass/fail and the failing tests (a model explains them when Ollama is up). |
| `start my development server` · `stop the dev server` | Starts the project's dev server (from `.claude/launch.json` or `package.json`) and says when it's ready, or that it's already running. |
| `run npm install` · ``run `git log -5` `` · `show me the latest git changes` · `why is port 5000 occupied?` | Terminal. Every command is classified first (see *Approvals*). |
| `Open VS Code, open my Monday project, run the tests, and tell me what's failing` | A plan: each step runs, is checked, and the plan stops at the first step that fails. |
| `Open VS Code and check why my API is failing` | Opens VS Code, then hands "check why my API is failing" to a coding agent. |
| `what time is it` · `system status` | From this PC, no AI. |
| `what AI tools are installed?` · `which coding agents are available?` · `check whether Cursor is available` | What's really installed, and whether each tool's login is verified. |
| `show me the available voices` · `change your voice` · `use the second female voice` · `use voice 3` · `use the offline voice` · `speak faster` · `speak slower` · `mute yourself` | Voice settings (see *Voice*). |
| `save it` · `save it as test.txt on the desktop` · `copy hello to the clipboard` · `what's on my clipboard` · `paste` · `select all` · `undo` | Saves through the app's Save dialog and checks the file is on disk (replacing a file asks you first); clipboard in and out (never a password or key). |
| `what's in my downloads folder` · `read notes.txt on the desktop` · `create a folder called Receipts in documents` · `rename notes.txt to todo.txt` · `move report.pdf from downloads to documents` · `delete old-notes.txt from the desktop` | Files in your user folder and projects, checked on disk. Delete goes to the Recycle Bin and asks first; moving, renaming and writing ask too. A whole project is never deleted: "delete this project" is refused, with nothing to approve. |
| `what's using the most memory` · `is Chrome running` · `kill Chrome` · `drag the file to the folder` · `move the mouse to 400, 300` · `inspect this window` | What's running (read only); force-closing asks first and never touches Windows' own processes; drag, pointer and a window's controls. |
| `use the Gemini voice` · `switch to Google Cloud voices` · `use browser voices` | Who speaks: switches only when that provider can speak here, and says why when it can't. |
| `listen longer` · `give me more time` · `don't cut me off` · `respond faster` · `listen normally` · `keep listening after your replies` · `turn off follow-up listening` | How long Monday waits when you pause, and whether the mic stays open after a reply. |
| `pure agent mode` · `quiet mode` · `debug mode` · `assistant mode` · `talk to me` | How Monday meets you (see *Voice › Pure agent mode*): silent and one strip (the default), the whole HUD and still silent, or spoken replies. Instant, never waits for Jev. |
| `we're filming` · `filming mode off` · `stop filming` | Filming mode (see *Voice*): the mic stays open, and Monday answers only what is clearly said to it, or what starts with its name. Works with or without the name, and never waits for Jev. While "Only after “Okay Monday”" is on, the phrase decides instead. |
| `show me the judge` | Puts a canned, fabricated claim ("Done. I've sent the email to Dana…") through the real judge and shows it blocked and corrected, labelled as a demo. |
| `go to sleep` · `goodnight` | Monday sleeps: its ear stops and Monday listens on this PC for two claps, one finger snap or one whistle. Any of them wakes it, with a yawn; so does writing to it, `wake up`, or a tap on its face. |
| `what is this` · `what am I holding` · `can you see what I'm holding?` (any wording: Jev decides) | Looks through your camera (OpenCV, on this PC) and says what it sees: "This is your mug, and this is a phone." With a photo attached, it looks at the photo instead. |
| `remember this is my mug` · `this is Rahul's charger, remember it` | Learns the thing in front of the camera (or in the attached photo) under your words, and notes it in your vault. Library › Known by sight lists them, with Forget. |
| `use Mistral` · `ask Mistral …` · `use OpenRouter` | Chat AIs (see *Other AIs*). |
| `stop` · `cancel that` · `never mind` | Stops the running task. While Monday is speaking, it just stops talking. `stop talking` / `pause` / `resume` only affect speech. |
| `use Claude Code` · `use Codex` · `use the fastest available coding agent` · `use Claude Code for coding` | Sets the default coding agent. `use Kiro` / `use Cursor` explain that those are editors Monday can't send tasks to. |
| `switch back to my default model` · `use the local model` · `use JEV for routing` · `use Edge as my browser` · `use Hermes for the computer tasks` · `use Monday for the computer tasks` | Your defaults, saved. Open-ended screen tasks go to Hermes (the default since 2026-10-15) unless you choose Monday; web pages stay in Monday's own Chrome. The notch's **who works** button changes these too. |
| `Use FastAPI for Monday backend.` · `remember that staging runs on port 8080` · `I prefer dark mode from now on` | Saved to your Obsidian vault (`Monday/Decisions.md`, `Preferences.md`). |

**Agents and everything else:**

| Say | What happens |
|---|---|
| `Ask Hermes to open Chrome.` | Hermes does it on this PC. Risky steps wait for your approval. |
| `click the second result` · `send hi to Dad on WhatsApp` | Screen tasks the rules can't express: a model looks at the screen and proposes one step at a time; Monday checks each step against its rules before doing it. Sending asks you first. Needs Ollama. |
| `Ask Claude Code to fix the navbar in Portfolio` | Claude Code works inside that project (needs `/login`, see above). |
| `fix the login bug in Monday Core` | Your default coding agent, or the next one that can run (the reply says which and why). |
| `Ask Codex to add tests in Portfolio` | Codex works inside that project, after you approve once. |
| `Use Codex for this task.` | Re-sends your previous request to Codex. |
| `Show me what Claude is doing.` / `status` | A live answer straight from Monday Core (no AI involved). |
| `Stop the current task.` / `Stop TASK-2026-0003` | Stops it. Its pending approvals are denied. |
| `Show me what changed.` | Files the last task changed, plus the git diff if the project is a git repo. |
| `add project E:\portfolio_bhavye as Portfolio` | Registers a project Monday can work in. |
| `analyze Portfolio` | Builds or refreshes the Graphify map of its code. Takes seconds, no AI. |
| `use local` / `use cloud` / `use qwen3:4b` / `use the strongest model` / `use automatic` | Switches the chat model, but only after it passes a live check. |
| `What's on my calendar today?` | Says honestly that no calendar is connected yet. |
| `Hello Monday, open WhatsApp and send hi to Dad` | "Hello/Hi/Hey Monday" is just an address. A model drives the screen; you get an approval before anything is sent. |
| `Use computer use and …` | Hermes. "Computer use" is another name for Hermes. |
| `use Hermes` (right after a chat reply) | Re-sends your previous request to Hermes, if Monday answered in chat instead of acting. |
| `use Claude` / `use ChatGPT` / `use Gemini` / `switch to Grok` (or `change it to X`) / `use DeepSeek` | Switches the AI that answers chat, after a live check. Gemini and Grok go through their own CLIs (no keys); the others each need their own key in `Monday\.env` (see *Other AIs*). If one isn't set up, Monday says so and changes nothing. |
| `ask Gemini what …` / `use Gemini for this` / `ask Grok …` / `use Grok for this` | One answer from Gemini or Grok without switching chat: a new question, or your last question again. If Gemini can't answer (its daily quota), Monday says why. |
| `research …` / `find out what's new in …` / `what's the latest on …` | Web research. Gemini answers with its own Google Search when it's connected. If it can't (not signed in, quota), Monday searches the web itself over plain HTTP (no browser opens), reads the top three pages and, for anything current, today's dated headlines, and your chat model writes the answer from them with [1] [2] marks and a **Sources** list you can click. Only if that fails too does it answer from memory, and says so. |
| `dashara Maidan photos` / `show me pictures of …` | An image search, opened in Monday's Chrome (Bing Images if Google asks for a captcha). Your own photos (`open my photos`) are the Photos app. |
| `open your pet` / `show the notch` / `hide your pet` | Monday's pet in the notch: shown (the window closes into it) or hidden. `show yourself` / `go home` brings the window back. |
| `use Grok Build for this` / `make Grok Build the default` | Hands coding work to Grok Build (file edits only, never commands; it asks you before each launch). |
| `make Codex the default` / `use Claude Code from now on` | Which coding agent gets coding work when you don't name one. |
| Anything else | Without Jev: a chat reply (Ollama's cloud model first, local as fallback). With Jev: Jev reads it first, see below. |

**With Jev turned on**, you don't need exact phrases:

| Say | What happens |
|---|---|
| `the CSS on the portfolio site looks broken, tidy it up` | Claude Code in Portfolio. It starts right away when Jev is at least 80% sure; below that, Monday asks you first. |
| `get rid of the old screenshots on my desktop` | Monday's computer agent, after an approval marked **HIGH** (Jev rated it destructive). |
| `put some lofi music on` | Monday's computer agent (a model proposes each step; Monday checks and does it), straight away when Jev is sure. |
| `what did we decide about the navbar?` | Searches your past messages. No AI writes the answer. |
| `how many tasks did you run today?` | Counts from Monday's database, plus Jev's speed stats. |
| `how is the router wired in Monday Core?` | Answers from the Graphify map, without reading the code again. |
| `how do I fix a flat tire?` | A chat reply. Jev overrides the word "fix", so no coding agent starts. |

- **Jev never approves anything and never stops a task.** Starting a worker isn't permission: deleting,
  installing, paying or sending a message still waits for your tap. "Can you halt what Claude is doing" gets
  *say "stop" and I will*.
- **Jev is asked first, every time,** except for `stop` and stop/pause/resume speech. For an exact command the rules
  supply the details and Jev confirms what it is. If Jev is at least 85% sure it means something else ("open the pod
  bay doors" is a joke, not an app), Jev's reading wins.
- **Follow-ups work:** "open Chrome" then "and search for TypeSafe", or "open Notepad" then "close it", within 2
  minutes.
- **When Jev is slow, unsure or down, the rules answer.** Jev gets 2 seconds, and anything under 50%
  confidence counts as unsure.

- **Coding tasks need a project name** in the sentence ("…in Portfolio"). If it's missing, Monday asks which project.
- **Projects Monday knows:** Library › Projects, where **Add project** adds one; or say “add project <folder> as <name>”.
- **Before a project task, Monday checks Graphify:**
  - which files changed since the last analysis;
  - which files matter for this request.

  It passes those to the agent instead of re-reading the whole project.
- **After the agent changes files,** Monday updates the map incrementally.

---

## 4. Approvals

Nothing risky runs without your click: the request shows as a card in Monday's window, with **Approve** and **Deny**.

| Agent | What asks you |
|---|---|
| **Monday (terminal)** | Every command is classified before it runs: **safe** (`git status`, `python --version`) and **low risk** (`npm install`, `npm run dev`, tests) run; **high risk** (`git push`, `pip install`, global installs, registry, killing processes, overwriting a file) and **destructive** (`rm`, `del`, `git reset --hard`, `git push --force`) ask you; **blocked** commands never run (wiping a drive, reading stored passwords or `.env`, turning off Windows security). |
| **Monday (screen)** | Clicking something labelled send, post, pay, buy, delete, remove, install, accept or sign out asks you; so does Enter in a messaging app. Monday never types passwords, card numbers or keys, and doesn't operate Windows Security. |
| **Monday's computer agent** | Every step the model proposes goes through the same two rules above. A request that mentions deleting, installing, paying, sending or accounts asks before it starts. |
| **Hermes** | Commands Hermes considers dangerous, and every file edit. Monday also asks *before starting* any Hermes request that mentions deleting, installing, paying, sending messages, accounts, or system actions (shutdown, registry…). |
| **Claude Code** | Any command, network access or tool beyond editing files inside the project. |
| **Codex, Gemini CLI, Grok Build** | Once, before they start. They can't ask mid-task; each runs in its most careful unattended mode (Codex sandboxed to the project, Gemini edits only, Grok file tools only). |
| **Kilo Code** | What Kilo asks to do comes to you mid-task, like Hermes' (it runs over ACP since 2026-10-15). |

- **Risk (LOW / MEDIUM / HIGH)** comes from fixed rules, not AI. Deletes, installs and anything outside the project are HIGH.
- **Unanswered requests count as denied:**
  - Hermes requests expire after 4 min 45 s, just before Hermes' own 5-minute limit.
  - Monday's own requests expire after 15 min.
- **Esc never approves.** It stops Monday's tasks; an approval card stays until you click Approve or Deny.

---

## 5. Monday's window

**Since 2026-10-19** (Qt, every OS): the look of your Coucou recording: a black rail with the pages in sections (Monday, Tools, Notch)
and Monday's state at its foot; each page on a near-black panel, with a banner (its tint, stars and Monday's pals) and charcoal cards;
orange for the one action a page leads with, green switches. Every number is Core's; where Core says nothing, the page says N/A.
`docs/design/REFERENCE.md` maps each page to the recording. The old Windows-only window (WinForms) was retired the same day.

**Monday**
- **Home:** a greeting, the ear (on or off, and who reads your words), your history (search what you asked and what Monday did),
  today's numbers, and what your own agents did since yesterday.
- **Chat:** the conversation; your words on the right, Monday's replies formatted (headings, lists, bold, code), each with **Copy** and
  the model that answered (an error has **Try again**). Each task is a card with its live state, its steps as they happen (✓ done,
  ● running, ✗ failed), the result, for code whether it was tested after the last change (✓ passed, ✗ failed, ? unclear, ! not tested
  since; a pass that came from changing a test is said, in amber), how long it took, and **Stop** while it runs. **Approvals** wait above
  the box, each with what exactly it will do, its risk, **Approve** and **Deny**: nothing else answers them. **The box:** Enter sends,
  Shift+Enter starts a new line; **+** attaches a file (up to 100 MB; programs are never run), the mic (Ctrl+M), Send (or Stop while a
  task runs and the box is empty); under it **who works** (computer work and code) and the **chat model**. Earlier chats open read only.
  In debug mode the **reflex strip** shows Jev's read of your sentence while you talk (see *The Reflex strip* under Voice).
- **Tasks:** Monday's own tasks above your to-dos, in groups of your own: rename in place, move to a group, pin, archive or delete
  (delete asks first; a running task can't be archived or deleted). Type a to-do, **Check** (Jev says who would do it and whether it
  involves money), then **Add to my list** or **Monday, do it** (anything involving money asks once more); tick it when done.
- **Browser:** Monday's own Chrome, live: the address bar, **Go**, **Back**, **Take control** (your clicks on the picture reach the
  page), **Resume** (Monday carries on) and **Stop** (the task using the browser stops).
- **Activity:** everything Monday did, step by step, newest first, as Core recorded it.
- **Statistics:** what you asked, what Monday did, and where, per day for a week, a month or everything.
- **Settings:** name, chat model, how Monday answers (debug mode among them), spoken replies, who speaks, the voice (with **Test**) and
  speed, and your standing yeses for approvals (HIGH risk always asks).

**Tools**
- **Library:** what Monday remembers (**Correct…** or **Forget**; forgotten lines go to `Forgotten.md` in your vault), your
  projects (**Add project**, **Update its map**) and what it learned by itself.
- **Bots:** Monday with a role: **Add Scout, Pixel and Forge** or make your own (name, role, instructions, who does its work, its own
  chat model, computer access); ask one, or say “Scout, find …” in the box. **Rooms** put several together (who answers: the lead,
  everyone, or only those @mentioned); a team file can be saved and opened.
- **Routines:** a bot does something on a schedule, shown on the week's calendar; each run leaves a receipt.
- **Tests:** record a workflow once (**Start recording**, do it, **Stop and save**; passwords are never kept), then **Run it**: Monday
  replays it here and checks every step against what it really sees. Untick a step to leave it out.
- **Camera:** **Turn on camera** to see what Monday sees, boxed and named (off when you leave the page or hide the window); **What is
  this?** asks Monday. Below: what Monday knows by sight, with **Forget**.

**Notch**
- **General:** the notch (shown at start or not, how soon it closes, **Reset the notch's place**), its pet (kind, colours, what it wears, its
  motion), its sounds and their volume, your pet **on the desktop** and its size there (50% to 250%), and starting Monday at login.
- **Agents:** every AI tool Monday found, with **Install** (asks first), **Sign in**, **Open** (in your current project) and **Site**;
  who does computer work and code by default; custom engines (**Remove…** for one you added; the program stays on your PC).
- **Integrations:** Google (its state, setting up your own Google client, the services Monday may use, **Connect Google…** in your own
  browser, **Check**, **Disconnect**), API keys (they go into `Monday\.env`; the box clears at once and the key is never shown again),
  your webhooks (each off until you turn it on) and every component with its real state (N/A, Offline or Not configured when that is
  the truth).
- **Shortcuts:** the keys that work from any app. A shortcut whose keys type a letter on your keyboard starts off, so the letter still
  types; the page says which and takes other keys.

**The notch** hangs from the top centre of the screen: your pet, what Monday is doing right now (hearing you, a task's step, waiting
for your OK, done or failed) and one line of detail; with your own agents running it shows theirs. Rest the mouse on it to open it;
drag it anywhere (it stays there). Its views: **Home** (Claude Code's hooks; an agent's question with **Allow** / **Deny**; the open
editor windows, a click brings one forward, or opens the view of your agent working in it), **Chat** (the thread, the model, a prompt sent like anything typed in the window), **+**
(a file dropped on it or picked: the pet eats it, then Monday offers only what it can do with that kind of file: summarize, ask about
it, key facts, review or explain code, what's in a picture, send by email, open, continue in chat; programs and scripts are never
opened) and **Settings** (sounds, volume, how soon it closes). An approval still waiting, an alert, and a long task's end also come as
a notification. **Ctrl+Alt+C** circles part of the screen and asks about it; Monday can mark where something is on your screen.
- **Your pet on the desktop** (like Dotpals' pal): bottom right above the taskbar (drag it anywhere), its name over it. Poke it three
  times and it gets dizzy. ⤢ opens Monday, × puts it away. The pet or the notch is always there: turn one off while the other is off
  and that one comes back.
- **Approve Claude Code from the notch:** when Claude Code (in the Claude desktop app) is about to ask your permission, the notch shows
  what it wants to do and where, with **Allow** and **Deny** and a countdown ("1:45 left here, then answer in Claude Code"); its own
  prompt still works, and whichever you answer first counts. **Ctrl+Alt+Y** / **Ctrl+Alt+N** answer from any app where they don't type
  a letter (Shortcuts). This needs the `PermissionRequest` hook (`core/approve_hook.py`) in your Claude Code settings.
- **Your own agents in detail** (like Dotpals; the old notch had it, the Qt notch since 2026-10-19): on Home, **Details** (or a click on
  the editor one of them works in) opens that agent's view: what it is on, its last edit as a code card (removed lines on red, added
  on green, "+N −M" by the file), its plan / context / limits bars (a bar only where its log says how far: Claude Code's logs don't, so
  theirs are words), **Steps** (this request's runs and edits, "try 2", "fixed on try 2", filtered by Key steps / Changes / Commands /
  Problems / All, and its Files), **Story** with **Copy today**, **Next agent** when several run, its chat, **Go to** (its window) and
  **Write to**: your words wait for its next step and its own hooks hand them over; when it can't take them (it isn't working, it isn't
  Claude Code) they're copied and its window comes forward, to paste. **Rewind…** (once Settings → Restore points is on) shows what its
  last request changed, puts back the files you keep ticked, and then offers **Undo the rewind**.
- **Not on Qt:** the old window's small strip view (the notch is the compact view), and from the old agent view **Continue in ▾**
  (handing an agent's work to another agent), **Go to** for an agent running in a terminal, each agent's own pal, and the plan's line
  under the closed notch. Their files are kept in `E:\monday\backup\2026-10-19-retire-winforms`.

How it behaves:
- **Instant:** an exact command whose every step is harmless (open, close, switch, click, type, scroll, play, pause, a screenshot,
  a photo) runs at once, and Jev's reading is recorded afterwards instead of being waited for. Anything that runs code, asks you first,
  or isn't an exact command still goes to Jev first.
- **Monday answers out loud only when you ask it something out loud** ("what time is it", "what's playing", "explain…"). Orders
  ("open Spotify", "play Blinding Lights", "close Chrome") are done without a word: the computer changing is the reply, and a failure
  shows in red. Typed questions are answered on screen only. Say `assistant mode` if you want every reply spoken.
- **Pure agent mode (the default since 2026-10-06):** say what you want; the app opens, the song plays, the search runs, and Monday
  says nothing. `debug mode` is still silent; `assistant mode` or `talk to me` turns spoken replies on; `pure agent mode` goes back.
  - The mic hears the room too. A sentence that isn't a command is answered only when it sounds like it was said to Monday. Start with
    its name (`Monday, …`) to be sure. Commands always run.
  - While Monday talks, what the mic hears of its own voice is dropped; anything else you say stops it and is sent.
- **It learns from what works.** No model is trained; Monday keeps what it saw succeed and drops what stops working (`GET /api/learned`
  lists it): app names as the recognizer hears them, the clicks that got a goal done in an app, and your browser answer after three
  identical replies.
- **Not in the window yet:** the old voice panel's *Take my time* and filming switches (say "listen longer" or "we're filming"), and
  the JEV panel (today's reflex calls are in `GET /api/jev`).

### Voice

- **Your own voice is the voice (since 2026-10-13).** Monday speaks with one voice: yours, cloned on this computer from a recording of
  about 30 seconds. Until the recording is there, spoken replies say so on screen ("Not spoken: your voice isn't set up yet") and
  nothing else speaks in its place; a failure of the voice is reported the same way, never hidden behind another voice.
  - **Record** about 30 seconds of the voice you want, in a quiet room, close to the microphone, not shouting (a phone's voice recorder
    is fine; WAV, FLAC, MP3, M4A, OGG or WEBM). Optionally type what was said into `sample.txt` beside it; it is used only when the
    recording is 12 seconds or shorter (the engine takes the best 12 seconds of a longer one and transcribes them itself once, which
    downloads Whisper, ~1.6 GB, the first time).
  - **Hand it over:** drop it on the notch and choose **Use as my voice** (or attach it to a message and say "use this recording as
    your voice"). Monday copies it to `data/voice/`, inspects it (length, speech, clipping, noise) and tells you what it found; a recording
    that is too short, too quiet, clipped or noisy is refused with what to record instead. The original is never altered; the last
    recording is kept beside it.
  - **The engine** is F5-TTS (code MIT; model weights CC-BY-NC 4.0, for your own use) in its own Python at `E:\monday\voice\env`
    (`MONDAY_VOICE_PYTHON` points elsewhere), started by Monday when needed (`data/voice/engine.log`). It needs about 2.5 GB of free
    memory to load; on a full machine it says "the paging file is too small" (or "not enough memory") and the reply stays on screen. On
    this laptop (16 GB, the pagefile on a nearly full C:) it could not load while VS Code, Chrome and the Claude app were open
    (2026-10-13): give Windows a pagefile on E: (System › About › Advanced system settings › Performance › Virtual memory), or close
    the big apps while the voice starts. Set it up once:
    `E:\anaconda\Scripts\conda.exe create -p E:\monday\voice\env python=3.11 pip` then `E:\monday\voice\env\python.exe -m pip install f5-tts`
    (the model, ~1.4 GB, downloads to `E:\monday\voice\hf` on first use). `MONDAY_VOICE_NFE` (default 16) trades quality for speed.
  - **Test it:** the voice panel's *Test*, or "say something".
  - The built-in voices below are no longer offered in the panel. Say "use the browser voice" to hear one anyway, "use my voice" to come back.
- **Voice settings:** the window's **Settings** page (who speaks, the voice, speed, spoken replies), or say it: "speak faster", "use the
  second female voice". Your choices are saved in Monday's database.
- **Who can speak** (reachable by voice command only, since your own voice is the voice):

  | Provider | What it is | What it costs |
  |---|---|---|
  | **This browser's voices** (default) | In Chrome, Google's and Microsoft's voices. In Edge, also Microsoft's natural online voices | Free, no key |
  | **Gemini voices** (optional) | Google's natural-sounding voices, with gender, accent and style listed by Google | Add `GEMINI_API_KEY=…` (a Google AI Studio key) to `Monday\.env`. The Flash TTS models are on Gemini's free tier; the limits show in AI Studio |
  | **Google Cloud Text-to-Speech** (optional) | Chirp 3: HD voices | Add `GOOGLE_TTS_API_KEY=…` to `Monday\.env`. The first 1M characters a month are free, then $30 per million, and billing must be enabled on the Cloud project |

  Prices were checked on 2026-09-27 and can change. If a voice fails (quota, network, the engine), the reply stays on screen and
  the activity stream says why; no other voice speaks in its place.
  Say "use the Gemini voice", "switch to Google Cloud voices", "use browser voices" or "use my voice".
- **If Monday stops hearing you** (the desktop app): the line under the box says which Windows switch is off, with a button to it:
  the *microphone* privacy switch, or (only with Windows' dictation chosen) *Online speech recognition* (Settings › Privacy & security ›
  Speech). The microphone switches are in (Settings › Privacy & security ›
  Microphone: "Microphone access" for the device and "Let desktop apps access your microphone"; on 2026-10-13 the device-wide one was
  off and Windows refused every app). Monday never flips these itself; it starts listening again by itself once they are on.
  `GET /api/stt` shows both (`privacy`, `microphone`). Also check the microphone's input level (100 % for this laptop's array).
- **Dictating exact text:** "write the following in Notepad: …" or "open Notepad and type this exactly: …" types everything after the
  colon as it is: it is never cut at "and", never read as a command, never rewritten. Long or multi-line text goes in through the
  clipboard in one go (your clipboard text is put back), and Monday reads the document back and tells you whether it matches exactly,
  and where it differs if not. A failed long insert is looked at again, never typed twice.
- **Opening a project that isn't registered** ("open the Monday code in VS Code"): Monday looks in its registry and in VS Code's own
  recent folders, then in your Desktop and Documents; for anything wider (your home folder, a whole drive) it asks first and remembers
  your yes ("forget the search folders" takes it back). Several matches are listed with their paths: answer "the one on my Desktop" or
  "the second one". The chosen folder is registered, opened, and confirmed against VS Code's own record of its active window.
- **Only real voices:** voices are what each provider reports. Only voices stated to be female are numbered: stated by
  the provider, or for browser voices by Windows or the voice's name. A voice whose gender isn't stated is never
  guessed.
- **Listening:** Monday's own ear, Whisper (`base.en`) on this PC, in Monday's window (one session per sentence; nothing heard is
  sent anywhere or kept). Windows' dictation is still there if you choose it (settings `default_stt: windows`). Google Cloud
  Speech-to-Text isn't built.
- **Short replies:** Monday speaks at most a couple of sentences and says "the rest is on screen". A finished task is
  said in one line ("Done. Claude Code finished: fix the login redirect. 4 files changed."), and the details stay on
  screen. Chat replies start speaking after the first sentence, while the rest is still being written.
- **Interrupting:** say "stop", "cancel", "never mind", or just start talking, and Monday stops speaking and listens,
  keeping what you said. "Pause" and "resume" pause speech.
- **Natural pauses:** pausing mid-sentence doesn't send your command. Monday waits:

  | You stopped after… | Monday waits |
  |---|---|
  | An ordinary sentence | 1.5 s |
  | A command | 1.9 s |
  | "…and" | 3.2 s |

  Tap the mic to send sooner. If Monday answers too soon, say "listen longer" or pick "Take my time" in the voice
  panel.
- **The Reflex strip** (debug mode, just above the box): while you talk, Jev answers a few yes/no questions about your
  sentence: is it finished, is it said to Monday, would acting send, delete or spend. Each shows as a bar with a tick at
  the gate Monday's code applies, the verdict word, and, when a value crosses its gate, a receipt like "35% → 97%". The pill
  says which model answered, how many questions, how long it took and what this sentence has cost. Without a Jev key
  it says so and shows N/A (likewise Off when routing is "rules only", and Failed when Jev or Monday Core didn't answer). It
  only informs: Monday still answers when you pause, and the rows stay until the next sentence's first read. Your words so far
  are read at most every half second, and the finished sentence once more.
- **Filming mode** (`we're filming`): the mic stays open (close it yourself with the mic button or Esc) and each finished
  sentence the open mic hears is judged before Monday answers. A sentence to a camera or an audience, even one that says
  "Monday is going to…" in the third person, stays quiet and never reaches the chat; the line just above the box says why
  ("Filming mode: addressed 36%, gate 70%, staying quiet. Not sent: …"). A sentence judged as said to Monday goes on as any
  other, so the open mic's rule still holds (it starts with Monday's name, or is an exact command). If Jev can't be reached,
  only the name (or `filming mode off`) gets through; if Monday Core can't be reached, nothing is sent and the line says so.
  Push-to-talk (holding Ctrl+Space) is you talking to Monday on purpose, so it isn't judged. The mode is kept across restarts.
- **The judge:** before a chat answer is spoken, Jev checks that it doesn't say Monday already *did* something (sent,
  booked, posted, paid, deleted). Chat can't act, so there is never a receipt: such an answer is replaced by one honest
  line, and the original stays on the message. Speech waits for the verdict only
  when the answer contains an action word, so ordinary replies aren't delayed.
- **What it costs:** Jev charges for input tokens only (measured: $0.00002–0.00003 per call). The JEV panel's *Reflex* row
  shows today's calls, time and cost, and `python -m core.jev --drill` prints how the filming gate and the judge do on
  sample sentences.

## 6. Check that everything works

With Monday running:

```
python preflight.py          # everything, including a live chat reply, a live Hermes task and (with a key) a live Jev decision
python preflight.py --quick  # skip the live checks
python -m core.router        # the routing rules, fast path, Jev policy and permission rules, checked offline
python demos.py              # the Monday 2.0 demos (Chrome search, find a page, research, chat, voice, Gemini, stop), timed
python demos.py --gui 1 5    # also Notepad and VS Code on your real screen: run it while you watch
python demos.py --agent 6    # also a coding agent (approve it in Monday's window)
```

Or just tell Monday **"run the tests"**: for Monday Core that runs every module's self-check (router, executor,
workers, terminal, computer, voice, memory, agents, tools, providers, screen-control proxy).

- **PASS:** works.
- **WARN:** not built yet, or needs your login. Monday still runs.
- **FAIL:** broken; the command exits with an error.

---

## 7. Troubleshooting

| You see | Do this |
|---|---|
| *"That was a fault in my own code…"* | Monday broke, wrote the problem up and gave it to a coding agent (the next one when that one can't finish; Antigravity IDE's or Kiro's own agent when no command-line agent can run). It keeps the fix only if its own checks still pass, then restarts itself to load it (when started by `Start Monday.cmd`). Say **"stop fixing yourself"** to turn this off, **"fix yourself"** to have it look into the last failure, **"restart yourself"** to load a fix. Each repair's write-up, the code from before and the checks are in `data\repairs\`. |
| A white, unstyled page, or *"Monday Core isn't serving this page"* | Start Monday (`Start Monday.cmd`) and use the `http://127.0.0.1:8765/…` address. |
| **OFFLINE** / *Backend offline* | The *Monday Core* window was closed or crashed. Start it again. |
| *Claude Code isn't logged in* | Run `claude` in a terminal, then `/login`. |
| Codex: *usage limit* | Your plan's limit; it resets Oct 7, 2026. |
| *No language model is reachable* / Hermes **Offline** | Start Ollama. |
| *Too many attempts* | Wait 5 minutes. |
| Mic does nothing or shows an error | Read the line next to **Mic**: it says what the ear did. Check Settings › Privacy & security › Microphone (both switches) and the microphone's input level; with Windows' dictation chosen, also › Speech (*Online speech recognition*). |
| Ctrl+Space does nothing | The line next to **Mic** says "push-to-talk is off: another app holds Ctrl+Space" when another app took the key first (some input methods and launchers do); close that app or change its shortcut, then restart Monday. While Monday runs, Ctrl+Space belongs to it: in Word or VS Code it no longer clears formatting or opens suggestions. |
| Port 8765 already in use | `set MONDAY_PORT=8777` before starting, and use that port in the addresses. Opening the HTML files from disk only redirects to 8765. |
| **JEV: Not configured** | Add your key to `Monday\.env` (see *Turn on Jev*). Everything still works on the rules meanwhile. |
| **JEV: Offline** | Hover the pill for the reason. Either there's no internet, or the key was rejected (401): check the key in `.env`. Monday retries after 15 seconds, and the next answer from Jev turns the pill back to Online. |
| **JEV: Degraded** | Jev is slow, rate-limited, or some recent calls failed. The rules cover for it; it recovers by itself. |
| *"Grok needs an API key first"* (or Claude, ChatGPT…) | Add that provider's key to `Monday\.env` (see *Other AIs*). |

---

## 8. Settings

| Setting | Default | What it does |
|---|---|---|
| `MONDAY_PORT` | `8765` | Port for both screens |
| `MONDAY_ALLOWED_HOSTS` | *(empty)* | Extra hostnames Monday accepts, e.g. a future HTTPS address |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Where Ollama is |
| `TYPESAFE_API_KEY` | *(empty)* | Jev key, `TYPESAFE_API_KEY=<configured securely>` in `Monday\.env` (or the environment) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY` | *(empty)* | Keys for the other chat AIs, in `Monday\.env`. Gemini chat needs none (it uses your Gemini CLI). `GEMINI_API_KEY` is only for the optional Gemini voices |
| `GOOGLE_TTS_API_KEY` (else `GOOGLE_API_KEY`) | *(empty)* | Google Cloud Text-to-Speech voices (billing enabled on the Cloud project) |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts` | Which Gemini speech model speaks |
| `MONDAY_CUA_DRIVER` | `E:\cua\…\cua-driver.exe` | Screen control for Hermes; remove it to turn that off |
| `MONDAY_DB` | `data\monday.db` | Database path; only for test copies of Monday |

- **Assistant name:** Settings › Name.
- **Chat model:** say `use …` (see section 3).

---

## 9. Where your data lives

| Path | Contents |
|---|---|
| `data/monday.db` | Conversation, tasks, approvals, receipts, settings, Jev decisions |
| `.env` | Your Jev key. Stays on this PC; git ignores it |
| `data/uploads/` | Files attached to messages |
| `graphify-out/` | Graphify's map of this project |
| `Monday/` | Your Obsidian vault. Monday writes `Decisions.md`, `Preferences.md` and `Projects/<name>.md` (what coding agents changed). Clicks, chat and "opened Chrome" are never written. |
| `data/uploads/screenshot-*.png` | Screenshots you asked for |

**Start with a clean history:** close Monday, rename `data/monday.db` (for example to `monday.old.db`), and start again.
The current database holds the test run from setup day (TASK-2026-0001 to 0008).

---

## 10. Not built yet

These are shown as *not configured* on both screens; they are never faked:
- **The FreeLLMAPI cloud pool.** Jev's "how hard is this" score picks between Ollama models instead.
- **Google without your OAuth client** (see One-time setup); moving or cancelling calendar events; organizing Drive files.
- **A wake word engine.** "Okay Monday" is matched on the words the ear writes down (Whisper on this PC, or the reader you chose on
  Home), so the ear still transcribes what it hears; only what Monday acts on needs the phrase. From `go to sleep`, Monday wakes by two
  claps, a snap or a whistle, not by its name.
- **Add connector (MCP)** in Integrations.
- **Owner recognition / camera security.**
- **A vision model for the screen.** Screen understanding uses UI Automation and OCR. The camera's object models (OpenCV, on this
  PC) look at things you hold up, not at the screen; no image is sent to an AI.
- **Cursor, Kiro, Antigravity and Copilot as coding agents.** They're editors/apps with no command-line agent here, so
  Monday opens projects in them but can't hand them tasks. Grok Build's CLI is installed but has no Monday adapter yet.
- **Analytics** beyond Jev's routing stats, today's tasks and the speed table, and the context/token monitor.
- **Tested lightly so far:** the model-driven screen agent (it needs Ollama, which was off during the build) and
  sending messages (never tested end to end, because that would message a real person).

Details: [ARCHITECTURE.md](ARCHITECTURE.md). Files: [PROJECT_MAP.md](PROJECT_MAP.md). Rules for AI agents working on Monday: [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md).

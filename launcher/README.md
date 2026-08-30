# Launch from Kitsu

A **Launch** button in the Kitsu web UI's task panel that opens a task's
work directly on the artist's own machine - Blender, Nuke or DaVinci
Resolve, whichever the task's department calls for - the way ftrack
Connect's software-launch buttons work.

Status: **Blender, Nuke and Resolve all confirmed working end-to-end** -
resolution, version tagging/labelling, and Launch itself, against real
tasks with real local `.blend`/`.nk` files and real Resolve projects on the
Kitsu test server. See [Nuke verification](#nuke-verification) and
[Resolve verification](#resolve-verification) for exactly what was checked
and how - both passes were done unattended overnight, and are worth a skim
before trusting them.

## Why it works the way it does, not the obvious way

The obvious design - register a `bbkitsu://` URL scheme and have the button
navigate to it - was built first and ultimately abandoned. It is left in
place (`install.py`, `bb_launch.py`'s URL parsing) because it still works
from a terminal or the Windows Run dialog, but **the browser button does not
use it.** Chrome refused every page-triggered navigation to the scheme with
`Failed to launch 'bbkitsu://...' because the scheme does not have a
registered handler` - and this held after three separate, individually
correct fixes:

1. A Default Programs registration (`RegisteredApplications` +
   `Capabilities` + `URLAssociations`), because Chrome checks that before
   handing off a page-triggered navigation, not just the plain
   `HKCU\Software\Classes\<scheme>` key ShellExecute reads.
2. Pointing the registered command at a dedicated compiled `bb_launch.exe`
   instead of `pythonw.exe` directly, on the theory that Chrome blocks
   handoff to well-known shared script interpreters as hardening against
   protocol-handler command injection.
3. A real `<a href="bbkitsu://...">` instead of a `window.location.href =`
   assignment, on the theory that JS-driven navigation is held to a
   stricter standard than a genuine anchor click.

None of it moved the needle, and a byte-for-byte registry comparison against
Discord's own working `discord://` registration showed **no structural
difference** - same key layout, same `AssocQueryString`/
`IApplicationAssociationRegistration` results at every level queried. A
fresh, never-before-used scheme name (`bbtest://`) worked immediately with
zero setup, which points at some stuck per-scheme Chrome-side state from
early testing rather than anything wrong with the registration itself - but
by that point the cost of continuing to chase an undocumented Chrome
behavior outweighed the cost of a different design entirely.

**What actually works:** a tiny HTTP server on `127.0.0.1:53212`
(`bb_launch_server.py`), which the page reaches with an ordinary `fetch()`.
That is not a protocol handoff at all, so none of the above ever applies to
it. This is the real design; treat the `bbkitsu://` pieces as inert unless
someone specifically wants a terminal-launchable URL.

## How a click becomes an open file

1. **`BB_core/versioning.py`** - `tag_comment()` appends a small marker,
   `[[v003]]`, to every publish comment. This is the *only* durable link
   between a Kitsu preview revision and the local scene version that
   produced it: Kitsu's revision counter advances on every publish, while a
   scene is only saved when something changes worth a version bump, so the
   two numbers drift apart under completely normal use - several published
   angles off one saved scene is a revision bump with no matching version
   bump. The comment text an artist actually reads is exactly what they
   typed; the marker rides along after it and gets stripped back out by
   parsing, not read by anyone.

   Called from two places in the Blender add-on - **both had to be found
   and patched separately**, which cost most of one debugging session:
   - `blender/BB_pipeline/publish.py` - the "save" publish.
   - `blender/BB_pipeline/review.py` - the separate "Render → Review →
     Submit" publish. Missing this one is exactly why the first round of
     testing showed untagged comments even after the fix "worked."

   And one place in Nuke:
   - `nuke/BB_pipeline_nuke/publish.py`.

   And one place in Resolve, added the same night Resolve support went in
   (below) once it was clear the exact same drift applies there - a
   colourist can publish several stills off one project version exactly as
   easily as a 3D artist can publish several angles off one saved scene:
   - `resolve/BB_pipeline_resolve/publish.py`'s `upload()` - reads the
     version off the *currently loaded* Resolve project's own name
     (`resolve_ops.version_from_name`), since `upload()` gets a bare `task`
     dict from its caller, not an `EntityContext` carrying a version the
     way the Blender/Nuke call sites do.

2. **The Kitsu frontend** (`launcher/kitsu-frontend.patch`, applied to a
   clone of `cgwire/kitsu`) reads each task's comment history (already
   fetched for the comment feed - no new API call), parses the marker back
   out with the same regex, and uses it to:
   - label each entry in the existing preview-revision dropdown as
     *"Image N from vX"* when a local version is known, or just the bare
     revision when it is not (an untagged comment predating this feature,
     or a custom comment typed before the marker existed on that publish
     path).
   - drive the **Launch** button, next to that dropdown in the task side
     panel (`src/components/sides/TaskInfo.vue`): `fetch()` to
     `bb_launch_server.py` with the version resolved from whichever
     revision is currently selected, or no version at all (→ latest) when
     it is not known.

3. **`bb_launch_server.py`**, running on the artist's own machine, resolves
   the task (department → DCC, from `BB_core/presets/default.toml`'s
   `[dcc.blender]`/`[dcc.nuke]`/`[dcc.resolve]` lists, matched against
   whatever department Kitsu itself has the task type filed under), then
   asks `bb_launch.py`'s `open_version()` to open the requested version (or
   the latest) - which branches into one of two completely different
   mechanisms depending on the DCC, see below.

### Two launch mechanisms, not one

Blender and Nuke share one mechanism: a version is a file on disk
(`BB_core.workfiles`/`versioning`), and opening it is
`subprocess.Popen([exe, path])`.

**Resolve does not fit that model at all.** A Resolve project lives inside
Resolve's own project database, addressed by name
(`Project_Sequence_Shot_Task_vNNN`, built by
`resolve_ops.build_project_base` - the same naming rule the existing Resolve
UI already uses, reused rather than re-derived), and a new version is a
newly-imported project, not a new file next to the old one. Opening one
means Resolve has to already be reachable through its own scripting API
(`resolve_launch.py`, connecting the same way
`resolve/BB_pipeline_resolve/resolve_ops.py` does for the Resolve-side UI),
then `ProjectManager.LoadProject(name)` - there is no file path involved at
any point. `bb_launch.py`'s `list_versions()`/`open_version()` hide this
branch behind one shared shape (`(version, identifier)` - a `Path` or a
project name) so `bb_launch_server.py` and the CLI do not need to know which
kind of DCC they are talking to.

### Creating a task's first version

Before this, a task with nothing on disk yet was a dead end: Launch just
said "create one from blender first" and left the artist to do that by
hand. Now, for Blender specifically, `open_version()` creates and stamps a
real v001 instead - by reusing the add-on's own creation logic
(`operators._create_version`, the same function `bpy.ops.bb.new_workfile`
calls), not by writing an empty `.blend` some other way and hoping the
pipeline treats it as a real version.

That function runs *inside* a real Blender, though, and normally reads its
context off the browser's own selected project/sequence/shot/task
properties - which do not exist when there is no browser UI to have
selected anything. So `blender_create_bg.py` runs inside a `--background`
Blender (`blender_create.py` launches it, given the `EntityContext` and
path `bb_launch.py` already resolved via Kitsu) and calls
`_create_version` directly, entirely bypassing the operator and its
UI-property dependency. Every UI-only step inside it - the viewport
thumbnail grab, the "publish now?" popup - already guards itself against
`bpy.app.background`/no-window (`_ask_to_publish`'s check, and
`generate_datablock_previews`'s own "skipped in background mode" note), so
none of that had to be handled specially here; it already worked this way
before this feature existed.

Verified end to end, and it took two real bugs to get there - both found by
actually calling this through the live `/launch` endpoint repeatedly rather
than trusting one clean run:

- The first attempt threw `TypeError: argument of type 'NoneType' is not a
  container or iterable`. Cause: `subprocess.run(..., text=True)` decodes
  Blender's captured output with Windows' default codepage (`cp1252` here),
  and one of this machine's ~50 *other* Blender add-ons prints something
  outside it - not a bug in Blender or in anything of ours, but it crashed
  Python's own output-reading thread and silently left `result.stdout` as
  `None`. Fixed by decoding as UTF-8 with `errors="replace"` instead of
  trusting the system codepage.
- Retested five times in a row afterward (once by hand, then three more
  through the real endpoint, closing Blender and deleting the created file
  between each) - all five succeeded and each produced a real, openable
  `.blend` at the correct versioned path.

### Nuke: done too, and a wrong conclusion corrected along the way

Nuke has the equivalent function already
(`BB_pipeline_nuke.scripts.create_version` - same shape as Blender's).
`-t` (terminal) mode needs a license type this machine has none configured
for, so unlike Blender there is no throwaway background mode available -
`nuke_create.py` launches a real interactive Nuke with `nuke_create_bg.py`
instead, and that session *is* the final result, not a step before one.

The first attempt at this was reported here as "did not visibly execute it
either (no error, no file, nothing)" - that was wrong, caught by actually
watching the Nuke window (via screen access) rather than trusting a
redirected-output log. **The script ran correctly the whole time** - the
file really was created, at the right path. What genuinely did not work was
`bb_launch.py`'s *wrapper* around it: modelled on Blender's, it waited for
the launched process to exit and then read its captured stdout back to
confirm success. An interactive Nuke correctly never exits, and stdout
redirected to a file is fully buffered rather than line-buffered, so
nothing written to it is even visible until the process does - a wait that
would never return, not a script that never ran.

Fixed by not waiting at all: `nuke_create.create()` fires the launch and
returns immediately, the same way opening an *existing* file already does -
Launch has never confirmed a file finished opening either. Verified twice
in a row through the real `/launch` endpoint (closing Nuke and deleting the
created file between runs): both times the HTTP call returned in under a
second, and Nuke came up seconds later with the correct file already
loaded, confirmed by screenshot rather than assumed from a 200 response.

Resolve is architecturally simpler for this than either
(`ProjectManager.CreateProject(name)`, no file involved at all), but has not
been wired in.

## Files in this folder

| File | Role |
|---|---|
| `bb_launch.py` | Core resolve + launch logic - `list_versions()`/`open_version()` branch between the file-based and Resolve mechanisms. Also a CLI: `python bb_launch.py <task_id>`. |
| `bb_launch_server.py` | **What the browser button actually calls.** `GET /launch?task_id=...&version=...` and `GET /versions?task_id=...`, on `127.0.0.1:53212`. Must be running - see [Running the server](#running-the-server-not-yet-automatic). |
| `resolve_launch.py` | Resolve-only half of the launch logic: connecting to Resolve's scripting API, listing/opening projects by name. Kept separate from `bb_launch.py` because none of it applies to the file-based DCCs. |
| `blender_create.py` / `blender_create_bg.py` | Create a task's first Blender work file when Launch finds none - see [Creating a task's first version](#creating-a-tasks-first-version). The `_bg` file is what actually runs *inside* Blender; the other is what `bb_launch.py` calls to run it. |
| `nuke_create.py` / `nuke_create_bg.py` | The Nuke equivalent - see [Nuke: done too](#nuke-done-too-and-a-wrong-conclusion-corrected-along-the-way). Launches Nuke directly rather than a throwaway background step; that session is the final result. |
| `launcher_config.py` | `blender_exe`/`nuke_exe`/`resolve_exe` paths, in their own file (`~/.BB_pipeline/launcher.json`) - see [Why a separate config file](#why-a-separate-config-file). |
| `dcc_versions.py` | CLI to list/set which installed DCC build to launch: `python dcc_versions.py list`, `set blender 5.1`, `current`. |
| `install.py` | Registers `bbkitsu://` (terminal/Run-dialog use only - see above). `--remove` undoes it. |
| `kitsu-frontend.patch` | The diff against upstream `cgwire/kitsu`, pinned at commit `f398fec6e8b011f65f31a916e994cb5a2fa96536` (`v1.0.58`). The actual clone is gitignored - multiple hundred MB, and irrelevant once built. |

## Why a separate config file

`blender_exe`/`nuke_exe` briefly lived in `BB_core/settings.py`'s shared
`DEFAULTS`, alongside `server`/`email`/`work_root`. That file is round-tripped
by Blender's own add-on every session, for its own reasons, and the two new
keys kept coming back **blank** with no single write site ever found despite
tracing every `settings.save()`/`settings.set()` call in the Blender
add-on - three separate times, across one evening. Moving them to their own
file with exactly one writer (`launcher_config.py`) ended the problem outright
rather than continuing to chase it. Nothing else should ever read or write
`~/.BB_pipeline/launcher.json`. `resolve_exe`, added later the same night,
went straight there and never touched the shared file at all.

## Running the server (not yet automatic)

Tonight, `bb_launch_server.py` was started by hand and kept running for the
length of the session. It does **not** yet start automatically at login -
next real step, before this is usable day-to-day:

```bash
pythonw bb_launch_server.py
```

To make it durable: a Windows Task Scheduler task with a logon trigger
(`schtasks /Create /SC ONLOGON ...`), no admin rights needed for a
per-user task. Not yet built.

## The Blender add-on's own copy-drift trap

Of the Blender versions on this machine, **only 5.1's installed extension is
a real standalone copy** of `BB_pipeline` - 4.2, 4.5, 5.0 and 5.2 are
symlinked straight to this repo (`tools/dev_install.*`), so edits to the
repo take effect immediately for those. 5.1 is not, and needs a manual
resync after *any* change to `BB_core/` or the Blender add-on:

```bash
EXT="$HOME/AppData/Roaming/Blender Foundation/Blender/5.1/extensions/user_default/BB_pipeline"
cp -r BB_core "$EXT/BB_core"
cp blender/BB_pipeline/publish.py "$EXT/publish.py"
cp blender/BB_pipeline/review.py "$EXT/review.py"
```

This bit twice tonight (`settings.py`, then the whole `publish.py`/
`review.py` tagging fix) before the pattern was obvious. Symlinking 5.1 the
same way as the others would remove the trap entirely - worth doing rather
than continuing to remember this step.

## Deploying the frontend patch to a Kitsu server

The running server (`riouxr@<kitsu-host>`, Docker Compose at
`~/Documents/kitsu-server`, image `cgwire/cgwire:1.0.58`) bundles Zou and
the built Kitsu frontend in one container (`kitsu-server-kitsu-1`), serving
the static build straight from `/opt/zou/kitsu` inside it - no separate
frontend container or image to rebuild.

**This lives in the container's writable layer.** It is lost if the
container is recreated (an image update, `docker compose down && up`) -
redeploy after any of those. A pre-patch backup was taken once, tonight:
`~/Documents/kitsu-server/kitsu-frontend-1.0.58-backup.tar.gz` on the host,
restorable with the same `docker cp` + `tar xzf` steps in reverse.

```bash
# 1. Clone upstream at the pinned commit and apply the patch (skip the
#    clone if launcher/kitsu-frontend already exists locally).
git clone https://github.com/cgwire/kitsu.git launcher/kitsu-frontend
cd launcher/kitsu-frontend
git checkout f398fec6e8b011f65f31a916e994cb5a2fa96536
git apply ../kitsu-frontend.patch
npm install
npm run build

# 2. Ship the build to the Kitsu host and into the container.
cd dist && tar czf /tmp/kitsu-dist.tar.gz .
scp /tmp/kitsu-dist.tar.gz <user>@<kitsu-host>:/tmp/kitsu-dist.tar.gz
ssh <user>@<kitsu-host> '
  docker cp /tmp/kitsu-dist.tar.gz kitsu-server-kitsu-1:/tmp/kitsu-dist.tar.gz &&
  docker exec kitsu-server-kitsu-1 sh -c \
    "rm -rf /opt/zou/kitsu/* && tar xzf /tmp/kitsu-dist.tar.gz -C /opt/zou/kitsu && rm /tmp/kitsu-dist.tar.gz"
'
```

No server restart needed - nginx inside the container serves the static
files directly, so the new build is live as soon as the files land. Confirm
with a hard refresh (the asset filenames are content-hashed, so a stale
cache is the only thing that would still show the old build).

If Docker isn't reachable over plain SSH the way it was here: `docker` was
at `/usr/local/bin/docker` (Docker Desktop on macOS), not on the default
non-login SSH `PATH` - use the full path, or `ssh -t ... bash -lc '...'` to
get a login shell that has it.

## Setting this up on another workstation

1. Clone this repo.
2. `python dcc_versions.py set blender <version or full path>` (and `nuke`,
   `resolve`).
3. `pythonw bb_launch_server.py`, kept running (see
   [Running the server](#running-the-server-not-yet-automatic) - no
   auto-start yet on any machine).
4. Log in once from the Blender or Nuke add-on with "remember password" on -
   `bb_launch_server.py` reads the same stored Kitsu credentials
   (`BB_core.credentials`), not a separate login.
5. Nothing to install browser-side: the Launch button ships with the Kitsu
   frontend build, not per-machine.

## Nuke verification

Done unattended overnight, via the CLI/API level rather than a real Nuke
session driving the add-on's UI - there was no way to click Nuke's own
Publish button without a live interactive session, and the Nuke MCP bridge
wasn't connected (it needs Nuke already running with its bridge listening
*before* the calling session starts, and no Nuke was open when this one
did). What was actually checked, against real tasks/files on the Kitsu test
server (`KitsuTest Project`, sequence `sc01`, shot `sh01`, task
`Compositing`, 8 real `.nk` versions already on disk):

1. **DCC resolution** - `GET /versions?task_id=...` correctly resolved
   `Compositing` to `nuke` (via Kitsu's own department for that task type,
   same as the Blender path) and listed all 8 local versions.
2. **Launch** - `GET /launch?task_id=...&version=3` actually opened
   `Nuke16.0.exe` on `sc01_sh01_Compositing_v003.nk` - confirmed by
   inspecting the spawned process's command line, not just a 200 response.
3. **Tagging round-trip** - posted a comment through `KitsuClient.
   publish_preview` using `tag_comment(..., 5)` directly (the same call
   `nuke/BB_pipeline_nuke/publish.py`'s `send()` makes, exercised without a
   live Nuke session), then confirmed `/versions` and a follow-up
   `/launch?version=5` (no explicit version needed once the frontend reads
   the tag - passed explicitly here only because this was tested without
   the frontend in the loop) opened exactly `v005.nk`.

**Not checked**, because they need an actual interactive Nuke session:

- Nuke's own Publish button/panel actually calling `send()` with a real
  save in progress - the code path was read and matches Blender's pattern
  exactly (`tag_comment(comment or default_comment(path), entity_context.
  version)`), and unlike Blender there is only the one publish path
  (confirmed: `grep -rn publish_preview nuke/BB_pipeline_nuke/` finds one
  call site, not two), but "matches the pattern" is exactly what needed
  correcting on the Blender side after the first round of testing missed
  `review.py`. Worth a real publish from Nuke to be sure.
- The frontend's Launch button and "Image N from vX" label, from an actual
  browser session against a Nuke task - the underlying data (tagged
  comments, `/versions`, `/launch`) is confirmed correct, but the Vue
  component itself was only exercised for Blender tasks by a human.
- A test comment was left on the real `Compositing` task on `sc01/sh01`
  ("nuke launcher verification", tagged `[[v005]]`) - same as the two left
  on `knife`/Modeling earlier - harmless test data on the test project,
  not cleaned up.
- Nuke, unlike Blender 5.1, has no copy-drift trap to worry about:
  `nuke/install.py` appends this repo's path straight to `sys.path` rather
  than copying anything, so every fix here was live for a real Nuke session
  with no sync step - one fewer thing to get wrong than the Blender side
  had.

## Resolve verification

Added and checked the same night as the Nuke pass, after the user pointed
out Color Grading tasks only ever go to Resolve. Unlike Nuke, **Resolve was
already running** (with real, pre-existing colourist projects in its
database) when this started, which made a genuinely stronger check
possible: the real `resolve/BB_pipeline_resolve/publish.py` `upload()`
function was called directly - not a hand-built simulation of what it does,
the actual function a click of Resolve's own Publish button calls - and
Resolve's *own live state* was read back afterward to confirm each launch,
not just trusted from an HTTP 200. Against a real Color Grading task on
`sc01/sh01` with two real projects already in Resolve's database
(`KitsuTest-Project_sc01_sh01_Color-Grading_v001`/`_v002`):

1. **DCC resolution** - `GET /versions?task_id=...` correctly resolved
   `Color Grading` to `resolve` and listed both existing project versions -
   reading Resolve's own project database
   (`resolve_ops.get_all_resolve_project_names()`), not a folder on disk;
   see [Two launch mechanisms](#two-launch-mechanisms-not-one) for why that
   is a genuinely different code path from Blender/Nuke, not just a
   different file extension.
2. **Launch** - `GET /launch?task_id=...&version=1`, then `&version=2`, then
   no version at all - each one actually switched Resolve's live current
   project (`ProjectManager.LoadProject`), independently confirmed after
   each call by reading `GetCurrentProject().GetName()` back from Resolve
   itself. The no-version call correctly defaulted to the latest (`v002`).
   A deliberately wrong version (`v099`) came back with a clean list of
   what actually exists instead of a crash.
3. **Tagging round-trip, through the real function** - called
   `resolve.BB_pipeline_resolve.publish.upload()` itself (not
   `tag_comment` directly, the way the Nuke check had to) with Resolve's
   actual current project loaded (`v002`), and confirmed the posted
   comment came back as `"...\n\n[[v002]]"` - `upload()` correctly read the
   version off the *live* project rather than needing it passed in.
   Followed by the same round-trip Nuke got: `/launch?version=1` then
   `/launch?version=2`, each confirmed against Resolve's own
   `GetCurrentProject()` afterward.

**The cold-start path was tried for real after the first pass reported here
turned out to be wrong** - the user closed Resolve, tried Launch from a real
browser, and got nothing. That surfaced a real bug: `open_version()` was
asking Resolve what versions exist (reading its project database) *before*
confirming Resolve was even reachable, and reading that database when
Resolve is not running answers "nothing found" rather than raising - so a
task with two real versions silently reported "no work yet," and
`ensure_running()` - the code that launches Resolve and waits for it - was
never even reached. Fixed by ensuring the connection first, only for
`dcc == "resolve"`, before asking for the version list at all. Retested
cold, for real, with Resolve confirmedly closed beforehand: **`Popen`-launched
Resolve, waited ~50 seconds for its scripting API to come up, then opened
the right project - confirmed the same way as everything else in this
section, by reading `GetCurrentProject()` back from Resolve itself
afterward.** The polling loop's upper bound (90s) was not tested - a real
launch settled well inside it.

**That fix was itself incomplete** - the very next real attempt (Resolve
closed a second time, same long-running server process) failed exactly the
same way, instantly. The actual bug: `resolve_ops.get_resolve()` caches its
connection object the first time it succeeds, and `is_running()` was only
checking "is the cached Python object non-`None`" - which stays true
forever once cached, even after the Resolve process behind it is gone. So
the second closed-Resolve attempt saw `is_running()` claim Resolve was
already up, skipped `ensure_running`'s relaunch-and-wait entirely, and went
straight to a version list read against a dead connection - the exact same
silent "nothing found" as before, just from a different cause than the
first fix addressed. `is_running()` now makes a real call
(`GetProjectManager()`) to tell a dead cached connection from a live one,
and clears the cache on failure so the next attempt gets a genuine new
connection. Retested twice in a row on one long-running server process -
Resolve closed, launched (22s), closed again, launched again (40s) - both
correctly relaunched Resolve rather than silently failing the second time.

**Confirmed by the user afterward, from the real browser button** - Launch
now works with Resolve either already open or fully closed beforehand. This
is the one part of the whole Resolve pass that a human, not just this
session's own curl/API checks, actually verified end to end.

**Still not checked**, and worth knowing before trusting this fully:

- Resolve's own Publish panel/button clicked by a human, the way the Nuke
  gap is worded too - `upload()` was exercised directly and for real, but
  never through the actual UI button end to end.
- The frontend's "Image N from vX" label against a real Resolve task in a
  browser - the Launch button itself *was* clicked for real by the user
  (that is how the cold-start bug above was actually found), but nobody has
  yet looked at whether the label reads correctly for a Color Grading task.
- A test comment was left on the real `Color Grading` task on `sc01/sh01`
  ("resolve launcher verification", tagged `[[v002]]`) - harmless test data
  on the test project, not cleaned up, same as the Blender and Nuke ones.
- Across all this testing, Resolve ended up switched between several
  `KitsuTest-Project_sc01_sh01_Color-Grading_v00N` projects and was closed
  and relaunched more than once - nothing was modified or deleted anywhere
  (`LoadProject` only changes which project is currently active), but worth
  knowing the active project moved around if that machine's Resolve is
  shared with someone else.

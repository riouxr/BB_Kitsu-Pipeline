# Launch from Kitsu

A **Launch** button in the Kitsu web UI's task panel that opens a task's
work file directly in Blender (Nuke next) on the artist's own machine, the
way ftrack Connect's software-launch buttons work.

Status: **Blender done and confirmed working end-to-end.** Nuke has the
publish-side tagging in place but the launcher itself has not been tested
against it yet - that is tomorrow's task; see [Nuke TODO](#nuke-todo) below.

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

   Resolve was **not** touched - its `upload()` publishes a render against
   a task with a caller-supplied comment, not a work-file save, and does not
   share the same version concept. Revisit if Resolve ever needs this.

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
   `[dcc.blender]`/`[dcc.nuke]` lists, matched against whatever department
   Kitsu itself has the task type filed under), finds the requested local
   scene version (or the latest) via `BB_core.versioning`, and
   `subprocess.Popen`s the configured DCC executable on it. The actual
   resolve/launch logic lives in `bb_launch.py` and is imported directly,
   not duplicated.

## Files in this folder

| File | Role |
|---|---|
| `bb_launch.py` | Core resolve + launch logic. Also a CLI: `python bb_launch.py <task_id>`. |
| `bb_launch_server.py` | **What the browser button actually calls.** `GET /launch?task_id=...&version=...` and `GET /versions?task_id=...`, on `127.0.0.1:53212`. Must be running - see [Running the server](#running-the-server-not-yet-automatic). |
| `launcher_config.py` | `blender_exe`/`nuke_exe` paths, in their own file (`~/.BB_pipeline/launcher.json`) - see [Why a separate config file](#why-a-separate-config-file). |
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
`~/.BB_pipeline/launcher.json`.

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
   once that side is tested).
3. `pythonw bb_launch_server.py`, kept running (see
   [Running the server](#running-the-server-not-yet-automatic) - no
   auto-start yet on any machine).
4. Log in once from the Blender or Nuke add-on with "remember password" on -
   `bb_launch_server.py` reads the same stored Kitsu credentials
   (`BB_core.credentials`), not a separate login.
5. Nothing to install browser-side: the Launch button ships with the Kitsu
   frontend build, not per-machine.

## Nuke TODO

- Publish-side tagging is in (`nuke/BB_pipeline_nuke/publish.py`) and Nuke
  has no separate render-review publish path the way Blender does, so
  that side should already be complete - **not yet verified against a real
  Nuke publish**, the way the Blender path was.
- `bb_launch_server.py`'s DCC resolution already covers Nuke
  (`config.dcc('nuke')` in `BB_core/presets/default.toml`) and
  `launcher_config`/`dcc_versions.py` already have a `nuke_exe` slot, set
  tonight (`C:\Program Files\Nuke16.0v6\Nuke16.0.exe`) - untested end to
  end.
- Confirm Nuke's `.nk` naming/versioning resolves through
  `BB_core.workfiles`/`versioning` the same way Blender's `.blend` did -
  should be automatic (same shared core, same config-driven templates), but
  "should be" is exactly what needed correcting three times tonight on the
  Blender side.

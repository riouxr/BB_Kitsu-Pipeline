# BB Kitsu Pipeline

A Kitsu-backed production pipeline for Blender and Nuke, with DaVinci Resolve
and Houdini to follow. Browse shots from Kitsu, open and version scene
files, render with derived names and publish back — the same way in every
application, because every application calls the same core.

Status: **early**. The shared core and the Blender shot browser work. Renders
and publishing are next.

## Layout

```
BB_core/            shared, DCC-agnostic pipeline logic
  config.py           TOML config loading and merging
  naming.py           the canonical naming scheme, and parsing it back
  context.py          EntityContext — the object everything is derived from
  versioning.py       version discovery and increment
  workfiles.py        path templates for work files and render streams
  transport.py        HTTP over requests or urllib, whichever exists
  kitsu.py            Kitsu REST client
  credentials.py      password storage with no dependencies
  presets/default.toml   naming, paths and output streams, as data

blender/BB_pipeline/       the Blender extension
nuke/BB_pipeline_nuke/     the Nuke integration
nuke/install.py              adds the Kitsu menu to ~/.nuke/menu.py
tools/                       build and development install scripts
tests/                       core tests, plus a per-DCC integration test
```

`BB_core` must never import `bpy`, `nuke`, `PySide` or anything else that
only exists inside one host. **It has no third-party dependencies at all** —
HTTP goes through `transport.py`, which uses `requests` when the host has it
(Blender does) and the standard library when it does not (Nuke 16 does not).
Verified running under Nuke's own Python 3.11 against a live Kitsu.

## Design rules

**One version number, read once.** A version is discovered by the core from
what is on disk, carried on a `ShotContext`, and used to build every name and
path that follows. Nothing re-parses it out of a path string, and nothing
accepts it as a second, separately-typed input. This is what stops a render
being filed under a different version than the scene that produced it.

**Names are data.** The scheme is
`{project}_{sequence}_{shot}_{task}_v{NNN}` — for example
`VIL_FF9_0070_precomp3d_v003` — but it lives in
[`presets/default.toml`](BB_core/presets/default.toml), not in code. Field
values are sanitized to a character set that excludes `_`, so a finished name
always parses back into exactly the fields that built it.

**Two trees, one code path.** A Kitsu project holds Assets and Shots, and the
context names them generically — `group`/`entity` is sequence/shot for a shot
and asset type/asset for an asset. One naming template, one path builder and
one publish call serve both, rather than a shot path and an asset path
drifting apart. Assets sit under their own `assets/` prefix so a sequence
named `Prop` cannot collide with the asset type `Prop`.

**One render context, many streams.** Main, proxy, offline review, matte and
plate are all derived from a single context, differing only by a stream
folder, format and colorspace. The layout is
`<render_root>/<seq>/<shot>/<task>/internalRender/<stream>/<name_vNNN>/<name_vNNN>.####.ext`.

**Context lives in the file.** Saving stamps the full context — names *and*
Kitsu ids — into the scene, so reopening restores it with no server round
trip. Filename parsing is the fallback for files that predate the add-on, and
it is flagged as id-less so publishing knows it still has work to do.

## Blender add-on

Requires Blender 4.2 or newer. No wheels and nothing to pip install: Kitsu
access rides on the `requests` Blender already bundles, and passwords go
straight to the Windows Credential Manager through `ctypes`.

### Install

```bash
python tools/build_extension.py
```

Then in Blender: **Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk**, pick
`dist/BB_pipeline-0.6.0.zip`.

### Develop

The add-on reaches the shared core through `from .BB_core import ...`, never
`sys.path`. Blender's extension policy forbids both writing to `sys.path` and
importing a bundled package as a top-level module, and lists each one under
**Warning** in the preferences — which reads like something is broken.

An installed extension has BB_core copied in beside the add-on, so the
relative import just works. A development checkout has it one level up
instead, and `core.bootstrap()` loads that directory *as* `BB_pipeline.BB_core`
— the spec carries a `submodule_search_locations`, so the core you edit is the
core that runs, with nothing added to `sys.path`.

The checkout cannot simply hold a junction to it the way Blender's extensions
folder does: this repository lives on an exFAT drive, which has no reparse
points at all.

Nuke has no such policy and keeps its plain `from BB_core import ...`.

Link the checkout in instead, so edits are live and there is nothing to
rebuild:

```bash
powershell -ExecutionPolicy Bypass -File tools/dev_install.ps1
```

### Set up

In the add-on preferences, set the Kitsu server, your email, and the work and
render roots. Everything else lives in the **Kitsu** menu, in the main menu bar
between Window and Help.

**It connects by itself.** The first `Kitsu ▸ Connect to Kitsu…` asks for your
password; with *Remember Password* ticked it goes to the Windows Credential
Manager, and every Blender after that signs in on startup without being
asked — so Connect only reappears on the menu when it could not. There is no
Disconnect: the session costs nothing.

The password is **not** in preferences, because preferences are written to
`userpref.blend` in plain text.

`Kitsu ▸ Browser…` opens the browser: pick a project, then work in either of
its two tabs — **Assets** (asset type ▸ asset) or **Shots** (sequence ▸ shot)
— then a task and a version. Each tab keeps its own selection, so switching
back and forth does not lose your place.

It is a popup, not a dialog: the buttons act as soon as they are clicked, so
there is nothing for an OK to confirm, and clicking away in the viewport
dismisses it. Anything that replaces the open file — Open, New, Append, Link —
waits for the popup to close first, because loading a file frees the data the
popup is drawn from.

**Two panes, not three columns.** The navigation is one expandable tree on the
left — sequence ▸ shot ▸ task, or asset type ▸ asset ▸ task — and the versions
are on the right, each with a picture. Prism spends a third column on
Departments and Tasks because it does not know which application is asking;
this does, so Blender never lists a Compositing task and Nuke never lists
Lighting, and that column has nothing left to say.

Tasks hang under the selected entity only. Kitsu serves tasks per entity, so a
tree that showed them under every shot would cost one request per shot on
every redraw — selecting a shot is what loads them.

Expanding a branch deliberately selects nothing. A dropdown could only ever be
*changed*, so browsing and choosing were one act; a tree lets you open a branch
just to look inside it, and treating that as a selection rewrote the bookmark
to a sequence the remembered shot did not belong to.

Under the tree sits one line — frame range, rate and resolution. Prism gives
this a panel with a thumbnail slot that is empty on most shots. It is three
numbers.

The add-on version is shown in the browser header, read from the manifest so it
cannot disagree with what the Extensions list reports. A junctioned add-on and
a stale copy look identical from the menu otherwise.

The browser **comes back where you left it** — tab, project, sequence and
shot or asset type and asset, and the task. The bookmark lives in the add-on
preferences, so it survives closing Blender. Anything Kitsu no longer has is
quietly dropped rather than restored.

Both trees are **re-read from Kitsu every time the browser opens**. The
session caches them so that narrowing is a local filter, but a cache is only
right until somebody adds a sequence — and the whole refresh costs under
40 ms, so there is no reason to serve stale lists. Your selection is restored
by id afterwards.

| | |
|---|---|
| **Open** | load the selected version |
| **New vNNN** | cut the next version from the startup file |
| **New vNNN from Current Scene** | cut the next version from what is open |
| **Append** | append from the selected version |
| **Link** | link from the selected version |

Append and Link hand straight off to `wm.append` / `wm.link`, because that is
the only file browser that can descend into a `.blend` and list the scenes,
collections and objects inside. It opens already inside the file, in thumbnail
mode.

### The picture on each version

Every version row carries an image. A version saved by this pipeline gets its
own, written next to the scene files in a `.thumbs` folder as it is saved.
Anything older falls back to the Kitsu thumbnail for the shot or asset, so a
row is recognisable even before it has been re-saved.

Kitsu cannot supply a per-version picture, and this is worth stating because it
looks as though it should. Its preview files are numbered by a **revision**
counter that counts publishes *and* review comments — measured against a real
project, revisions 1–4 belonged to comments like "render test" and "reformat",
while the comments naming `_v006.nk` carried no preview at all. Revision 3 is
neither version 3 nor reliably attached to any version. So the picture of a
version has to be taken when the version is saved.

`.thumbs` is a dot-folder beside the work files rather than a parallel tree: it
moves with the work when a shot is relocated, and sync tools skip dot-folders
by default.

### Only the tasks that belong to this application

Each DCC offers only the tasks whose Kitsu **department** it is configured
for. Blender shows the 3D departments; Nuke will show the compositing ones:

```toml
[dcc.blender]
departments = ["Animation", "FX", "Layout", "Lighting", "Modeling"]

[dcc.nuke]
departments = ["Compositing", "2D"]
```

So a shot with both a Lighting and a Compositing task offers only Lighting in
Blender. Kitsu's own `department_id` on each task type drives this — nothing
is hardcoded, and removing `departments` offers every task.

For the server, give a full URL including the port when there is one —
`http://192.168.50.121:8080`. A bare IP with no scheme is assumed to be
`http`, since a certificate is issued for a hostname and never validates
against an IP. If you reach Kitsu through a proxy that redirects to https on
a certificate for its public name, either use that hostname or tick **Skip
Certificate Check**.

If a task has no scene file yet, the browser offers to create v001. Otherwise
pick a version and open it, or use `Kitsu ▸ Save Next Version` to increment the
file you are already in.

### Saving updates Kitsu

Saving a version opens a dialog asking for a **comment** and a **task status**,
then posts them on the Kitsu task with an OpenGL render of the viewport
attached — which Kitsu also uses as the task thumbnail. It is an OpenGL render
rather than a screenshot so the add-on's own dialogs and gizmos never end up
as the shot thumbnail.

The status list comes from Kitsu and starts with **Leave unchanged**, which is
the default. `Kitsu ▸ Update Kitsu…` opens the same dialog for the file already
open, without saving a new version.

It goes through the same `KitsuClient.publish_preview` the render publish will
use, so a save and a render land in Kitsu identically and differ only in the
image attached. The upload runs on a worker thread — the file is already on
disk by the time it starts, so a failed publish costs a comment, never work.

The status only moves when it is deliberately picked. A work-in-progress save
is not a submission, so flipping a task to "waiting for approval" because
someone pressed save would be worse than saying nothing.

Both the publish step and the preview can be switched off in preferences, and the
preview resolution is a percentage of the scene resolution (50% by default —
Kitsu shows it as a thumbnail).

### Frame range and frame rate

Kitsu owns the shot's in and out frames and the project's frame rate and
resolution, so Blender takes all three from there rather than inheriting the
startup file's 1-250 at 1920x1080. Assets have no frame range but do take the
resolution.

Creating a version sets them outright — the scene is new, there is nothing to
lose. **Opening** one re-asks Kitsu and *reports* a difference instead of
reaching into a scene somebody is working in; the Kitsu menu shows what moved
and offers **Apply Kitsu Frame Range**. Set *On Open* to `Apply` in
preferences if you would rather it just fixed itself, or `Ignore` to stop
checking.

Kitsu is loose about both, so the parsing is defensive: `frame_in` and
`frame_out` are usually empty columns with the real values in the shot's
custom `data` dict, `nb_frames` is separate and can disagree, and `fps`
arrives as a string. Broadcast rates keep their pulldown — 23.976 becomes
`fps 24 / fps_base 1.001`, not 24, because rounding drifts a frame every forty
seconds and only shows up once a cut is conformed.

Assets have no frame range, so none of it applies to them.

### Where the scene renders to

Opening or creating a version points the scene's **own** output path at that
version's render folder, so pressing F12 by hand writes where the pipeline
expects rather than into the startup file's `/tmp` or the last shot you had
open:

```
<render_root>/sc01/sh03/Lighting/Render/v012/sh03_v012.
<render_root>/assets/Prop/Kitchen-counter/Modeling/Render/v002/Kitchen-counter_v002.
```

Nothing there repeats what the path already says. The project is the root, the
entity and the task are folders, so the file names only the thing and its
version — enough to stay identifiable if it is copied out on its own, and no
more. The task stays a folder because a prop can be rendered from modelling
and from lighting, and those must not collide.

A shot goes under its sequence and shot, an asset under `assets/` and its
type and name. The trailing dot is deliberate: Blender appends the frame and
the extension itself, giving `<version>.0001.exr` — the same names the
pipeline's own render operators produce, so the review panel finds either.

It is set before anything else on open, and for assets as well as shots. An
asset carries no frame range, so the Kitsu check has nothing to say about a
prop — but a prop still renders, and an output path left pointing at the last
shot is not a disagreement to report, it is frames written into the wrong
folder.

Turn it off with **Set Output Path** in the preferences.

### Rendering and review

Three entries on the Kitsu menu, all filed under the version that produced
them — the path is never typed in, it comes off the same context that named
the scene file:

| | |
|---|---|
| **Render Image** | the current frame |
| **Render Animation** | the frame range, EXR |
| **Render Playblast** | OpenGL pass of the range, straight to H.264 |

```
<render root>/<seq>/<shot>/<task>/internalRender/main/<name_v003>/<name_v003>.1001.exr
                                                     /playblast/<name_v003>.mp4
```

Blender opens the result in its render window, and that window's sidebar
(`N` ▸ **Kitsu**) carries a comment box, a task status and **Submit to Kitsu**.

Submitting converts whatever was rendered into something Kitsu can show and
uploads that, leaving the EXRs where they are: a **sequence becomes an H.264
MP4**, and a **single frame becomes a PNG or a JPEG** — your choice in
preferences, with a quality setting for JPEG. Both go through Blender's colour
management, so a linear EXR arrives graded rather than washed out.

**Uploads go at full show resolution**, and should. Kitsu already keeps two
versions of every movie — the same low/high split ftrack had — and makes the
low one itself:

| | |
|---|---|
| `movies/originals/…` | 3840×2160, what you uploaded |
| `movies/low/…` | 1280×720, generated by Zou |

**Kitsu is told not to re-encode.** Left to itself, Zou conforms every movie
to the project resolution and upscales anything smaller — a 960×540 test
render came back stored as 3840×2160 — and re-encodes what was already H.264.
Uploads therefore go with `?normalize=false`, and Zou keeps the exact bytes
it was handed:

| upload | `normalize=true` | `normalize=false` |
|---|---|---|
| 960×540 movie | stored 3840×2160 | stored **960×540**, byte-identical |

That matters for half and quarter resolution test renders, which should stay
the size they were rendered. The cost is that Zou no longer builds a separate
low-resolution proxy, so the player streams the full file; thumbnails are
still generated either way. *Let Kitsu Re-encode* turns it back on.

The flag only affects movies. **Stills are always converted to PNG** whatever
you send — which is why uploading a JPEG still results in a *larger* file on
the server — and are never upscaled.

*Review Width* caps the longest edge if a slow link makes full size
impractical, but it defaults to off. Anything already smaller is left alone,
and both dimensions come back even because H.264 in yuv420p cannot encode an
odd one.

**PNG is the right default**, for the opposite of the obvious reason. Zou
re-encodes every still to PNG on ingest, so a JPEG upload is stored as a PNG
built from lossy data — which compresses *worse*. Measured against the studio
server:

| uploaded | stored by Kitsu |
|---|---|
| JPEG, 78 KB | PNG, **218 KB** |
| PNG, 118 KB | PNG, **90 KB** |

So JPEG only ever saves upload bandwidth, never storage, and gives up quality
to do it. Worth switching to only on a slow link.

**WebP is not an option** — Zou 1.0.26 rejects it with HTTP 400. H.264 specifically, and the reason is measured rather
than assumed: an H.265 file uploaded to the studio's Zou came back re-encoded
to `avc1` anyway, and its normalised original was *larger* than the H.264 one.
Kitsu transcodes everything to H.264 for browser playback, so encoding to
anything else costs time and adds a decoder dependency for nothing.

The movie is built through the sequencer rather than by shelling out to
ffmpeg, so it inherits the scene's view transform — which is what stops a
linear EXR arriving on Kitsu looking washed out. That build runs on the main
thread, because `bpy` is not thread-safe, so a long sequence will hold the
interface while it encodes; only the upload is backgrounded.

Every render setting the pipeline touches is restored when the job finishes.

### Icons in the Append and Link browsers

Saving also embeds the icons those browsers show. Blender writes the file's own
thumbnail automatically (`file_preview_type` defaults to `AUTO`, and the
pipeline forces it on for its own saves if you have turned it off), and the
add-on renders preview icons for the **scenes and collections** in the file so
they show as thumbnails rather than a list of names — the things you actually
pick when appending. Objects are skipped: generating a preview for every one
in a heavy scene would turn a save into a coffee break. Switch it off with
*Generate Preview Icons*.

Browsing runs inline rather than on a worker thread: measured against the
studio server, a shot or task list takes 4-26 ms and the login 225 ms, which
is far below the point where threading earns its complexity - and inline means
the dialog shows new data on the same redraw. The threaded path is still there
behind `background=True`, for the preview uploads that genuinely take an hour.

## Nuke

Nuke browses **shots only** and offers **compositing tasks only** — an asset
has no comp, and a lighting task has no business opening as a `.nk`. The task
filter is the same department list the Blender side uses for its 3D tasks, so
it is a config change rather than a code change:

```toml
[dcc.nuke]
ext = "nk"
departments = ["Compositing", "2D"]
```

### Install

```bash
"C:/Program Files/Nuke16.0v6/python.exe" nuke/install.py
```

That appends a delimited block to `~/.nuke/menu.py` and leaves everything
around it alone — including a Prism block, which is what is usually already in
there. `--remove` takes it back out. Restart Nuke and there is a **Kitsu** menu
in the menu bar.

`Kitsu ▸ Settings…` takes the server, your email and the roots, and has a
**Test Connection** button — use it, because Kitsu answers a mistyped email
exactly the same way it answers a wrong password, and a typo in an address is
invisible until something reads it back to you.

The password goes to the Windows Credential Manager — the *same* store Blender
and the standalone tools use, so if you have already connected in Blender
there is nothing to type.

### What it does

| | |
|---|---|
| **Browser…** | a shot tree on the left, versions with pictures on the right |
| **Import into Current Script** | paste another version's nodes into the open comp |
| **Create Write Node** | a Write pointed at this version's render path |
| **Save Next Version** | version up the open script |
| **Update Kitsu…** | comment, status and a snapshot for the script already open |

**Import into Current Script** is on its own row under the other three: those
replace what is open, this one adds to it, and a mis-click between the two is
expensive. It uses `nodePaste`, so the nodes arrive selected and ready to move.

### Reading somebody else's renders

The tree lists **every** task on the shot, not only the compositing ones. A
task Nuke does not author is marked `(renders)` and behaves differently: there
is no script to open, so **Open**, **New Version** and **New from Current**
disappear, and the right pane lists the **rendered sequences on disk** for that
task instead of `.nk` versions. **Import into Current Script** brings the
selected one in as a Read node, over the range that was actually rendered.

Which departments Nuke *authors* is still `[dcc.nuke] departments`. That list
now means "tasks a comper works in", not "tasks a comper may look at" —
filtering the tree by it hid the renders a comp is assembled from behind a
department the comper does not own.

The sequence comes off the **render root**, never out of Kitsu. What Kitsu
holds is the review movie: re-encoded to H.264, and often at review
resolution. Comping against that would throw away the float data and the
resolution the render was made at. Kitsu is where a render is *reviewed*; the
render root is where it *is*.

The browser has the same two panes as Blender's — a `QTreeWidget` of sequences,
shots and compositing tasks on the left, a version list with icons on the
right, in a splitter you can drag. Shot facts sit under the tree, and the
version number is in the window title: Nuke caches imported modules, so a
session that was never restarted runs old code while looking identical. If the
title does not say the version you expect, the module did not reload.

Version pictures work exactly as they do in Blender — see
[The picture on each version](#the-picture-on-each-version). A shot with no
Kitsu preview and no saved versions has nothing to show, which is not a
failure; only some shots on a young show have a preview at all.

Creating a version sets the script's **frame range and fps** from Kitsu. The
format is deliberately left alone: a comp's format usually comes from its
plate, so writing one in would override a decision the artist made on purpose.

### The Kitsu Write node

Press **Tab** in the node graph and type *Kitsu Write*, or use
`Kitsu ▸ Create Write Node` (Ctrl+Alt+W). It makes an **ordinary Nuke Write**
with a Kitsu tab added — not a gizmo wrapping one, so every native setting stays where a comper
expects it. A Write whose usual controls are out of reach just gets replaced
with a normal Write, and then the pipeline loses track of the render entirely.

The tab adds the three things a Write cannot do for itself:

| | |
|---|---|
| **Set Output Path** | re-derive the path from the script's version |
| **Add Read Node** | read back exactly what was rendered, over its real frame range |
| **Publish to Kitsu** | convert the render and upload it |

The output path is never typed in — it comes from the same context that named
the script, so a render cannot be filed under a version that did not produce
it. The buttons are PyScript knobs, saved inside the `.nk`, so they still work
in a script somebody else opens.

**Publish builds an H.264 `.mp4`.** "QuickTime" is what everyone calls the
review movie, but the container is the deliberate part: uploads go up with
`normalize=false` so Kitsu keeps exactly the bytes it is given, which means the
file has to be one a browser plays unaided. H.264 in `.mp4` is that everywhere;
the same stream in a `.mov` is not — Safari plays it, Chrome frequently will
not. The movie is built in Nuke, so the frames go through Nuke's own colour
management on the way.

Publishing a save can attach a **snapshot**, which is how a comp gets a thumbnail in
Kitsu at all. Nuke has no viewport to grab, so it is a real one-frame render
through a temporary Write node — of the selected node, or of whatever the
Viewer is looking at. It is written through sRGB, because a linear comp handed
over untagged arrives flat, and the Write node is removed again so the node
graph is left exactly as it was.

The context is stamped onto a hidden knob on the root node — knobs save with
the script, so reopening a comp restores its shot, task, version *and* the
Kitsu ids, which no filename can carry.

### No dependencies, and why that mattered

Nuke 16 ships Python 3.11 with `certifi` and `PySide6` and **no `requests`**,
and pip-installing into a Foundry install directory is not something a
pipeline should do to an artist's machine. So the core lost its last
dependency: HTTP goes through `transport.py`, which uses `requests` where it
exists and `urllib` where it does not.

### What is not there yet

Rendering and review submission. Blender has both; Nuke has the browser, work
files and publishing. Executing Write nodes to the pipeline path is the next
piece.

## Configuration

Everything the pipeline knows about naming and layout is in
`BB_core/presets/default.toml`. To change it without touching the repo, copy
that file and point at the copy — first match wins:

1. the **Config Override** field in the add-on preferences
2. the `BB_PIPELINE_CONFIG` environment variable
3. `~/.BB_pipeline/config.toml`

Overrides merge per key, so a file setting only `naming.version_padding = 4`
keeps every other default.

### Where the layout comes from

If the Kitsu project carries a **file tree**, that is the layout — it is the
studio's answer to where files go, and every DCC reads the same one. The local
config is the fallback, not the authority.

The tree is *translated* into the templates the core already speaks, rather
than calling Kitsu's `/tasks/{id}/working-file-path` per path. That endpoint is
built around Kitsu working-file records and revisions, which would make Kitsu
the version authority instead of the files on disk, and it costs a round trip
each time. Reading the template once keeps versioning where it is and path
building instant. Kitsu's own endpoint returns a folder and a stem with no
revision in either, so the version and extension were always ours to add.

Zou's tokens map onto the generic context fields:

| Kitsu | field | | Kitsu | field |
|---|---|---|---|---|
| `<Project>` | `project` | | `<Shot>` | `entity` (shots) |
| `<Sequence>` | `group` | | `<Asset>` | `entity` (assets) |
| `<AssetType>` | `group` | | `<TaskType>` | `task` |
| `<Department>` | `department` | | `<OutputType>` | `stream` |

A tree using a token with no matching field — `<Episode>`, `<Software>` — is
reported and ignored rather than producing a path with a hole in it.

One deliberate divergence: **Zou lowercases every name** unless the tree says
`uppercase`, with no option to leave them alone. Names are kept as production
spells them (`PizzaHunt_Resto_sh01_Lighting`, not `pizzahunt_resto_sh01_lighting`)
unless the tree explicitly asks for a case.

There is **no field for this in the Kitsu web UI**. It is a JSON column on the
project, set with `PUT /api/data/projects/{id}`.

### Settings in the project Brief

Which is why the **Brief** is also read. Kitsu's project description is a
plain text box anyone can edit in the browser, so a `[bb]` block in it is
treated as configuration — the one place a producer can set a show's root
without a developer:

```
Pizza Hunt - second season. Delivery 12 December.

[bb]
work_root = "I:/PizzaHunt"
render_root = "P:/PizzaHunt/renders"

[bb.naming]
version_padding = 4
```

Bare keys are paths, because roots are the point. Sub-tables address the other
config sections by name, so anything the config file can say the brief can say
too. The prose around the block is left alone, and a brief with no block is
just a brief.

With the roots coming from Kitsu, the add-on preferences need only the server
and your email — the Work Root field can stay empty, and preferences say so
rather than leaving it looking broken.

Resolution order, each winning over the last: built-in defaults, local
`config.toml`, the project's `file_tree`, then the Brief. The Brief goes last
because it is the most deliberate and the most easily corrected. A `[bb]`
block that is not valid TOML is **reported, not ignored** — silently dropping
a typo in a root would write files somewhere unexpected.

Paths can be written the way Windows hands them to you:

```toml
[bb]
work_root = "E:\Misery Loves Company"
render_root = "E:\Misery Loves Company"
```

TOML would normally reject that. A backslash inside a double-quoted string
starts an escape, so `"E:\Misery Loves Company"` is not a path with a typo in
it — it is a parse error at `\M`, and the whole block is discarded, leaving
"Set a Work Root" on screen for a project that plainly has one. A lone
backslash in a `key = "value"` line is doubled before the parser sees it, so
pasted paths, forward slashes and correctly-escaped TOML all mean the same
thing.

Leniency stops there. A block that will not parse for any other reason is
still reported rather than ignored — in the add-on preferences, and in place
of the "Set a Work Root" message that used to hide it.

### macOS, and one root for two platforms

A project root is written once in Kitsu and read by every machine, so
`work_root = "E:\Misery Loves Company"` has to mean something on a Mac too.
It cannot on its own: `E:` is a Windows drive letter, and on macOS those
backslashes are not separators at all — `PurePosixPath` reads the whole
string as a single filename.

So each machine is told where it mounts the disks a root can name. This lives
in the settings file, never in Kitsu, because it describes the computer and
not the show:

Set it in **Edit ▸ Preferences ▸ Add-ons ▸ BB Kitsu Pipeline ▸ Volumes** —
a list, one row per disk, because a show is rarely on one: the plates, the
renders and the work can each be somewhere different.

The panel appears when a root names a disk this machine cannot resolve, names
the letter that is missing, and offers a pre-filled row for it — one click
rather than a blank field you have to know how to fill in. Once mapped it
shows what each root resolves to here, so it can be checked before anything
is opened.

The rows are an editor for the settings file, which is what the core actually
reads, and are filled from it at startup — so a mapping made by hand, or by
the Nuke side, shows up in the list:

```json
"volumes": {"E:": "/Volumes/Misery", "I:": "/Volumes/I 4TB_Externe"}
```

`E:\Misery Loves Company` then resolves to `/Volumes/Misery/Misery Loves
Company` on the Mac and stays put on Windows. The table is read in both
directions, so a brief written on a Mac works on Windows as well.

A drive with no entry is **left exactly as written** rather than having its
letter dropped. `Z:\Show` becoming `/Show` would be a real-looking root that
is not the one anybody meant, failing later and somewhere else; instead the
error names the letter that needs a mapping.

Passwords go to the **Keychain** through `/usr/bin/security`, the same
generic-password item `keyring` uses, so a password saved by the add-on is
visible to the standalone tools and the other way round. Blender ships no
`keyring` and an extension cannot bring wheels, which is why this shells out
rather than importing.

To link a checkout into Blender on macOS or Linux:

```bash
tools/dev_install.sh
```

A symlink, where Windows uses a junction. Note the link is created under
`~/Library/Application Support/Blender`, on the boot disk — an exFAT external
drive cannot hold a symlink itself, but is perfectly fine as the target of
one.

### Roots

**The roots are the project folder** — one show per root, changed when you
change show, so the layout starts inside it:

```
I:/PizzaHunt/knife/pickup/Compositing/PizzaHunt_knife_pickup_Compositing_v001.blend
^^^^^^^^^^^^ work root
```

A studio keeping several shows under one shared root prefixes the templates
with `{project}/` instead.

Either way, a show that needs its own root or its own layout can take a
`[projects.*]` table, keyed on the project code — or its name, when no code is
set in Kitsu. Matching is case-insensitive and anything valid under `[paths]`
can be overridden:

```toml
[projects.PizzaHunt]
work_root = "I:/PizzaHunt"
render_root = "I:/PizzaHunt/_renders"
```

## Tests

```bash
python -m unittest discover -s tests -v
```

`--factory-startup` matters: without it an installed copy of the add-on is
enabled alongside the one under test, and the two fight over the same class
names.

The Blender side has an integration test that stubs the Kitsu session, so the
create / open / increment loop runs offline:

```bash
blender --background --factory-startup --python tests/blender_check.py
```

The Nuke side runs under Nuke's *own* interpreter with `nuke` stubbed, so what
is proved is that the pipeline works in the Python that Nuke ships — no render
licence needed:

```bash
"C:/Program Files/Nuke16.0v6/python.exe" tests/nuke_check.py
```

Three checks in `test_core.py` are static rather than behavioural, because the
bugs they catch only surface when a line runs — and Blender stops drawing a
panel when its draw callback raises, reporting nothing:

- an attribute read off a sibling module that the module does not define;
- a `self.<name>` a class reads but never assigns;
- the version declared in three files that cannot import each other.

Each was written after the bug it describes reached a running DCC, and each is
verified to fail when that bug is put back.

Verified against Blender 4.2, 4.5, 5.1 and 5.2, and Nuke 16.0v6.

## Licence

GPL-3.0-or-later.

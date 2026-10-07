"""Playing the build as an animation (on screen) and recording it to a video.

The timeline (lib/timeline.py) says, for any time, which step is shown, how far exploded and
where the camera is. On screen a background thread fires a custom event about 30 times a second
and each tick draws the frame for the time that has passed (frames are skipped if Fusion is
slow, so the speed stays right). A recording draws every frame in turn and saves it as a picture,
then FFmpeg (if installed) makes an MP4 from them.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

import adsk.core
import adsk.fusion

from . import capture, log, model, timeline

TICK_EVENT = "buildBookAnimTick"
FPS_SCREEN = 60.0              # ticks asked for a second (a slow frame just skips ahead)


def settings_of(manual):
    return dict(timeline.DEFAULTS, **(manual["settings"].get("animation") or {}))


def steps_for(manual, scope, sid=None):
    """[(step id, moves, camera)] for "book", a section (id) or one step (id), in book order."""
    out = []
    default = settings_of(manual).get("allAtOnce", True) is not False
    if scope == "overview":
        # The exploded view of the whole assembly: one "step" with every move in it.
        _, own = model.find_step(manual, model.OVERVIEW_STEP)
        step = model.effective_step(manual, own)
        camera = manual.get("overview", {}).get("camera") or manual.get("cover", {}).get("camera")
        together = own.get("together")
        return [(model.OVERVIEW_STEP, len(step.get("explodes", [])), camera,
                 default if together is None else bool(together))] if step.get("explodes") else []
    for sec in manual["sections"]:
        if scope == "section" and sec["id"] != sid:
            continue
        for step in sec["steps"]:
            if scope == "step" and step["id"] != sid:
                continue
            together = step.get("together")
            out.append((step["id"], len(step.get("explodes", [])), step.get("camera"),
                        default if together is None else bool(together)))
    return out


def scope_title(manual, scope, sid=None):
    if scope == "section":
        sec = model.find_section(manual, sid)
        return sec["title"] if sec else "Section"
    if scope == "step":
        _, step = model.find_step(manual, sid)
        return step["title"] if step else "Step"
    if scope == "overview":
        return "{} - exploded view".format(manual.get("title") or "Build manual")
    return manual.get("title") or "Build manual"


def find_ffmpeg(manual):
    path = (manual["settings"].get("animation") or {}).get("ffmpeg") or ""
    if path and os.path.isfile(path):
        return path
    return shutil.which("ffmpeg")


class Player:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.playing = False
        self.segments = []
        self.manual = None
        self.shown = None           # step id the scene shows now
        self.camera = None          # camera last applied
        self.together = {}          # step id -> its moves all at once
        self.recording = False
        self.start = 0.0
        self.pending = False        # a tick event is queued (don't flood Fusion with them)
        self._thread = None

    # ------------------------------------------------------------ drawing one frame

    def _frame(self, t):
        step_id, p, cam = timeline.at(self.segments, t)
        if step_id is None:
            return
        ctrl = self.ctrl
        if step_id != self.shown:
            ctrl.scene.show(ctrl.design(), self.manual, step_id)
            self.shown = step_id
            if self.playing:
                ctrl.send_panel("playingStep", {"id": step_id})     # the list follows along
        ctrl.scene.set_progress(p, self.together.get(step_id, False), fast=not self.recording)
        # The camera only when it moves (setting it every frame made Fusion redraw everything).
        if cam and cam != self.camera:
            capture.apply_camera(ctrl.app.activeViewport, cam, smooth=False, quiet=True)
            self.camera = cam

    def _prepare(self, scope, sid, reverse):
        manual = self.ctrl.load()
        steps = steps_for(manual, scope, sid)
        if not steps:
            return False
        self.manual = manual
        self.segments = timeline.build(steps, reverse, settings_of(manual))
        self.together = {s[0]: s[3] for s in steps}
        self.shown = None
        self.camera = None
        self.ctrl.crop_overlay.clear()
        self.ctrl.frame_ratio = None            # (no crop frame while it plays)
        return True

    # ------------------------------------------------------------ on screen

    def play(self, scope, sid=None, reverse=False):
        self.stop()
        if not self._prepare(scope, sid, reverse):
            return False
        log.info("animation: playing {} {} ({:.1f} s{})".format(
            scope, sid or "", timeline.total(self.segments), ", reversed" if reverse else ""))
        self.playing = True
        self.start = time.perf_counter()
        self._frame(0.0)
        self.ctrl.app.activeViewport.refresh()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        app = adsk.core.Application.get()
        while self.playing:
            if not self.pending:
                self.pending = True
                app.fireCustomEvent(TICK_EVENT, "")
            time.sleep(1.0 / FPS_SCREEN)

    def tick(self):
        self.pending = False
        if not self.playing:
            return
        t = time.perf_counter() - self.start
        try:
            t0 = time.perf_counter()
            before = self.shown
            self._frame(t)
            t1 = time.perf_counter()
            if self.shown != before:
                # Drawing a new step takes a moment: hold the clock for it, so the camera and parts
                # carry on from where they were instead of jumping ahead.
                self.start += t1 - t0
            self.ctrl.app.activeViewport.refresh()
            ms = (time.perf_counter() - t0) * 1000
            if ms > 45:
                moved, t_move, trails, t_trail = self.ctrl.scene.anim_stats
                log.info("animation: slow frame at {:.2f} s ({:.0f} ms: {} entities moved in {:.0f} ms, {} trails in "
                         "{:.0f} ms, redraw {:.0f} ms; step {})".format(
                             t, ms, moved, t_move, trails, t_trail, (time.perf_counter() - t1) * 1000, self.shown))
        except Exception:
            log.error("animation frame")
            self.stop()
            return
        if t >= timeline.total(self.segments):
            self.stop()

    def stop(self):
        """Stop playing; the last frame stays on screen (opening a step shows it as usual)."""
        if not self.playing:
            return
        self.playing = False
        try:
            self.ctrl.scene.show_all_edges()
            self.ctrl.app.activeViewport.refresh()
        except Exception:
            pass
        log.info("animation: stopped")
        self.ctrl.push_state()

    # ------------------------------------------------------------ recording

    def record(self, scope, sid=None, reverse=False):
        """Record the book / a section / a step to one MP4 (FFmpeg) in the export folder."""
        self.stop()
        manual = self.ctrl.load()
        if not steps_for(manual, scope, sid):
            return
        title = capture.safe_filename("{} - {}".format(
            scope_title(manual, scope, sid), "disassembly" if reverse else "assembly"))
        out_dir = capture.export_folder(manual)
        message = self._record_jobs(manual, [(scope, sid, title)], reverse, out_dir)
        self.ctrl.notify(message)
        self.ctrl.push_state()

    def record_each(self, scope, sid=None, reverse=False):
        """Every step of the book / a section to a video of its own, in the export folder's
        "Animations" folder, named like the step pictures ("Section 1 - Step 2 - Name - assembly")."""
        self.stop()
        manual = self.ctrl.load()
        jobs = []
        if scope == "overview":                     # (one "step": the same as one video)
            self.record(scope, sid, reverse)
            return
        for step_id, _, _, _ in steps_for(manual, scope, sid):
            _, step = model.find_step(manual, step_id)
            base = capture.image_name(manual, step)[:-4]                 # (without ".png")
            jobs.append(("step", step_id, capture.safe_filename(
                "{} - {}".format(base, "disassembly" if reverse else "assembly"))))
        if not jobs:
            return
        out_dir = os.path.join(capture.export_folder(manual), "Animations")
        message = self._record_jobs(manual, jobs, reverse, out_dir)
        self.ctrl.notify(message)
        self.ctrl.push_state()

    def _record_jobs(self, manual, jobs, reverse, out_dir):
        """Record each (scope, id, file title) in turn under one progress bar; returns the message."""
        ctrl = self.ctrl
        anim = settings_of(manual)
        fps = max(5, min(60, int(anim.get("fps") or 30)))
        video = json.loads(json.dumps(manual))
        video["settings"].setdefault("image", {}).update({"width": int(anim.get("videoWidth") or 1280),
                                                          "transparent": False})
        # Work out every timeline first, for one progress bar over all the frames.
        plans = []
        for scope, sid, title in jobs:
            steps = steps_for(manual, scope, sid)
            segments = timeline.build(steps, reverse, anim)
            plans.append((title, steps, segments, int(timeline.total(segments) * fps) + 1))
        total_frames = sum(p[3] for p in plans)
        progress = ctrl.ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.show("BuildBook", "Recording frame %v of %m...", 0, total_frames, 0)
        os.makedirs(out_dir, exist_ok=True)
        ffmpeg = find_ffmpeg(manual)
        camera = capture.camera_to_dict(ctrl.app.activeViewport.camera)
        done, kept_frames, failed, cancelled, t0 = [], [], [], False, time.perf_counter()
        frame_no = 0
        self.recording = True
        try:
            for title, steps, segments, frames in plans:
                self.manual, self.segments = manual, segments
                self.together = {s[0]: s[3] for s in steps}
                self.shown, self.camera = None, None
                ctrl.crop_overlay.clear()
                ctrl.frame_ratio = None
                folder = tempfile.mkdtemp(prefix="buildbook-video-")
                box = None
                for i in range(frames):
                    if progress.wasCancelled:
                        cancelled = True
                        break
                    self._frame(i / float(fps))
                    box = capture.render_raw(ctrl.app, video, os.path.join(folder, "f_{:05d}.png".format(i)))
                    frame_no += 1
                    progress.progressValue = frame_no
                if cancelled:
                    shutil.rmtree(folder, ignore_errors=True)
                    break
                if ffmpeg:
                    out = os.path.join(out_dir, title + ".mp4")
                    try:
                        _encode(ffmpeg, folder, fps, box, out)
                        shutil.rmtree(folder, ignore_errors=True)
                        done.append(out)
                        log.info("animation: video " + out)
                        continue
                    except Exception as err:
                        log.error("make the video")
                        failed.append(str(err))
                kept_frames.append(_keep_frames(folder, out_dir, title))
        finally:
            self.recording = False
            progress.hide()
            capture.apply_camera(ctrl.app.activeViewport, camera, smooth=False, quiet=True)
        log.info("animation: recorded {} frame(s) in {:.1f} s".format(frame_no, time.perf_counter() - t0))
        if cancelled:
            return "Recording cancelled{}.".format(
                " after {} video(s) in {}".format(len(done), out_dir) if done else "")
        if done and not kept_frames:
            return "Video saved: " + done[0] if len(done) == 1 else                 "Saved {} videos in {}".format(len(done), out_dir)
        if not ffmpeg:
            return ("No FFmpeg found, so the frames were saved as pictures in {}. Install FFmpeg "
                    "(e.g. “winget install ffmpeg”), restart Fusion and record again for "
                    "MP4s.".format(out_dir if len(kept_frames) > 1 else kept_frames[0]))
        return "FFmpeg couldn't make {} video(s) ({}); their frames are in {}".format(
            len(failed), failed[0] if failed else "", out_dir)


def _keep_frames(folder, out_dir, title):
    dest = os.path.join(out_dir, title + " frames")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(folder, dest)
    return dest


def _encode(ffmpeg, folder, fps, box, out):
    if box is not None:
        x, y, w, h = box
        vf = "crop={}:{}:{}:{}".format(w - w % 2, h - h % 2, x, y)      # (H.264 needs even sizes)
    else:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps),
           "-i", os.path.join(folder, "f_%05d.png"), "-vf", vf,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900, creationflags=flags)
    if result.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError((result.stderr or "exit code {}".format(result.returncode)).strip()[:200])

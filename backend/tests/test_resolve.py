from app import resolve_bridge


class FakeItem:
    def __init__(self, path):
        self.path, self.metadata, self.markers = path, {}, []

    def GetClipProperty(self, key):
        return {"File Path": self.path, "FPS": "60"}.get(key)

    def SetMetadata(self, data):
        self.metadata.update(data)
        return True

    def AddMarker(self, frame, color, name, note, duration):
        self.markers.append((frame, color, name))
        return True


class FakeFolder:
    def __init__(self, name):
        self.name, self.subs, self.clips = name, [], []

    def GetName(self):
        return self.name

    def GetSubFolderList(self):
        return self.subs

    def GetClipList(self):
        return self.clips


class FakePool:
    def __init__(self):
        self.root, self.current, self.appended = FakeFolder("Master"), None, []

    def GetRootFolder(self):
        return self.root

    def AddSubFolder(self, parent, name):
        f = FakeFolder(name)
        parent.subs.append(f)
        return f

    def SetCurrentFolder(self, f):
        self.current = f

    def ImportMedia(self, paths):
        items = [FakeItem(p) for p in paths]
        self.current.clips.extend(items)
        return items

    def CreateEmptyTimeline(self, name):
        return object()

    def AppendToTimeline(self, entries):
        self.appended.extend(entries)
        return True


class FakeProject:
    def __init__(self):
        self.pool, self.timeline = FakePool(), None

    def GetMediaPool(self):
        return self.pool

    def GetSetting(self, key):
        return "30"

    def GetCurrentTimeline(self):
        return self.timeline

    def SetCurrentTimeline(self, t):
        self.timeline = t


class FakeResolve:
    def __init__(self):
        self.project = FakeProject()

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self.project


def test_push_clips_builds_bins_metadata_markers(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    r = FakeResolve()
    item = {"path": str(video), "title": "LOL", "summary": "scream", "tags": ["jumpscare"],
            "category": "Jumpscare / Scary", "streamer": "Alpha",
            "moments": [{"t": 2, "description": "monster"}], "best_in": 1, "best_out": 3}
    out = resolve_bridge.push_clips(r, [item], "Twitch Clips", append=True)
    assert out == {"imported": 1, "already_there": 0, "errors": []}
    cat = r.project.pool.root.subs[0].subs[0].subs[0]
    assert cat.GetName() == "Jumpscare / Scary"
    clip = cat.clips[0]
    assert clip.metadata["Keywords"] == "jumpscare"
    assert clip.markers == [(120, "Purple", "monster")]
    assert r.project.pool.appended[0]["startFrame"] == 60

    again = resolve_bridge.push_clips(r, [item], "Twitch Clips")
    assert again["already_there"] == 1 and len(cat.clips) == 1


def test_missing_file_reported(tmp_path):
    out = resolve_bridge.push_clips(FakeResolve(), [{"path": str(tmp_path / "nope.mp4"), "title": "x"}])
    assert out["imported"] == 0 and out["errors"]

import { useEffect, useState } from "react";
import { api, type Settings, type Status } from "../api";
import { Button, Group, LargeTitle, Row, Segmented, TextInput, Toggle, useToast } from "../components/ui";

// Rough per-clip usage: ~6 frames at 512px plus transcript, two calls (describe + double-check).
const CLAUDE_PRICES: Record<string, [number, number]> = {
  "claude-opus-5-5": [4, 20],
  "claude-sonnet-5-5": [2, 10],
  "claude-haiku-4-5": [1, 5],
};
function perClipCost(model: string) {
  const [inp, out] = CLAUDE_PRICES[model] ?? [4, 20];
  return (6000 * inp + 1500 * out) / 1_000_000;
}

function Dot({ ok }: { ok: boolean }) {
  return <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: ok ? "var(--green)" : "var(--orange)" }} />;
}

export function SettingsPage({ theme, setTheme, onSaved }: { theme: string; setTheme: (t: string) => void; onSaved: () => void }) {
  const toast = useToast();
  const [s, setS] = useState<Settings | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [dirty, setDirty] = useState<Settings>({});
  const [saving, setSaving] = useState(false);

  const loadStatus = () => api.get<Status>("/api/status").then(setStatus).catch(() => {});
  useEffect(() => {
    api.get<{ settings: Settings }>("/api/settings").then((r) => setS(r.settings));
    loadStatus();
  }, []);

  if (!s) return null;
  const v = { ...s, ...dirty };
  const set = (k: string, val: string | number | boolean) => setDirty((d) => ({ ...d, [k]: val }));
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.put<{ settings: Settings }>("/api/settings", dirty);
      setS(r.settings);
      setDirty({});
      toast("Saved");
      loadStatus();
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  const text = (k: string, placeholder = "", type = "text") => (
    <TextInput type={type} value={String(v[k] ?? "")} placeholder={placeholder} onChange={(e) => set(k, type === "number" ? Number(e.target.value) : e.target.value)} aria-label={k} />
  );
  const hasChanges = Object.keys(dirty).length > 0;

  return (
    <div className="mx-auto max-w-2xl px-4 pb-40 sm:px-6">
      <LargeTitle title="Settings" />

      <Group title="Appearance">
        <div className="p-3"><Segmented value={theme} onChange={setTheme} options={[{ value: "system", label: "Auto" }, { value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} /></div>
      </Group>

      <Group
        title={<span className="flex items-center gap-2">Twitch {status && <Dot ok={status.twitch} />}</span>}
        footer={<>Free: go to <a className="text-accent" href="https://dev.twitch.tv/console/apps/create" target="_blank" rel="noreferrer">dev.twitch.tv/console</a>, register an app (OAuth redirect <code>http://localhost</code>, category “Other”, client type “Confidential”), then copy the Client ID and press “New Secret”.</>}
      >
        <Row label="Client ID">{text("twitch_client_id", "paste here")}</Row>
        <Row label="Client Secret" last>{text("twitch_client_secret", "paste here", "password")}</Row>
      </Group>

      <Group
        title="Who watches the clips"
        footer={v.ai_mode === "local"
          ? "Local runs on your graphics card with Ollama. Free, private, works offline."
          : `Claude runs online. Roughly $${perClipCost(String(v.claude_model)).toFixed(3)} per clip with ${v.claude_model} — about $${(perClipCost(String(v.claude_model)) * 1000).toFixed(0)} per 1,000 clips.`}
      >
        <div className="p-3">
          <Segmented value={String(v.ai_mode)} onChange={(m) => set("ai_mode", m)} options={[{ value: "local", label: "Local (offline)" }, { value: "claude", label: "Claude (online)" }]} />
        </div>
      </Group>

      <Group
        title={<span className="flex items-center gap-2">Local AI (Ollama) {status && <Dot ok={status.local.ok} />}</span>}
        footer={status && !status.local.ok ? status.local.message : "Install Ollama from ollama.com, then run: ollama pull qwen2.5vl:7b (fits in 8 GB of VRAM)."}
      >
        <Row label="Ollama address">{text("ollama_url")}</Row>
        <Row label="Vision model" last>{text("ollama_model")}</Row>
      </Group>

      <Group
        title={<span className="flex items-center gap-2">Claude {status && <Dot ok={status.claude.ok} />}</span>}
        footer={<>Get a key at <a className="text-accent" href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">console.anthropic.com</a>. Cross-check sends only clips the local AI was unsure about to Claude.</>}
      >
        <Row label="API key">{text("anthropic_api_key", "sk-ant-…", "password")}</Row>
        <Row label="Model">
          <select className="bg-transparent text-right text-[16px] text-label2 outline-none" value={String(v.claude_model)} onChange={(e) => set("claude_model", e.target.value)} aria-label="Claude model">
            <option value="claude-opus-5-5">Opus 5.5 (best)</option>
            <option value="claude-sonnet-5-5">Sonnet 5.5</option>
            <option value="claude-haiku-4-5">Haiku 4.5 (cheapest)</option>
          </select>
        </Row>
        <Row label="Effort" detail="Higher = more careful, slower, pricier">
          <div className="w-44"><Segmented size="sm" value={String(v.claude_effort)} onChange={(e) => set("claude_effort", e)} options={[{ value: "low", label: "Low" }, { value: "medium", label: "Med" }, { value: "high", label: "High" }]} /></div>
        </Row>
        <Row label="Cross-check unsure local results" last>
          <Toggle checked={!!v.claude_cross_check} onChange={(b) => set("claude_cross_check", b)} label="Cross-check" />
        </Row>
      </Group>

      <Group
        title={<span className="flex items-center gap-2">Speech to text {status && <Dot ok={status.whisper} />}</span>}
        footer="Whisper runs locally in both modes. With two graphics cards, put Whisper on the second one (GPU 1) and start Ollama on the first to use both at once."
      >
        <Row label="Whisper model">
          <select className="bg-transparent text-right text-[16px] text-label2 outline-none" value={String(v.whisper_model)} onChange={(e) => set("whisper_model", e.target.value)} aria-label="Whisper model">
            {["base", "small", "medium", "large-v3", "large-v3-turbo"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Row>
        <Row label="Device">
          <div className="w-44"><Segmented size="sm" value={String(v.whisper_device)} onChange={(d) => set("whisper_device", d)} options={[{ value: "auto", label: "Auto" }, { value: "cuda", label: "GPU" }, { value: "cpu", label: "CPU" }]} /></div>
        </Row>
        <Row label="GPU number" last>{text("whisper_gpu_index", "0", "number")}</Row>
      </Group>

      <Group title="Processing" footer={status ? `FFmpeg ${status.ffmpeg ? "found" : "missing"} · meaning search ${status.embeddings ? "on" : status.embeddings_state === "loading" ? "downloading its model…" : "off (pip install fastembed)"}` : undefined}>
        <Row label="Analyse new clips automatically"><Toggle checked={!!v.auto_analyze_new_clips} onChange={(b) => set("auto_analyze_new_clips", b)} label="Auto analyse" /></Row>
        <Row label="Re-check unsure clips when idle"><Toggle checked={!!v.background_recheck} onChange={(b) => set("background_recheck", b)} label="Background recheck" /></Row>
        <Row label="Frames per clip" detail="More = better understanding, slower">{text("frames_per_clip", "6", "number")}</Row>
        <Row label="Flag for review below" detail="Confidence 0–1">{text("review_threshold", "0.6", "number")}</Row>
        <Row label="Clip folder" detail="Where downloaded clips are saved" last>{text("library_dir")}</Row>
      </Group>

      <Group title="DaVinci Resolve" footer="Resolve Studio: Preferences › System › General › External scripting using: Local. Or copy resolve/Clip Manager.py into Resolve's Scripts/Utility folder and run it from Workspace › Scripts.">
        <Row label="Bin name" last>{text("resolve_bin_root")}</Row>
      </Group>

      {hasChanges && (
        <div className="glass fixed inset-x-3 bottom-[88px] z-40 mx-auto md:bottom-6 md:left-[252px] flex max-w-md items-center justify-between rounded-[18px] p-2 pl-4 shadow-[var(--shadow)]">
          <span className="text-[14px] font-medium">Unsaved changes</span>
          <div className="flex gap-2">
            <Button kind="gray" onClick={() => setDirty({})}>Discard</Button>
            <Button kind="filled" busy={saving} onClick={save}>Save</Button>
          </div>
        </div>
      )}
    </div>
  );
}

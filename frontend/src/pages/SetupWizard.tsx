import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  api, fmtBytes, type CheckResult, type OllamaStatus, type ResolveStatus, type Settings,
} from "../api";
import { Button, Icon, Segmented, Spinner, Toggle, useToast } from "../components/ui";

const STEPS = ["Welcome", "Twitch", "AI", "Resolve", "Streamer"] as const;

export function SetupWizard({ startAt = 0, onDone }: { startAt?: number; onDone: () => void }) {
  const [step, setStep] = useState(startAt);
  const [dir, setDir] = useState(1);
  const go = (n: number) => {
    setDir(n > step ? 1 : -1);
    setStep(Math.max(0, Math.min(STEPS.length - 1, n)));
  };
  const finish = async () => {
    await api.post("/api/setup/complete", {});
    onDone();
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-bg/95 p-3 backdrop-blur-xl sm:p-6" role="dialog" aria-label="Setup">
      <div className="flex max-h-full w-full max-w-xl flex-col overflow-hidden rounded-[24px] bg-bg2 shadow-[var(--shadow)]">
        <div className="flex items-center justify-between px-5 pt-4">
          <div className="flex gap-1.5" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
            {STEPS.map((s, i) => (
              <span key={s} className={`h-1.5 rounded-full transition-all ${i === step ? "w-6 bg-accent" : i < step ? "w-1.5 bg-accent/60" : "w-1.5 bg-fill"}`} />
            ))}
          </div>
          <button className="text-[14px] text-label2" onClick={finish}>Skip setup</button>
        </div>
        <div className="relative flex-1 overflow-y-auto px-5 pb-5 pt-3">
          <AnimatePresence mode="wait" custom={dir}>
            <motion.div
              key={step}
              custom={dir}
              initial={{ opacity: 0, x: 30 * dir }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -30 * dir }}
              transition={{ duration: 0.2 }}
            >
              {step === 0 && <Welcome next={() => go(1)} />}
              {step === 1 && <TwitchStep back={() => go(0)} next={() => go(2)} />}
              {step === 2 && <AIStep back={() => go(1)} next={() => go(3)} />}
              {step === 3 && <ResolveStep back={() => go(2)} next={() => go(4)} />}
              {step === 4 && <StreamerStep back={() => go(3)} finish={finish} />}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

/* ---------- shared bits ---------- */

function Header({ icon, title, children }: { icon: string; title: string; children?: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-[14px] bg-accentsoft text-accent">
        <Icon name={icon} size={26} />
      </div>
      <h2 className="text-[26px] font-bold leading-tight">{title}</h2>
      {children && <p className="mt-1 text-[15px] leading-relaxed text-label2">{children}</p>}
    </div>
  );
}

function Nav({ back, next, nextLabel = "Continue", nextDisabled, skip }: {
  back?: () => void; next: () => void; nextLabel?: string; nextDisabled?: boolean; skip?: () => void;
}) {
  return (
    <div className="mt-6 flex items-center gap-2">
      {back && <Button kind="gray" onClick={back}>Back</Button>}
      <div className="flex-1" />
      {skip && <button className="px-3 text-[15px] text-label2" onClick={skip}>Skip for now</button>}
      <Button kind="filled" onClick={next} disabled={nextDisabled}>{nextLabel}</Button>
    </div>
  );
}

function Result({ r }: { r: CheckResult | null }) {
  if (!r) return null;
  return (
    <div className={`mt-3 flex items-start gap-2 rounded-[12px] p-3 text-[14px] ${r.ok ? "bg-[color-mix(in_srgb,var(--green)_14%,transparent)]" : "bg-[color-mix(in_srgb,var(--red)_14%,transparent)]"}`} role="status">
      <span className={r.ok ? "text-good" : "text-bad"}><Icon name={r.ok ? "check" : "close"} size={18} /></span>
      <span>{r.message}</span>
    </div>
  );
}

function Field({ label, ...props }: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="mb-1 block text-[13px] text-label2">{label}</span>
      <input {...props} aria-label={label} className="w-full rounded-[12px] bg-bg3 px-3 py-2.5 text-[16px] outline-none placeholder:text-label3 focus:ring-2 focus:ring-accent/40" />
    </label>
  );
}

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className="space-y-2">
      {items.map((it, i) => (
        <li key={i} className="flex gap-3 text-[15px]">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-fill text-[13px] font-semibold">{i + 1}</span>
          <span className="pt-0.5 leading-snug">{it}</span>
        </li>
      ))}
    </ol>
  );
}

/* ---------- steps ---------- */

function Welcome({ next }: { next: () => void }) {
  return (
    <div>
      <Header icon="sparkle" title="Welcome to Clip Manager">
        A few quick steps and the AI will start sorting clips for you. It takes about 5 minutes, and you can change everything later in Settings.
      </Header>
      <Steps items={["Connect Twitch (free keys)", "Choose the AI: offline on your graphics card, or Claude online", "Connect DaVinci Resolve (optional)", "Add your first streamer"]} />
      <Nav next={next} nextLabel="Let's go" />
    </div>
  );
}

function TwitchStep({ back, next }: { back: () => void; next: () => void }) {
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CheckResult | null>(null);

  useEffect(() => {
    api.get<{ settings: Settings }>("/api/settings").then(({ settings }) => {
      setId(String(settings.twitch_client_id || ""));
      setSecret(String(settings.twitch_client_secret || ""));
    });
  }, []);

  const test = async () => {
    setBusy(true);
    try {
      await api.put("/api/settings", { twitch_client_id: id.trim(), twitch_client_secret: secret.trim() });
      setResult(await api.post<CheckResult>("/api/setup/test-twitch", { client_id: id.trim(), client_secret: secret.trim() }));
    } catch (e) {
      setResult({ ok: false, message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <Header icon="people" title="Connect Twitch">
        Twitch gives out free keys so apps can list clips. You only do this once.
      </Header>
      <Steps items={[
        <>Open the Twitch developer console and log in. <button className="font-semibold text-accent" onClick={() => window.open("https://dev.twitch.tv/console/apps/create", "_blank")}>Open it ↗</button></>,
        <>Name: anything (e.g. <b>my-clip-manager</b>). OAuth Redirect URL: <code className="rounded bg-fill px-1">http://localhost</code> then press <b>Add</b>.</>,
        <>Category: <b>Other</b>. Client type: <b>Confidential</b>. Tick the captcha and press <b>Create</b>.</>,
        <>Click <b>Manage</b> on your app, copy the <b>Client ID</b>, then press <b>New Secret</b> and copy that.</>,
      ]} />
      <div className="mt-4 space-y-3">
        <Field label="Client ID" value={id} onChange={(e) => setId(e.target.value)} placeholder="paste here" autoComplete="off" />
        <Field label="Client Secret" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="paste here" type="password" autoComplete="off" />
        <Button onClick={test} busy={busy} disabled={!id.trim() || !secret.trim()}>Test &amp; save</Button>
      </div>
      <Result r={result} />
      <Nav back={back} next={next} nextDisabled={!result?.ok} skip={result?.ok ? undefined : next} />
    </div>
  );
}

function AIStep({ back, next }: { back: () => void; next: () => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<"local" | "claude">("local");
  const [o, setO] = useState<OllamaStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [picked, setPicked] = useState<{ store: string | null; model: string } | null>(null);
  const [key, setKey] = useState("");
  const [keyResult, setKeyResult] = useState<CheckResult | null>(null);
  const [crossCheck, setCrossCheck] = useState(false);
  const [applied, setApplied] = useState<string[]>([]);

  const load = useCallback(() => api.get<OllamaStatus>("/api/ollama/status").then((s) => {
    setO(s);
    setPicked((p) => p ?? { store: s.choice.store, model: s.choice.model });
  }).catch(() => {}), []);

  useEffect(() => {
    api.get<{ settings: Settings }>("/api/settings").then(({ settings }) => {
      setMode(settings.ai_mode === "claude" ? "claude" : "local");
      setKey(String(settings.anthropic_api_key || ""));
      setCrossCheck(!!settings.claude_cross_check);
    });
    load();
  }, [load]);

  // Keep the Ollama status fresh while downloading/starting.
  useEffect(() => {
    const t = setInterval(load, o?.pull && !o.pull.done && o.pull.status !== "idle" ? 1500 : 5000);
    return () => clearInterval(t);
  }, [load, o?.pull]);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try {
      await fn();
      await load();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const use = () => act("use", async () => {
    const r = await api.post<{ done: string[] }>("/api/ollama/use", picked);
    setApplied(r.done);
  });

  const testKey = async () => {
    setBusy("key");
    try {
      await api.put("/api/settings", { anthropic_api_key: key.trim() });
      setKeyResult(await api.post<CheckResult>("/api/setup/test-claude", { api_key: key.trim() }));
    } finally {
      setBusy(null);
    }
  };

  const saveAndNext = async () => {
    await api.put("/api/settings", { ai_mode: mode, claude_cross_check: crossCheck });
    next();
  };

  const pull = o?.pull;
  const pulling = !!pull && !pull.done && pull.status !== "idle";
  const chosenStore = o?.stores.find((s) => s.path === picked?.store);
  const chosenHasModel = !!chosenStore?.models.some((m) => m.name === picked?.model && m.complete);
  const localReady = !!o?.running && !o.mismatch && (chosenHasModel || (pull?.done && !pull.error && pull.model === picked?.model));

  return (
    <div>
      <Header icon="sparkle" title="Choose the AI">
        The AI watches each clip. <b>Offline</b> runs free on your graphics card. <b>Claude</b> runs online, is more accurate and costs a few cents per clip.
      </Header>
      <Segmented value={mode} onChange={setMode} options={[{ value: "local", label: "Offline (your GPU)" }, { value: "claude", label: "Claude (online)" }]} />

      {mode === "local" && (
        <div className="mt-4 space-y-3">
          {!o ? (
            <div className="flex items-center gap-2 text-label2"><Spinner /> Looking for Ollama and your models…</div>
          ) : !o.installed ? (
            <div className="rounded-[14px] bg-bg3 p-4 text-[15px]">
              <b>Ollama isn't installed.</b> It's the free program that runs the offline AI.
              <div className="mt-3 flex flex-wrap gap-2">
                <Button onClick={() => window.open("https://ollama.com/download/windows", "_blank")}>Download Ollama ↗</Button>
                <Button kind="gray" onClick={() => act("check", load)} busy={busy === "check"}>I've installed it: check again</Button>
              </div>
            </div>
          ) : (
            <>
              <div className="rounded-[14px] bg-bg3 p-3">
                <div className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-label2">Your model folders</div>
                {o.stores.length === 0 && <div className="text-[14px] text-label2">No existing Ollama models found.</div>}
                <div className="space-y-2">
                  {o.stores.map((st) => (
                    <div key={st.path} className="rounded-[12px] bg-bg2 p-2.5">
                      <div className="truncate text-[13px] font-medium" title={st.path}>{st.path} <span className="text-label2">· {fmtBytes(st.size)}</span></div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {st.models.filter((m) => m.vision).length === 0 && <span className="text-[13px] text-label3">no vision models here</span>}
                        {st.models.filter((m) => m.vision).map((m) => {
                          const on = picked?.store === st.path && picked?.model === m.name;
                          return (
                            <button key={m.name} disabled={!m.complete} onClick={() => setPicked({ store: st.path, model: m.name })}
                              className={`rounded-full px-2.5 py-1 text-[13px] ${on ? "bg-accent text-white" : "bg-fill2"} disabled:opacity-40`}
                              title={m.complete ? fmtBytes(m.size) : "incomplete download"}>
                              {m.name}{!m.complete && " (incomplete)"}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
                {o.notes.length > 0 && <div className="mt-2 text-[12px] text-label3">{o.notes.join(" · ")}</div>}
                <p className="mt-2 text-[13px] text-label2">{o.choice.reason}</p>
              </div>

              <Button kind="filled" onClick={use} busy={busy === "use"} disabled={!picked}>
                Use {picked?.model}{picked?.store ? " from this folder" : ""}
              </Button>
              {applied.length > 0 && <div className="text-[13px] text-label2">{applied.join(" · ")}</div>}

              {o.running && o.mismatch && (
                <div className="rounded-[14px] bg-[color-mix(in_srgb,var(--orange)_16%,transparent)] p-3 text-[14px]">
                  Ollama is running with a different model folder.
                  <div className="mt-2"><Button onClick={() => act("restart", () => api.post("/api/ollama/restart", { store: picked?.store }))} busy={busy === "restart"}>Restart Ollama with {picked?.store}</Button></div>
                </div>
              )}
              {!o.running && (
                <Button onClick={() => act("start", () => api.post("/api/ollama/start"))} busy={busy === "start"}>Start Ollama</Button>
              )}
              {o.running && !chosenHasModel && picked && (
                <div className="rounded-[14px] bg-bg3 p-3">
                  <div className="text-[14px]">{picked.model} still needs downloading (about 6 GB, one time).</div>
                  {pulling || pull?.done ? (
                    <div className="mt-2">
                      <div className="h-2 overflow-hidden rounded-full bg-fill">
                        <motion.div className="h-full rounded-full bg-accent" animate={{ width: `${pull?.percent ?? 0}%` }} />
                      </div>
                      <div className="mt-1 text-[13px] text-label2">{pull?.error ? `Failed: ${pull.error}` : `${pull?.percent ?? 0}% · ${pull?.status}`}</div>
                    </div>
                  ) : null}
                  {!pulling && <div className="mt-2"><Button onClick={() => act("pull", () => api.post("/api/ollama/pull", { model: picked.model }))} busy={busy === "pull"}>{pull?.error ? "Try again" : "Download now"}</Button></div>}
                </div>
              )}
              {localReady && <Result r={{ ok: true, message: `Offline AI is ready: ${picked?.model}` }} />}
            </>
          )}
        </div>
      )}

      <div className={`mt-4 space-y-3 ${mode === "claude" ? "" : "rounded-[14px] bg-bg3 p-3"}`}>
        {mode === "local" && <div className="text-[13px] text-label2">Optional: add a Claude key so clips the offline AI isn't sure about can be double-checked online.</div>}
        <Field label="Claude API key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-ant-…" type="password" autoComplete="off" />
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={testKey} busy={busy === "key"} disabled={!key.trim()}>Test &amp; save</Button>
          <button className="text-[14px] text-accent" onClick={() => window.open("https://console.anthropic.com/settings/keys", "_blank")}>Get a key ↗</button>
        </div>
        <Result r={keyResult} />
        {mode === "local" && keyResult?.ok && (
          <label className="flex items-center justify-between gap-3 text-[15px]">
            Double-check unsure clips with Claude
            <Toggle checked={crossCheck} onChange={setCrossCheck} label="Cross-check" />
          </label>
        )}
      </div>
      <Nav back={back} next={saveAndNext} nextDisabled={mode === "claude" ? !keyResult?.ok : false} skip={mode === "claude" && !keyResult?.ok ? saveAndNext : undefined} />
    </div>
  );
}

function ResolveStep({ back, next }: { back: () => void; next: () => void }) {
  const [s, setS] = useState<ResolveStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<CheckResult | null>(null);
  useEffect(() => {
    api.get<ResolveStatus>("/api/resolve/status").then(setS).catch(() => {});
  }, []);
  const install = async () => {
    setBusy("install");
    try {
      setS(await api.post<ResolveStatus>("/api/resolve/install-script"));
    } finally {
      setBusy(null);
    }
  };
  const check = async () => {
    setBusy("test");
    try {
      setTest(await api.post<CheckResult>("/api/resolve/test"));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div>
      <Header icon="send" title="Connect DaVinci Resolve">
        Send clips straight into your Resolve project, sorted into bins with markers at the key moments.
      </Header>
      {s && !s.resolve_installed && <p className="mb-3 rounded-[12px] bg-bg3 p-3 text-[14px] text-label2">Resolve doesn't seem to be installed on this PC. You can skip this step.</p>}
      <Steps items={[
        <>In Resolve Studio open <b>Preferences → System → General</b> and set <b>External scripting using</b> to <b>Local</b>. Restart Resolve.</>,
        <>Install the <b>Clip Manager</b> script into Resolve's Workspace → Scripts menu:
          <div className="mt-2 flex items-center gap-2">
            <Button onClick={install} busy={busy === "install"}>{s?.script_current ? "Reinstall script" : "Install script"}</Button>
            {s?.script_current && <span className="text-good"><Icon name="check" /></span>}
          </div>
        </>,
        <>Open a project in Resolve, then test the connection:
          <div className="mt-2"><Button kind="gray" onClick={check} busy={busy === "test"}>Test connection</Button></div>
        </>,
      ]} />
      <Result r={test} />
      <Nav back={back} next={next} skip={test?.ok ? undefined : next} />
    </div>
  );
}

function StreamerStep({ back, finish }: { back: () => void; finish: () => void }) {
  const toast = useToast();
  const [login, setLogin] = useState("");
  const [range, setRange] = useState("365");
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<string[]>([]);
  const add = async () => {
    setBusy(true);
    try {
      const s = await api.post<{ display_name: string }>("/api/streamers", { login, since_days: range ? Number(range) : null });
      setAdded((a) => [...a, s.display_name]);
      setLogin("");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <Header icon="people" title="Add a streamer">
        Whose clips do you edit? Their clips start appearing straight away, and the AI begins sorting them.
      </Header>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (login.trim()) add(); }}>
        <input value={login} onChange={(e) => setLogin(e.target.value)} placeholder="Twitch name or link" aria-label="Streamer name"
          className="flex-1 rounded-[12px] bg-bg3 px-3 py-2.5 text-[16px] outline-none placeholder:text-label3" />
        <Button kind="filled" busy={busy} disabled={!login.trim()}>Add</Button>
      </form>
      <div className="mt-3 flex items-center gap-3">
        <span className="text-[13px] text-label2">Fetch clips from</span>
        <div className="flex-1"><Segmented size="sm" value={range} onChange={setRange} options={[{ value: "30", label: "30 days" }, { value: "365", label: "1 year" }, { value: "", label: "All time" }]} /></div>
      </div>
      {added.length > 0 && <Result r={{ ok: true, message: `Added ${added.join(", ")}. Clips are on their way.` }} />}
      <Nav back={back} next={finish} nextLabel={added.length ? "Finish" : "Finish without a streamer"} />
    </div>
  );
}

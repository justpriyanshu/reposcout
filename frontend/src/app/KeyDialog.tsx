// The API key dialog (spec §6.8).
import { useMutation } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input, InputGroup } from "@/components/ui/input";
import { api, friendlyError } from "@/lib/api";
import { toast } from "@/lib/toast";
import { closeKeyDialog, settings, useConfig, useKeyDialog, type KeyDialogState } from "./settings";

const PLACEHOLDER = "Check your key to load models";
interface Option { value: string; label: string }
interface Models { models: string[]; default: string }

export function KeyDialog() {
  const state = useKeyDialog();
  const input = useRef<HTMLInputElement>(null);
  return (
    <Dialog open={state.open} onOpenChange={(open) => { if (!open) closeKeyDialog(); }}>
      <DialogContent id="keyDialog"
        onOpenAutoFocus={(e) => { e.preventDefault(); input.current?.focus(); }}
        // Like a native <dialog>: focus returns to what had it before (the dialog has no Radix trigger).
        onCloseAutoFocus={(e) => { e.preventDefault(); if (state.opener?.isConnected) state.opener.focus(); }}>
        <KeyForm key={state.seq} state={state} input={input} />
      </DialogContent>
    </Dialog>
  );
}

function KeyForm({ state, input }: { state: KeyDialogState; input: React.RefObject<HTMLInputElement | null> }) {
  const config = useConfig();
  const [hadKey] = useState(() => !!settings.key);
  const [key, setKey] = useState(() => settings.key);
  const [remember, setRemember] = useState(() => settings.remember);
  const [options, setOptions] = useState<Option[]>(() => [{ value: settings.model || "", label: settings.model || PLACEHOLDER }]);
  const [model, setModel] = useState(() => settings.model || "");
  const [msg, setMsg] = useState<{ text: string; tone: "" | "ok" | "err" }>(() => ({ text: state.message, tone: state.message ? "err" : "" }));
  const [shown, setShown] = useState(false);
  const check = useMutation({ mutationFn: (k: string) => api<Models>("/api/models", { method: "POST", key: k }) });

  /** Load the models this key can use; resolves the chosen model, or null when the key failed. */
  async function loadModels(): Promise<string | null> {
    const k = key.trim();
    if (!k) { setMsg({ text: "Paste your key first.", tone: "err" }); input.current?.focus(); return null; }
    setMsg({ text: "Checking…", tone: "" });
    try {
      const data = await check.mutateAsync(k);
      const want = data.models.includes(settings.model) ? settings.model : data.models.includes(data.default) ? data.default : data.models[0] || "";
      setOptions(data.models.map((m) => ({ value: m, label: m })));
      setModel(want);
      setMsg({ text: `Key works. ${data.models.length} models available.`, tone: "ok" });
      return want;
    } catch (err) {
      setMsg({ text: friendlyError(err), tone: "err" });
      return null;
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault(); // Save is the form's submit button, so Enter in the key field saves.
    const k = key.trim();
    let chosen = model;
    if (k && !chosen) {
      const loaded = await loadModels();
      if (loaded === null) return;
      chosen = loaded;
    }
    const next = state.then;
    settings.save(k, chosen, remember);
    closeKeyDialog();
    if (k) toast("Key saved.", { kind: "success" });
    if (k && next) next();
  }

  function remove() {
    settings.save("", "", false);
    closeKeyDialog();
    toast("Key removed from this browser.");
  }

  return (
    <form id="keyForm" noValidate onSubmit={save}>
      <DialogTitle>Your {config.provider} API key</DialogTitle>
      <DialogDescription>RepoScout uses your own free key to answer questions and review code.</DialogDescription>
      <div className="field">
        <label htmlFor="keyInput">{config.provider} API key</label>
        <InputGroup>
          <Input id="keyInput" ref={input} className="mono" type={shown ? "text" : "password"} autoComplete="off" spellCheck={false} placeholder="gsk_…"
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              // A newly pasted key invalidates the models loaded for the previous one.
              setOptions([{ value: "", label: PLACEHOLDER }]); setModel(""); setMsg({ text: "", tone: "" });
            }} />
          <Button variant="quiet" id="toggleKey" aria-label={shown ? "Hide key" : "Show key"} onClick={() => setShown(!shown)}>{shown ? "Hide" : "Show"}</Button>
        </InputGroup>
        <span className="help">Don't have one? <a id="keyLink" href={config.key_url} target="_blank" rel="noopener">Create a free key</a> (takes a minute).</span>
      </div>
      <div className="field">
        <label htmlFor="modelSelect">Model</label>
        <div className="field-row">
          <InputGroup>
            <select id="modelSelect" value={model} onChange={(e) => setModel(e.target.value)}>
              {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </InputGroup>
          <Button id="checkKey" disabled={check.isPending} onClick={() => void loadModels()}>Check key</Button>
        </div>
        <span className={msg.tone ? `field-msg ${msg.tone}` : "field-msg"} id="keyMsg" aria-live="polite">{msg.text}</span>
      </div>
      <label className="check">
        <input type="checkbox" id="rememberKey" checked={remember} onChange={(e) => setRemember(e.target.checked)} />{" "}
        <span>Remember on this device. Leave unticked on shared computers; the key is then forgotten when you close the tab.</span>
      </label>
      <p className="banner">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
        </svg>
        <span>Your key never gets saved on the server. It's sent over HTTPS with each request you make and used only for that request.</span>
      </p>
      <div className="act">
        <Button variant="quiet" className="start" id="forgetKey" hidden={!hadKey} onClick={remove}>Remove key</Button>
        <Button id="cancelKey" onClick={closeKeyDialog}>Cancel</Button>
        <Button variant="primary" type="submit" id="saveKey">Save</Button>
      </div>
    </form>
  );
}

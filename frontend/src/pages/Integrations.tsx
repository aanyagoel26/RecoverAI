import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Copy, Eye, EyeOff, KeyRound, Plug, RefreshCw, RotateCcw, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { Badge, Card, Confirm, Empty, ErrorBox, Loading, Notice, PageHeader, Spinner, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ago, titleCase } from "../lib/format";
import { NewCheckoutButton } from "./Payments";

const FIELD_LABEL: Record<string, string> = {
  client_id: "Client ID", client_secret: "Secret", webhook_id: "Webhook ID", api_key: "API key", model: "Model", base_url: "Base URL",
  from_email: "From address", account_sid: "Account SID", auth_token: "Auth token", from_number: "From number (E.164)",
};
const FIELD_HINT: Record<string, string> = {
  client_id: "PayPal Developer Dashboard → Apps & Credentials → your app", client_secret: "Shown under the Client ID (click Show)",
  webhook_id: "Your app → Webhooks → the webhook you added (copy its ID)", model: "Leave empty for the default",
  base_url: "Only for OpenAI-compatible providers; default https://api.openai.com/v1", from_email: 'e.g. "Store <billing@yourdomain.com>" (verified domain in Resend)',
  from_number: "A Twilio number you own, e.g. +15551234567",
};

function StatusPill({ p }: { p: any }) {
  if (p.status === "CONNECTED") return <Badge tone="green"><CheckCircle2 className="h-3 w-3" /> Connected</Badge>;
  if (p.status === "ERROR") return <Badge tone="red"><XCircle className="h-3 w-3" /> Error</Badge>;
  if (p.saved) return <Badge tone="amber">Saved · not tested</Badge>;
  if (p.env_fallback) return <Badge tone="blue">Using server env vars</Badge>;
  return <Badge tone="gray">Not connected</Badge>;
}

export function CredentialForm({ provider, p, testTo, locked }: { provider: string; p: any; testTo?: boolean; locked?: boolean }) {
  const qc = useQueryClient();
  const [vals, setVals] = useState<Record<string, string>>({});
  const [show, setShow] = useState<Record<string, boolean>>({});
  const [to, setTo] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const save = useMutation({ mutationFn: () => api(`/api/integrations/${provider}`, { method: "PUT", body: { values: vals } }),
    onSuccess: () => { setVals({}); qc.invalidateQueries(); } });
  const test = useMutation({ mutationFn: () => api(`/api/integrations/${provider}/test`, { body: to ? { to } : {} }), onSuccess: () => qc.invalidateQueries() });
  const del = useMutation({ mutationFn: () => api(`/api/integrations/${provider}`, { method: "DELETE" }), onSuccess: () => { setConfirmDel(false); qc.invalidateQueries(); } });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-semibold text-white">{p.label}</span><StatusPill p={p} /></div>
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {p.fields.map((f: string) => {
          const secret = p.secret_fields.includes(f);
          return (
            <label key={f} className="block">
              <span className="label">{FIELD_LABEL[f] ?? f}{p.required.includes(f) ? " *" : ""}</span>
              <div className="relative mt-1">
                <input className="input pr-9 font-mono text-xs" disabled={locked} type={secret && !show[f] ? "password" : "text"} autoComplete="off" spellCheck={false}
                  placeholder={p.masked?.[f] ? `Saved: ${p.masked[f]} — leave empty to keep` : ""} value={vals[f] ?? ""}
                  onChange={(e) => setVals({ ...vals, [f]: e.target.value })} aria-label={FIELD_LABEL[f] ?? f} />
                {secret && <button type="button" className="absolute right-2 top-2 text-ink-400" onClick={() => setShow({ ...show, [f]: !show[f] })} aria-label="Show or hide">
                  {show[f] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button>}
              </div>
              {FIELD_HINT[f] && <span className="mt-1 block text-[11px] text-ink-500">{FIELD_HINT[f]}</span>}
            </label>);
        })}
        <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
          <button className="btn-primary btn-sm" disabled={locked || save.isPending || !Object.values(vals).some((v) => v.trim())}>{save.isPending ? <Spinner /> : <KeyRound className="h-3.5 w-3.5" />} Save securely</button>
          {testTo && !locked && <input className="input w-56 py-1.5 text-xs" placeholder="Send test to (optional)" value={to} onChange={(e) => setTo(e.target.value)} />}
          <button type="button" className="btn-ghost btn-sm" onClick={() => test.mutate()} disabled={test.isPending || (!p.saved && !p.env_fallback)}>{test.isPending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />} Test connection</button>
          {p.saved && !locked && <button type="button" className="btn-danger btn-sm" onClick={() => setConfirmDel(true)}><Trash2 className="h-3.5 w-3.5" /> Remove</button>}
          {p.last_tested_at && <span className="text-[11px] text-ink-500">Last tested {ago(p.last_tested_at)}</span>}
        </div>
      </form>
      <ErrorBox error={save.error || test.error || del.error} />
      {test.data && <Notice tone={test.data.ok ? "green" : "amber"}>{test.data.message}</Notice>}
      {p.status === "ERROR" && p.last_error && !test.data && <Notice tone="amber">{p.last_error}</Notice>}
      <Confirm open={confirmDel} title={`Remove ${p.label} credentials?`} body="RecoverAI will stop using these credentials immediately. You can add them again any time."
        confirmLabel="Remove" danger busy={del.isPending} onConfirm={() => del.mutate()} onCancel={() => setConfirmDel(false)} />
    </div>
  );
}

export function Integrations() {
  const qc = useQueryClient();
  const { me } = useAuth();
  const [tab, setTab] = useState("paypal");
  const [liveConfirm, setLiveConfirm] = useState(false);
  const data = useQuery({ queryKey: ["integrations"], queryFn: () => api("/api/integrations") });
  const st = useQuery({ queryKey: ["paypal-status"], queryFn: () => api("/api/paypal/status"), refetchInterval: 10000 });
  const wh = useQuery({ queryKey: ["webhooks"], queryFn: () => api("/api/webhooks/events"), refetchInterval: 8000, enabled: tab === "paypal" });
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api("/api/integrations/ai-usage"), enabled: tab === "ai" });
  const env = useMutation({ mutationFn: (environment: string) => api("/api/paypal/environment", { body: { environment, confirm_live: environment === "live" } }),
    onSuccess: () => { setLiveConfirm(false); qc.invalidateQueries(); } });
  const aiSel = useMutation({ mutationFn: (ai_provider: string) => api("/api/integrations-ai-provider", { method: "PUT", body: { ai_provider } }), onSuccess: () => qc.invalidateQueries() });
  const replay = useMutation({ mutationFn: (event_id: string) => api("/api/demo/replay-webhook", { body: { event_id } }) });
  if (data.isLoading || !data.data) return <Loading />;
  const d = data.data;
  const pv = d.providers;
  const locked = !!me?.merchant.is_demo;
  const s = st.data;
  const step = (ok: boolean, text: string) => <li className="flex items-center gap-2 text-sm">{ok ? <CheckCircle2 className="h-4 w-4 text-mint-400" /> : <span className="h-4 w-4 rounded-full border border-ink-500" />}<span className={ok ? "text-ink-200" : "text-ink-300"}>{text}</span></li>;

  return (
    <div>
      <PageHeader title="Integrations" sub="Connect PayPal, an AI provider, email and SMS. Secrets are encrypted at rest and never shown again after saving."
        actions={<NewCheckoutButton />} />
      {locked && <div className="mb-4"><Notice tone="violet">This is the shared demo workspace, so its integrations are read-only. <Link to="/signup" className="font-semibold underline">Create your own account</Link> to connect your PayPal Sandbox, AI provider, email and SMS.</Notice></div>}
      <div className="mb-4 overflow-x-auto"><Tabs value={tab} onChange={setTab} items={[{ value: "paypal", label: "PayPal" }, { value: "ai", label: "AI provider" }, { value: "email", label: "Email" }, { value: "sms", label: "SMS" }]} /></div>

      {tab === "paypal" && (
        <div className="grid gap-4 xl:grid-cols-3 [&>*]:min-w-0">
          <Card title={<span className="flex items-center gap-2"><Plug className="h-4 w-4 text-volt-400" /> Setup checklist</span>}>
            <div className="space-y-4 p-5">
              <ul className="space-y-2">
                {step(!!s?.credentials_configured, `${s?.environment === "live" ? "Live" : "Sandbox"} credentials saved`)}
                {step(s?.connection_status === "CONNECTED", "Connection tested")}
                {step(!!s?.webhook_id_configured, "Webhook ID saved")}
                {step(!!s?.last_webhook_at, "First webhook received")}
              </ul>
              <div>
                <div className="label mb-1.5">Active environment</div>
                <Tabs value={s?.environment ?? "sandbox"} onChange={(v) => (locked ? undefined : v === "live" ? setLiveConfirm(true) : env.mutate("sandbox"))}
                  items={[{ value: "sandbox", label: "Sandbox" }, { value: "live", label: "Live" }]} />
                <div className="mt-1.5"><ErrorBox error={env.error} /></div>
                <p className="mt-1.5 text-[11px] text-ink-500">Sandbox and Live credentials are stored separately and never mixed. Recovery records are labelled with the environment they came from.</p>
              </div>
              <div>
                <div className="label mb-1.5">Your webhook URL</div>
                <div className="flex gap-2"><code className="input min-w-0 flex-1 truncate py-1.5 font-mono text-[11px]">{d.webhook_url}</code>
                  <button className="btn-ghost btn-sm" onClick={() => navigator.clipboard?.writeText(d.webhook_url)} aria-label="Copy webhook URL"><Copy className="h-3.5 w-3.5" /></button></div>
                <p className="mt-1.5 text-[11px] text-ink-500">Add it in your PayPal app → Webhooks, subscribe to PAYMENT.CAPTURE.* , CHECKOUT.ORDER.APPROVED and INVOICING.INVOICE.PAID, then paste the Webhook ID here. It must be a public HTTPS URL (deploy first, or use a tunnel locally).</p>
              </div>
            </div>
          </Card>
          <Card className="xl:col-span-2" title="Credentials">
            <div className="space-y-8 p-5">
              <CredentialForm provider="paypal_sandbox" p={pv.paypal_sandbox} locked={locked} />
              <div className="border-t border-ink-800 pt-6"><CredentialForm provider="paypal_live" p={pv.paypal_live} locked={locked} /></div>
            </div>
          </Card>
          <Card className="xl:col-span-3" title="Recent webhook events" sub="Stored, de-duplicated by PayPal event ID, verified, then applied once">
            {wh.isLoading ? <Loading /> : !wh.data?.items?.length ? <Empty title="No webhook events yet" body="Events appear here once PayPal (or the labelled demo simulator) sends them." /> : (
              <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-xs"><tbody className="divide-y divide-ink-800">{wh.data.items.map((w: any) => (
                <tr key={w.id}><td className="px-5 py-2.5 font-mono text-ink-100">{w.event_type}</td>
                  <td className="px-2"><Badge tone={w.is_simulated ? "violet" : w.verification_status === "SUCCESS" ? "green" : "red"}>{w.is_simulated ? "DEMO" : `Verify: ${w.verification_status}`}</Badge></td>
                  <td className="px-2"><Badge tone={w.processing_status === "PROCESSED" ? "green" : "gray"}>{titleCase(w.processing_status)}</Badge></td>
                  <td className="px-2 text-ink-500">{w.processing_error ?? ""}</td><td className="px-2 text-ink-500">{ago(w.received_at)}</td>
                  <td className="px-5 text-right">{w.is_simulated && <button className="btn-ghost btn-sm" onClick={() => replay.mutate(w.paypal_event_id)}><RotateCcw className="h-3 w-3" /> Replay</button>}</td>
                </tr>))}</tbody></table></div>)}
            {replay.data && <div className="p-4"><Notice tone="green">Re-delivered event acknowledged as <b>{replay.data.status}</b> — nothing was applied twice.</Notice></div>}
          </Card>
        </div>)}

      {tab === "ai" && (
        <div className="grid gap-4 xl:grid-cols-3 [&>*]:min-w-0">
          <Card title="Provider" sub={d.active_ai ? `Active: ${d.active_ai.provider} · ${d.active_ai.model} (${d.active_ai.source})` : "No AI provider active — RecoverAI uses its transparent rules engine"}>
            <div className="space-y-3 p-5">
               <div className="overflow-x-auto">                
                 <Tabs value={d.ai_provider} onChange={(v) => !locked && aiSel.mutate(v)}                  
                   items={[{ value: "auto", label: "Auto" }, { value: "gemini", label: "Gemini" },                    
                           { value: "anthropic", label: "Anthropic" }, { value: "openai", label: "OpenAI" },                    
                           { value: "none", label: "Off" }]} />              
               </div>              
              <p className="text-xs text-ink-400"><b>Auto</b> uses whichever key you saved                
                (Anthropic, then OpenAI, then Gemini). Pick <b>Gemini</b> to always use your free key.</p>
              <ErrorBox error={aiSel.error} />
              <p className="text-xs text-ink-400">AI writes explanations, messages and Copilot answers. It never decides payment state, recovery eligibility or amounts — the Trust Firewall does. If the provider fails, screens show a labelled rules-based fallback.</p>
            </div>
          </Card>
          <Card className="xl:col-span-2" title="Credentials">
           <div className="space-y-8 p-5">              
             <div className="space-y-3">                
               <Notice tone="green">                  
                 <b>Free option:</b> get a Gemini API key at no cost from{" "}                  
                 <a className="underline" href="https://aistudio.google.com/apikey" target="_blank"                    
                   rel="noreferrer">Google AI Studio - Get API key</a> (no card needed). Leave                  
                 <i> Model</i> empty for the default. Free-tier keys have per-minute and per-day                  
                 limits; when one is reached RecoverAI shows labelled rules-based text instead.                
               </Notice>                
               <CredentialForm provider="gemini" p={pv.gemini} locked={locked} />              
             </div>              
             <div className="border-t border-ink-800 pt-6">                
               <CredentialForm provider="anthropic" p={pv.anthropic} locked={locked} />              
             </div>              
             <div className="border-t border-ink-800 pt-6">                
               <CredentialForm provider="openai" p={pv.openai} locked={locked} />              
             </div>            
           </div>
          </Card>
          <Card className="xl:col-span-3" title="AI usage" sub="Every call is recorded without prompts, outputs or keys">
            {!usage.data ? <Loading /> : (<>
              <div className="grid grid-cols-2 gap-px bg-ink-800 sm:grid-cols-5">
                {[["Calls", usage.data.summary.calls], ["Failed", usage.data.summary.failed], ["Input tokens", usage.data.summary.input_tokens], ["Output tokens", usage.data.summary.output_tokens], ["Avg latency", `${usage.data.summary.avg_latency_ms} ms`]].map(([l, v]) =>
                  <div key={l as string} className="bg-ink-900 px-5 py-3"><div className="text-[11px] text-ink-400">{l}</div><div className="num text-lg text-white">{v as any}</div></div>)}
              </div>
              {!usage.data.items.length ? <Empty title="No AI calls yet" /> : (
                <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-xs"><tbody className="divide-y divide-ink-800">{usage.data.items.map((u: any, i: number) => (
                  <tr key={i}><td className="px-5 py-2 text-ink-300">{ago(u.created_at)}</td><td className="px-2 text-ink-100">{u.purpose}</td><td className="px-2 font-mono text-ink-300">{u.provider}:{u.model}</td>
                    <td className="px-2"><Badge tone={u.success ? "green" : "red"}>{u.success ? "OK" : "Failed"}</Badge></td><td className="num px-2 text-ink-300">{u.latency_ms} ms</td>
                    <td className="px-5 text-ink-500">{u.error ?? ""}</td></tr>))}</tbody></table></div>)}
            </>)}
          </Card>
        </div>)}

      {tab === "email" && (
        <Card title="Email (Resend)" sub="Used for customer recovery messages, email verification and password resets">
          <div className="space-y-4 p-5">
            <CredentialForm provider="resend" p={pv.resend} testTo locked={locked} />
            <Notice>Without an email provider, recovery messages are generated and stored as <b>Not sent · preview only</b>. "Sent" means Resend accepted the message; RecoverAI never claims delivery it can't confirm.</Notice>
          </div>
        </Card>)}

      {tab === "sms" && (
        <Card title="SMS (Twilio)" sub="Optional second channel for customers with a phone number and consent">
          <div className="space-y-4 p-5">
            <CredentialForm provider="twilio" p={pv.twilio} testTo locked={locked} />
            <Notice>Turn SMS on in Settings → Recovery policy → Channels. Delivery is confirmed by Twilio's signed status callback at <code className="font-mono">{d.twilio_callback_url}</code>.</Notice>
          </div>
        </Card>)}
      <p className="mt-4 text-[11px] text-ink-500">Encryption: {d.encryption}</p>

      <Confirm open={liveConfirm} title="Switch to PayPal Live?" danger confirmLabel="Switch to Live" busy={env.isPending}
        body={<>Live mode uses real money. Make sure your Live credentials and Live webhook are saved, and keep the automation amount limit conservative. The Failure Lab never runs against Live.</>}
        onConfirm={() => env.mutate("live")} onCancel={() => setLiveConfirm(false)} />
    </div>
  );
}

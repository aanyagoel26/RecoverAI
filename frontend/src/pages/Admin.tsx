import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, LogOut, RotateCcw, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Card, Confirm, Empty, ErrorBox, Loading, Notice, PageHeader, Spinner, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, dateTime } from "../lib/format";
const AUTO_ACTIONS: [string, string][] = [
  ["SEND_REMINDER", "Send payment reminders"], ["REQUEST_CUSTOMER_ACTION", "Ask customer to finish a pending PayPal step"],
  ["RECOMMEND_ALTERNATIVE_METHOD", "Suggest another PayPal funding source"], ["SEND_PAYPAL_INVOICE", "Send a PayPal invoice"],
  ["RETRY_CAPTURE", "Retry capture of a buyer-approved order (after reconciliation)"],
];
const NOTIFY: [string, string][] = [
  ["recovered", "A payment is recovered"], ["approval", "An action needs my approval"], ["blocked", "The Trust Firewall blocks an action"],
  ["escalation", "A case is escalated"], ["integration", "An integration fails"], ["duplicate", "A possible duplicate payment is detected"],
];
const TZ = ["Asia/Kolkata", "UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Singapore", "Asia/Dubai", "Australia/Sydney"];

function Field({ label, help, children }: { label: string; help?: string; children: React.ReactNode }) {
  return <label className="block"><span className="label">{label}</span><div className="mt-1">{children}</div>{help && <span className="mt-1 block text-[11px] text-ink-500">{help}</span>}</label>;
}
function SaveBar({ m, label = "Save changes" }: { m: any; label?: string }) {
  return (<div className="border-t border-ink-800 px-5 py-3">
    <div className="flex items-center gap-3"><button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>{m.isPending && <Spinner />} {label}</button>
      {m.isSuccess && <span className="text-xs text-mint-300">Saved</span>}</div>
    <div className="mt-2"><ErrorBox error={m.error} /></div></div>);
}

function BusinessTab() {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const sq = useQuery({ queryKey: ["settings"], queryFn: () => api("/api/merchant/settings") });
  const [f, setF] = useState({ name: me?.merchant.name ?? "", website: me?.merchant.website ?? "", industry: me?.merchant.industry ?? "", business_type: me?.merchant.business_type ?? "",
    timezone: me?.merchant.timezone ?? "Asia/Kolkata", default_currency: me?.merchant.default_currency ?? "USD", full_name: me?.user.full_name ?? "" });
  const [b, setB] = useState<any>(null);
  useEffect(() => { if (sq.data && !b) setB({ brand_name: sq.data.brand_name ?? "", support_email: sq.data.support_email ?? "", default_tone: sq.data.default_tone }); }, [sq.data, b]);
  const save = useMutation({ mutationFn: async () => {
    await api("/api/merchant/profile", { method: "PUT", body: { ...f, website: f.website || null, industry: f.industry || null, business_type: f.business_type || null } });
    if (b) await api("/api/merchant/settings", { method: "PUT", body: { brand_name: b.brand_name || null, support_email: b.support_email || null, default_tone: b.default_tone } });
  }, onSuccess: () => { refresh(); qc.invalidateQueries({ queryKey: ["settings"] }); } });
  const inp = (k: keyof typeof f) => <input className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />;
  return (
    <Card title="Business profile" sub="Used in customer messages, invoices and time-based rules">
      <div className="grid gap-4 p-5 sm:grid-cols-2">
        <Field label="Business name">{inp("name")}</Field>
        <Field label="Website">{inp("website")}</Field>
        <Field label="Business type">
          <select className="input" value={f.business_type} onChange={(e) => setF({ ...f, business_type: e.target.value })}>
            <option value="">Select…</option>{["E-commerce", "SaaS / subscriptions", "Digital goods", "Services", "Education", "Marketplace", "Other"].map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label="Industry">{inp("industry")}</Field>
        <Field label="Time zone" help="Quiet hours and schedules use this zone">
          <select className="input" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })}>{[...new Set([f.timezone, ...TZ])].map((z) => <option key={z}>{z}</option>)}</select></Field>
        <Field label="Default currency"><select className="input" value={f.default_currency} onChange={(e) => setF({ ...f, default_currency: e.target.value })}>{["USD", "EUR", "GBP", "AUD", "CAD", "JPY"].map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Your name">{inp("full_name")}</Field>
        <div><span className="label">Sign-in email</span><div className="mt-2 text-sm text-ink-200">{me?.user.email} <Badge tone={me?.user.email_verified ? "green" : "amber"}>{me?.user.email_verified ? "Verified" : "Not verified"}</Badge></div></div>
        {b && <>
          <Field label="Brand name in messages"><input className="input" value={b.brand_name} onChange={(e) => setB({ ...b, brand_name: e.target.value })} /></Field>
          <Field label="Support email in messages"><input className="input" type="email" value={b.support_email} onChange={(e) => setB({ ...b, support_email: e.target.value })} /></Field>
          <Field label="Default message tone"><select className="input" value={b.default_tone} onChange={(e) => setB({ ...b, default_tone: e.target.value })}><option value="professional">Professional</option><option value="friendly">Friendly</option><option value="urgent">Urgent but polite</option></select></Field>
        </>}
      </div>
      <SaveBar m={save} />
    </Card>);
}

function PolicyTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api("/api/merchant/settings") });
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (q.data) setF({ ...q.data, intervals: q.data.reminder_intervals_minutes.join(", "), auto_amount: (q.data.max_auto_amount_minor / 100).toFixed(2), quiet: q.data.quiet_hours_start != null }); }, [q.data]);
  const save = useMutation({
    mutationFn: () => {
      const amt = String(f.auto_amount).trim();
      if (!/^\d+(\.\d{1,2})?$/.test(amt)) throw new Error("Automation amount limit must be a number with at most 2 decimals");
      const [whole, frac = ""] = amt.split(".");
      return api("/api/merchant/settings", { method: "PUT", body: {
        recovery_mode: f.recovery_mode,
        reminder_intervals_minutes: String(f.intervals).split(",").map((x: string) => Number(x.trim())).filter((n: number) => n > 0),
        max_reminders_per_case: Number(f.max_reminders_per_case), min_minutes_between_messages: Number(f.min_minutes_between_messages),
        abandonment_threshold_minutes: Number(f.abandonment_threshold_minutes), pending_alert_after_minutes: Number(f.pending_alert_after_minutes),
        max_retry_attempts: Number(f.max_retry_attempts), retry_cooldown_minutes: Number(f.retry_cooldown_minutes),
        max_auto_amount_minor: Number(whole) * 100 + Number((frac + "00").slice(0, 2)), permitted_auto_actions: f.permitted_auto_actions,
        escalate_after_hours: Number(f.escalate_after_hours), channels: f.channels,
        quiet_hours_start: f.quiet ? Number(f.quiet_hours_start ?? 21) : null, quiet_hours_end: f.quiet ? Number(f.quiet_hours_end ?? 8) : null,
      } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  if (!f) return <Loading />;
  const num = (k: string, label: string, help?: string) => <Field label={label} help={help}><input className="input" type="number" value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>;
  const toggle = (list: string, v: string) => setF({ ...f, [list]: f[list].includes(v) ? f[list].filter((x: string) => x !== v) : [...f[list], v] });
  return (
    <div className="space-y-4">
      <Card title="Recovery mode" sub="How much the agent may do on its own">
        <div className="grid gap-3 p-5 sm:grid-cols-3">
          {([["AUTOMATIC", "Automatic", "Allowed actions run on their own, inside every limit below."], ["APPROVAL", "Approval", "The agent proposes; a person approves each action."], ["MANUAL", "Manual", "The agent analyses only. You act yourself."]] as const).map(([v, l, d]) => (
            <button key={v} type="button" onClick={() => setF({ ...f, recovery_mode: v })} aria-pressed={f.recovery_mode === v}
              className={`rounded-xl border p-4 text-left transition ${f.recovery_mode === v ? "border-volt-400 bg-volt-500/10" : "border-ink-700 hover:border-ink-500"}`}>
              <div className="text-sm font-semibold text-white">{l}</div><div className="mt-1 text-xs text-ink-400">{d}</div></button>))}
        </div>
      </Card>
      <Card title="Automation limits" sub="Enforced by the Trust Firewall on every action, in every mode">
        <div className="grid gap-4 p-5 sm:grid-cols-2">
          <Field label="Max amount for automatic actions" help="Larger payments always need approval"><input className="input" inputMode="decimal" value={f.auto_amount} onChange={(e) => setF({ ...f, auto_amount: e.target.value })} /></Field>
          {num("escalate_after_hours", "Escalate unresolved cases after (hours)")}
          <div className="sm:col-span-2"><span className="label">Actions allowed to run automatically</span>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">{AUTO_ACTIONS.map(([v, l]) => (
              <label key={v} className="flex items-center gap-2 text-sm text-ink-200"><input type="checkbox" className="accent-volt-500" checked={f.permitted_auto_actions.includes(v)} onChange={() => toggle("permitted_auto_actions", v)} />{l}</label>))}</div></div>
        </div>
      </Card>
      <Card title="Messages and timing">
        <div className="grid gap-4 p-5 sm:grid-cols-2">
          <Field label="Reminder schedule (minutes)" help="Comma separated, ascending. 30, 1440, 2880 = 30 min, 24 h, 48 h"><input className="input" value={f.intervals} onChange={(e) => setF({ ...f, intervals: e.target.value })} /></Field>
          {num("max_reminders_per_case", "Max messages per case", "0–5")}
          {num("min_minutes_between_messages", "Minimum minutes between messages")}
          {num("abandonment_threshold_minutes", "Checkout abandoned after (minutes inactive)")}
          {num("pending_alert_after_minutes", "Alert on pending payment after (minutes)")}
          <div><span className="label">Channels</span><div className="mt-2 flex gap-4">{["email", "sms"].map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm text-ink-200"><input type="checkbox" className="accent-volt-500" checked={f.channels.includes(c)} onChange={() => toggle("channels", c)} />{c === "sms" ? "SMS (Twilio)" : "Email"}</label>))}</div>
            <span className="mt-1 block text-[11px] text-ink-500">SMS is used only for customers with a phone number.</span></div>
          <div className="sm:col-span-2"><label className="flex items-center gap-2 text-sm text-ink-200"><input type="checkbox" className="accent-volt-500" checked={f.quiet} onChange={(e) => setF({ ...f, quiet: e.target.checked })} /> Quiet hours — never message customers between</label>
            {f.quiet && <div className="mt-2 flex items-center gap-2 text-sm text-ink-300">
              <select className="input w-24" value={f.quiet_hours_start ?? 21} onChange={(e) => setF({ ...f, quiet_hours_start: e.target.value })}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select> and
              <select className="input w-24" value={f.quiet_hours_end ?? 8} onChange={(e) => setF({ ...f, quiet_hours_end: e.target.value })}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select>
              <span className="text-xs text-ink-500">(business time zone)</span></div>}</div>
        </div>
      </Card>
      <Card title="Safe payment retry" sub="RecoverAI always reconciles with PayPal first and reuses the same idempotency key">
        <div className="grid gap-4 p-5 sm:grid-cols-2">
          {num("max_retry_attempts", "Max capture retries per payment", "0–5. 0 disables retries")}
          {num("retry_cooldown_minutes", "Cooldown between retries (minutes)")}
        </div>
        <SaveBar m={save} label="Save policy" />
      </Card>
    </div>);
}

function NotifyTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api("/api/merchant/settings") });
  const [on, setOn] = useState<string[] | null>(null);
  useEffect(() => { if (q.data && on === null) setOn(q.data.notify_on); }, [q.data, on]);
  const save = useMutation({ mutationFn: () => api("/api/merchant/settings", { method: "PUT", body: { notify_on: on } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  if (!on) return <Loading />;
  return (
    <Card title="Notify me when" sub="Shown in the Notification Centre (bell icon)">
      <div className="space-y-2.5 p-5">{NOTIFY.map(([v, l]) => (
        <label key={v} className="flex items-center gap-2 text-sm text-ink-200"><input type="checkbox" className="accent-volt-500" checked={on.includes(v)} onChange={() => setOn(on.includes(v) ? on.filter((x) => x !== v) : [...on, v])} />{l}</label>))}</div>
      <SaveBar m={save} />
    </Card>);
}

function SecurityTab() {
  const { me, signOut } = useAuth();
  const [p, setP] = useState({ current_password: "", new_password: "", confirm: "" });
  const [ask, setAsk] = useState(false);
  const change = useMutation({ mutationFn: () => {
    if (p.new_password !== p.confirm) throw new Error("New passwords don't match");
    return api("/api/auth/change-password", { body: { current_password: p.current_password, new_password: p.new_password } });
  }, onSuccess: () => setP({ current_password: "", new_password: "", confirm: "" }) });
  const all = useMutation({ mutationFn: () => api("/api/auth/logout-all", { method: "POST" }), onSuccess: () => signOut() });
  const demo = !!me?.merchant.is_demo;
  return (
    <div className="space-y-4">
      {demo && <Notice tone="amber">Password and session changes are disabled on the shared demo account.</Notice>}
      <Card title="Change password" sub="At least 10 characters">
        <form className="grid gap-4 p-5 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
          {([["current_password", "Current password", "current-password"], ["new_password", "New password", "new-password"], ["confirm", "Confirm new password", "new-password"]] as const).map(([k, l, ac]) => (
            <Field key={k} label={l}><input className="input" type="password" autoComplete={ac} value={p[k]} onChange={(e) => setP({ ...p, [k]: e.target.value })} disabled={demo} /></Field>))}
          <div className="sm:col-span-3 flex items-center gap-3"><button className="btn-primary" disabled={demo || change.isPending || !p.current_password || p.new_password.length < 10}>{change.isPending && <Spinner />} Update password</button>
            {change.isSuccess && <span className="text-xs text-mint-300">Password updated</span>}</div>
          <div className="sm:col-span-3"><ErrorBox error={change.error} /></div>
        </form>
      </Card>
      <Card title="Sessions">
        <div className="flex flex-wrap items-center justify-between gap-3 p-5">
          <p className="text-sm text-ink-300">Sign out of RecoverAI on every device, including this one.</p>
          <button className="btn-danger" onClick={() => setAsk(true)} disabled={demo}><LogOut className="h-4 w-4" /> Sign out everywhere</button>
        </div>
        <div className="px-5 pb-4"><ErrorBox error={all.error} /></div>
      </Card>
      <Confirm open={ask} title="Sign out everywhere?" body="Every active session, on every device, will be signed out immediately." confirmLabel="Sign out everywhere" danger busy={all.isPending}
        onConfirm={() => all.mutate()} onCancel={() => setAsk(false)} />
    </div>);
}

function DemoTab() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [ask, setAsk] = useState(false);
  const reset = useMutation({ mutationFn: () => api("/api/demo/reset", { method: "POST" }), onSuccess: () => { setAsk(false); qc.invalidateQueries(); nav("/dashboard"); } });
  return (
    <Card title="Demo workspace">
      <div className="space-y-3 p-5"><p className="text-sm text-ink-300">Restore the seeded demo data (customers, payment attempts, abandoned checkouts and recovery cases). Demo data is always labelled and never counted as verified revenue.</p>
        <button className="btn-danger" onClick={() => setAsk(true)}><RotateCcw className="h-4 w-4" /> Reset demo data</button><ErrorBox error={reset.error} /></div>
      <Confirm open={ask} title="Reset demo data?" body="All demo payments, cases and messages will be replaced with the original seeded data." confirmLabel="Reset" danger busy={reset.isPending}
        onConfirm={() => reset.mutate()} onCancel={() => setAsk(false)} />
    </Card>);
}

export function SettingsPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "business";
  const items = [{ value: "business", label: "Business" }, { value: "policy", label: "Recovery policy" }, { value: "notifications", label: "Notifications" }, { value: "security", label: "Security" },
    ...(me?.merchant.is_demo ? [{ value: "demo", label: "Demo" }] : [])];
  return (
    <div className="max-w-4xl">
      <PageHeader title="Settings" sub="Business details, the recovery policy the Trust Firewall enforces, notifications and account security." />
      <div className="mb-4 overflow-x-auto"><Tabs value={tab} onChange={(v) => setParams({ tab: v })} items={items} /></div>
      {tab === "business" && <BusinessTab />}
      {tab === "policy" && <PolicyTab />}
      {tab === "notifications" && <NotifyTab />}
      {tab === "security" && <SecurityTab />}
      {tab === "demo" && <DemoTab />}
    </div>);
}

export function Profile() { return <Navigate to="/settings?tab=business" replace />; }

export function Notifications() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["notifications"], queryFn: () => api("/api/notifications"), refetchInterval: 8000 });
  const read = useMutation({ mutationFn: (ids?: string[]) => api("/api/notifications/read", { body: { ids } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }) });
  const tone: Record<string, string> = { success: "bg-mint-400", warning: "bg-amberx-400", danger: "bg-rose-400", info: "bg-volt-400" };
  return (
    <div className="max-w-3xl">
      <PageHeader title="Notification Centre" actions={<button className="btn-ghost btn-sm" onClick={() => read.mutate(undefined)}>Mark all read</button>} />
      <Card>
        {q.isLoading ? <Loading /> : !q.data?.items?.length ? <Empty icon={<Bell className="h-5 w-5" />} title="You're all caught up" /> : (
          <ul className="divide-y divide-ink-800">{q.data.items.map((n: any) => (
            <li key={n.id}><button className={`row-hover flex w-full gap-3 px-5 py-3.5 text-left ${n.read ? "opacity-60" : ""}`} onClick={() => { read.mutate([n.id]); if (n.link) nav(n.link); }}>
              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${tone[n.kind] ?? "bg-ink-400"}`} />
              <span className="flex-1"><span className="block text-sm font-semibold text-white">{n.title}</span><span className="block text-xs text-ink-300">{n.body}</span></span>
              <span className="shrink-0 text-[11px] text-ink-500">{ago(n.created_at)}</span>
            </button></li>))}</ul>)}
      </Card>
    </div>
  );
}

export function Audit() {
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["audit", actor, q], queryFn: () => api(`/api/audit?limit=300${actor ? `&actor=${actor}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`), refetchInterval: 10000 });
  return (
    <div>
      <PageHeader title="Activity & audit log" sub="Immutable record of every agent decision, user approval, PayPal event and system action." />
      <div className="mb-4 flex flex-wrap gap-2">
        <Tabs value={actor} onChange={setActor} items={[{ value: "", label: "All" }, { value: "agent", label: "Agent" }, { value: "user", label: "Users" }, { value: "paypal", label: "PayPal" }, { value: "system", label: "System" }]} />
        <div className="relative w-full sm:w-72"><Search className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" /><input className="input pl-9" placeholder="Search log" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <Card>
        {list.isLoading ? <Loading /> : !list.data?.items?.length ? <Empty title="No log entries" /> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs">
            <thead><tr className="border-b border-ink-700 text-left">{["Time", "Actor", "Action", "Summary"].map((h) => <th key={h} className="label px-5 py-3">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-ink-800">{list.data.items.map((a: any) => (
              <tr key={a.id}><td className="whitespace-nowrap px-5 py-2.5 text-ink-400">{dateTime(a.created_at)}</td>
                <td className="px-5"><Badge tone={a.actor_type === "agent" ? "blue" : a.actor_type === "user" ? "green" : a.actor_type === "paypal" ? "amber" : "gray"}>{a.actor_type}</Badge></td>
                <td className="px-5 font-mono text-ink-300">{a.action}</td><td className="px-5 text-ink-100">{a.summary}</td></tr>))}</tbody></table></div>)}
      </Card>
    </div>
  );
}

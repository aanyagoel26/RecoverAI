import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ExternalLink, Mail, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AnalysisPanel } from "../components/Analysis";
import { Firewall, RailCard, RunHeader, TraceSteps } from "../components/Trace";
import { Badge, Card, CaseStatus, Category, Drawer, Empty, ErrorBox, Loading, Notice, PageHeader, Prob, SourceBadge, Spinner, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { actLabel, ago, dateTime, money, titleCase } from "../lib/format";

const CUSTOMER_FACING = ["SEND_REMINDER", "REQUEST_CUSTOMER_ACTION", "RECOMMEND_ALTERNATIVE_METHOD", "SEND_PAYPAL_INVOICE"];

export function DeliveryBadge({ s }: { s: string }) {
  const map: Record<string, [string, any, string]> = {
    SENT: ["Sent", "green", "Delivered to the email provider"], TEST_SENT: ["Test sent", "blue", "Sent to your own address"],
    PREVIEW_ONLY: ["Not sent · preview only", "amber", "No email provider configured — nothing was delivered"], FAILED: ["Failed", "red", "Provider rejected the message"],
    DRAFT: ["Draft", "gray", ""],
  };
  const [l, t, title] = map[s] ?? [s, "gray", ""];
  return <Badge tone={t} title={title}>{l}</Badge>;
}

function Composer({ caseId, action, consent, onDone }: { caseId: string; action: any; consent: boolean; onDone: () => void }) {
  const qc = useQueryClient();
  const [tone, setTone] = useState<"professional" | "friendly" | "urgent">(action?.payload?.tone ?? "friendly");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [edited, setEdited] = useState(false);
  const preview = useQuery({ queryKey: ["preview", caseId, tone], queryFn: () => api(`/api/recovery/${caseId}/preview-message`, { body: { tone } }), staleTime: 60000 });
  useEffect(() => { if (preview.data && !edited) { setSubject(preview.data.subject); setBody(preview.data.body); } }, [preview.data, edited]);
  const approve = useMutation({
    mutationFn: () => api(`/api/recovery/${caseId}/approve`, { body: { action_id: action.id, tone, ...(edited ? { subject, body } : {}) } }),
    onSuccess: () => { qc.invalidateQueries(); onDone(); },
  });
  const test = useMutation({ mutationFn: () => api("/api/communications/test", { body: { case_id: caseId, tone, subject, body } }) });
  const reject = useMutation({ mutationFn: () => api(`/api/recovery/actions/${action.id}/reject`, { method: "POST" }), onSuccess: () => qc.invalidateQueries() });

  return (
    <div className="rounded-xl border border-amberx-500/30 bg-ink-850 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-white">Approve: {actLabel(action.action_type)}</div>
        <Tabs value={tone} onChange={(v) => { setTone(v); setEdited(false); }} items={[{ value: "professional", label: "Professional" }, { value: "friendly", label: "Friendly" }, { value: "urgent", label: "Urgent but polite" }]} />
      </div>
      {!consent && <div className="mt-3"><Notice tone="amber">This customer has not consented to recovery messages. Approval will be blocked by the Trust Firewall.</Notice></div>}
      {action.action_type === "SEND_PAYPAL_INVOICE" && <div className="mt-3"><Notice>On approval RecoverAI creates and sends a <b>PayPal invoice</b> (Invoicing API v2) for the original amount. The link in this message becomes the PayPal invoice link, and PayPal also emails the invoice.</Notice></div>}
      {preview.isLoading ? <Loading label="Drafting message" /> : (
        <div className="mt-3 space-y-2">
          <input className="input font-semibold" value={subject} onChange={(e) => { setSubject(e.target.value); setEdited(true); }} aria-label="Subject" />
          <textarea className="input min-h-[220px] font-mono text-xs leading-relaxed" value={body} onChange={(e) => { setBody(e.target.value); setEdited(true); }} aria-label="Message body" />
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-400">
            <Badge tone={preview.data?.generated_by === "llm" ? "blue" : "gray"}>{preview.data?.generated_by === "llm" ? "Written by LLM, checked by rules" : "Template (no LLM key)"}</Badge>
            {preview.data?.recovery_link && <a href={preview.data.recovery_link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-volt-400 hover:text-volt-300"><ExternalLink className="h-3 w-3" /> Secure checkout link</a>}
            {edited && <span className="text-amberx-400">Edited — the secure link is re-added automatically if removed.</span>}
          </div>
        </div>)}
      <ErrorBox error={approve.error || test.error} />
      {test.data && <div className="mt-2"><Notice tone={test.data.delivery_status === "TEST_SENT" ? "green" : "amber"}>Test message: <b>{titleCase(test.data.delivery_status)}</b>{test.data.error ? ` — ${test.data.error}` : ""}</Notice></div>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn-success" onClick={() => approve.mutate()} disabled={approve.isPending || preview.isLoading}>{approve.isPending ? <Spinner /> : <Check className="h-4 w-4" />} Approve & send</button>
        <button className="btn-ghost" onClick={() => navigator.clipboard?.writeText(`${subject}\n\n${body}`)}><Copy className="h-4 w-4" /> Copy</button>
        <button className="btn-ghost" onClick={() => test.mutate()} disabled={test.isPending}>{test.isPending ? <Spinner /> : <Mail className="h-4 w-4" />} Send test to me</button>
        <button className="btn-danger" onClick={() => reject.mutate()} disabled={reject.isPending}><X className="h-4 w-4" /> Reject</button>
      </div>
      <p className="mt-2 flex items-center gap-1 text-[10px] text-ink-500"><ShieldCheck className="h-3 w-3" /> Backend re-checks consent, message limits, already-paid and pending status before sending.</p>
    </div>
  );
}

export function CaseDrawer({ caseId, onClose }: { caseId: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const d = useQuery({ queryKey: ["case", caseId], queryFn: () => api(`/api/recovery/cases/${caseId}`), enabled: !!caseId, refetchInterval: 4000 });
  const exec = useMutation({ mutationFn: (action_id: string) => api(`/api/recovery/${caseId}/execute`, { body: { action_id } }), onSuccess: () => qc.invalidateQueries() });
  const close = useMutation({ mutationFn: () => api(`/api/recovery/${caseId}/close`, { body: { outcome: "CLOSED_UNRECOVERED" } }), onSuccess: () => qc.invalidateQueries() });
  const reanalyse = useMutation({ mutationFn: () => api(`/api/recovery/${caseId}/reanalyse`, { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const sync = useMutation({ mutationFn: () => api(`/api/recovery/${caseId}/invoice/sync`, { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const c = d.data?.case;
  const proposed = c?.actions?.find((a: any) => a.status === "PROPOSED");
  const active = c && ["OPEN", "AWAITING_APPROVAL", "IN_PROGRESS"].includes(c.status);
  return (
    <Drawer open={!!caseId} onClose={onClose} width="max-w-3xl" title={c ? <span className="flex items-center gap-2">{c.customer.name} · {c.order_number} <CaseStatus s={c.status} /> <SourceBadge s={c.data_source} /></span> : "Recovery case"}>
      {d.isLoading || !c ? <Loading /> : (
        <div className="space-y-5 p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="card px-3 py-2"><div className="text-[11px] text-ink-400">Order value</div><div className="num text-lg text-white">{money(c.amount_minor, c.currency)}</div></div>
            <div className="card px-3 py-2"><div className="text-[11px] text-ink-400">Probability</div><div className="mt-1"><Prob p={c.recovery_probability} /></div></div>
            <div className="card px-3 py-2"><div className="text-[11px] text-ink-400">Messages</div><div className="num text-lg text-white">{c.reminders_sent}</div></div>
            <div className="card px-3 py-2"><div className="text-[11px] text-ink-400">Opened</div><div className="text-sm text-white">{ago(c.created_at)}</div></div>
          </div>
          {c.status === "RECOVERED" && <Notice tone="green">Recovered <b>{money(c.recovered_amount_minor, c.currency)}</b> {ago(c.recovered_at)}. All remaining reminders were stopped. <Link className="underline" to={`/payments/${c.recovered_payment_id}`}>View confirming payment</Link></Notice>}
          {c.priority_reason && active && <p className="text-xs text-ink-400"><span className="font-semibold text-ink-200">Why this priority:</span> {c.priority_reason}</p>}

          {proposed && active && (CUSTOMER_FACING.includes(proposed.action_type) ? <Composer key={proposed.id} caseId={c.id} action={proposed} consent={c.customer.consent} onDone={() => undefined} /> : (
            <div className="rounded-xl border border-amberx-500/30 bg-ink-850 p-4">
              <div className="text-sm font-semibold text-white">Proposed: {actLabel(proposed.action_type)}</div>
              <p className="mt-1 text-xs text-ink-300">{proposed.reason}</p>
              <button className="btn-primary btn-sm mt-3" onClick={() => exec.mutate(proposed.id)} disabled={exec.isPending}>{exec.isPending && <Spinner />} Execute</button>
            </div>))}
          <ErrorBox error={exec.error || close.error} />

          {d.data.runs?.[0] && (
            <Card title="Agent decision trace" sub={d.data.runs[0].trigger}>
              <div className="grid gap-4 p-5 lg:grid-cols-[1fr_280px]">
                <div><div className="mb-3"><RunHeader run={d.data.runs[0]} /></div><TraceSteps run={d.data.runs[0]} animate={false} /></div>
                <div className="space-y-3"><RailCard rail={d.data.runs[0].rail} /><Firewall checks={d.data.runs[0].guardrails} /></div>
              </div>
            </Card>)}
          {d.data.invoices?.length > 0 && (
            <Card title="PayPal invoices" action={<button className="btn-ghost btn-sm" onClick={() => sync.mutate()} disabled={sync.isPending}>{sync.isPending && <Spinner />} Check payment status</button>}>
              <ul className="divide-y divide-ink-800">{d.data.invoices.map((i: any) => (
                <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-xs">
                  <span className="font-mono text-ink-100">{i.paypal_invoice_id} · {i.invoice_number}</span>
                  <span className="flex items-center gap-2"><Badge tone={i.status === "PAID" ? "green" : "blue"}>{titleCase(i.status)}</Badge>
                    {i.payer_view_url && <a className="text-volt-400 hover:text-volt-300" href={i.payer_view_url} target="_blank" rel="noreferrer">Open on PayPal</a>}</span>
                </li>))}</ul>
              <div className="px-5 pb-3"><ErrorBox error={sync.error} /></div>
            </Card>)}

          {d.data.analysis ? <Card title="AI analysis" action={active && <button className="btn-ghost btn-sm" onClick={() => reanalyse.mutate()} disabled={reanalyse.isPending}>{reanalyse.isPending && <Spinner />} Re-analyse</button>}>
            <div className="p-5"><AnalysisPanel a={d.data.analysis} /></div></Card>
            : <Notice>This historical case has no stored analysis.</Notice>}

          <Card title="Action plan">
            <ul className="divide-y divide-ink-800">
              {c.actions.map((a: any) => (
                <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-xs">
                  <div><div className="font-semibold text-ink-100">{actLabel(a.action_type)}{a.payload?.sequence ? ` #${a.payload.sequence}` : ""}</div>
                    <div className="text-ink-500">{a.executed_at ? `Executed ${dateTime(a.executed_at)}` : a.scheduled_for ? `Scheduled ${dateTime(a.scheduled_for)}` : a.reason?.slice(0, 90)}{a.blocked_reason ? ` · ${a.blocked_reason}` : ""}</div></div>
                  <Badge tone={a.status === "EXECUTED" ? "green" : a.status === "PROPOSED" ? "amber" : a.status === "BLOCKED" || a.status === "REJECTED" ? "red" : "gray"}>{titleCase(a.status)}</Badge>
                </li>))}
              {c.actions.length === 0 && <li className="px-5 py-4 text-xs text-ink-400">No actions planned.</li>}
            </ul>
          </Card>

          <Card title="Communication history">
            {d.data.communications.length === 0 ? <Empty title="No messages yet" /> : (
              <ul className="divide-y divide-ink-800">
                {d.data.communications.map((m: any) => (
                  <li key={m.id} className="px-5 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-semibold text-white">{m.subject}</span><DeliveryBadge s={m.delivery_status} /></div>
                    <div className="mt-0.5 text-[11px] text-ink-500">{titleCase(m.tone)} · {m.generated_by === "llm" ? "LLM" : "template"} · {dateTime(m.created_at)}</div>
                    <details className="mt-2"><summary className="cursor-pointer text-[11px] text-volt-400">Show message</summary><pre className="mt-2 whitespace-pre-wrap rounded-lg bg-ink-950 p-3 font-sans text-xs text-ink-200">{m.body}</pre></details>
                  </li>))}
              </ul>)}
          </Card>
          <div className="flex flex-wrap gap-2">
            {c.payment_id && <Link to={`/payments/${c.payment_id}`} className="btn-ghost btn-sm">View payment</Link>}
            {active && <button className="btn-danger btn-sm" onClick={() => close.mutate()} disabled={close.isPending}>Close as unrecovered</button>}
          </div>
        </div>)}
    </Drawer>
  );
}

export function Recovery() {
  const [sp, setSp] = useSearchParams();
  const [status, setStatus] = useState("active");
  const [view, setView] = useState("all");
  const [sort, setSort] = useState("priority");
  const caseId = sp.get("case");
  const q = useQuery({ queryKey: ["cases", status, sort, view], queryFn: () => api(`/api/recovery/cases?status=${status}&sort=${sort}${view !== "all" ? `&view=${view}` : ""}`), refetchInterval: 6000 });
  const items = q.data?.items ?? [];
  const total = items.reduce((s: number, c: any) => s + c.amount_minor, 0);
  const expected = items.reduce((s: number, c: any) => s + c.amount_minor * (c.recovery_probability ?? 0), 0);
  return (
    <div>
      <PageHeader title="Recovery Centre" sub="AI-ranked priority queue. Each case shows why it is ranked where it is." />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="card card-pad"><div className="label">Cases in view</div><div className="num mt-1 text-2xl text-white">{items.length}</div></div>
        <div className="card card-pad"><div className="label">Value in view</div><div className="num mt-1 text-2xl text-amberx-400">{money(total)}</div></div>
        <div className="card card-pad"><div className="label">Expected recoverable (estimate)</div><div className="num mt-1 text-2xl text-volt-300">{money(expected)}</div><div className="text-[11px] text-ink-500">Σ value × probability — not actual revenue</div></div>
      </div>
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap gap-2">
          <Tabs value={status} onChange={setStatus} items={[{ value: "active", label: "Open" }, { value: "RECOVERED", label: "Recovered" }, { value: "CLOSED_UNRECOVERED", label: "Unrecovered" }, { value: "CLOSED_ALREADY_PAID", label: "Closed · paid" }, { value: "all", label: "All" }]} />
          {status === "active" && <Tabs value={view} onChange={setView} items={[{ value: "all", label: "Everything" }, { value: "urgent", label: "Urgent" }, { value: "approval", label: "Requires approval" }]} />}
        </div>
        <select className="input w-full lg:w-60" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
          <option value="priority">Sort: AI priority</option><option value="revenue">Sort: highest revenue</option><option value="probability">Sort: highest probability</option><option value="oldest">Sort: oldest unresolved</option><option value="newest">Sort: newest</option>
        </select>
      </div>
      <Card>
        {q.isLoading ? <Loading /> : items.length === 0 ? <Empty title="No cases here" body="When payments fail or checkouts are abandoned, the agent opens cases automatically." /> : (
          <ul className="divide-y divide-ink-800">
            {items.map((c: any) => (
              <li key={c.id}>
                <button className="row-hover grid w-full grid-cols-[40px_1fr_auto] items-center gap-4 px-5 py-3.5 text-left md:grid-cols-[40px_1.3fr_1fr_120px_120px_110px]" onClick={() => setSp({ case: c.id })}>
                  <span className="num text-sm text-ink-400">#{c.rank}</span>
                  <span className="min-w-0"><span className="flex items-center gap-2"><span className="truncate font-semibold text-white">{c.customer.name}</span>{c.awaiting_approval && <Badge tone="amber">Needs approval</Badge>}</span>
                    <span className="mt-0.5 block truncate text-xs text-ink-400">{c.priority_reason ?? `${c.order_number} · ${titleCase(c.case_type)}`}</span></span>
                  <span className="hidden md:block">{c.failure_category ? <Category c={c.failure_category} /> : <Badge tone="amber">Pending at PayPal</Badge>}</span>
                  <span className="hidden md:block"><Prob p={c.recovery_probability} /></span>
                  <span className="hidden md:block"><CaseStatus s={c.status} /></span>
                  <span className="num text-right font-semibold text-white">{money(c.status === "RECOVERED" ? c.recovered_amount_minor : c.amount_minor, c.currency)}</span>
                </button>
              </li>))}
          </ul>)}
      </Card>
      <CaseDrawer caseId={caseId} onClose={() => setSp({})} />
    </div>
  );
}

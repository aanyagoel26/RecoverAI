import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Card, CaseStatus, Category, Drawer, Empty, ErrorBox, Loading, Notice, PageHeader, PaymentStatus, Spinner, Stat, Tabs, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { ago, dateTime, money, titleCase } from "../lib/format";
import { CaseDrawer, DeliveryBadge } from "./Recovery";

export function Communications() {
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<any>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["comms"], queryFn: () => api("/api/communications"), refetchInterval: 8000 });
  const st = useQuery({ queryKey: ["paypal-status"], queryFn: () => api("/api/paypal/status") });
  const items = (q.data?.items ?? []).filter((m: any) => filter === "all" || m.delivery_status === filter);
  return (
    <div>
      <PageHeader title="Customer Communication Centre" sub="Every recovery message the agent drafted or sent, with honest delivery status." />
      {st.data && !st.data.email_configured && <div className="mb-4"><Notice tone="amber">No email provider is connected. Messages are generated and logged as <b>Not sent · preview only</b> — nothing is delivered to customers. Connect Resend in <Link to="/integrations" className="underline">Integrations → Email</Link> to enable real delivery.</Notice></div>}
      <div className="mb-4"><Tabs value={filter} onChange={setFilter} items={[{ value: "all", label: "All" }, { value: "SENT", label: "Sent" }, { value: "PREVIEW_ONLY", label: "Preview only" }, { value: "TEST_SENT", label: "Tests" }, { value: "FAILED", label: "Failed" }]} /></div>
      <div className="grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-2">
          {q.isLoading ? <Loading /> : !items.length ? <Empty title="No messages yet" body="Approve a recovery action in the Recovery Centre to generate one." /> : (
            <ul className="max-h-[70vh] divide-y divide-ink-800 overflow-y-auto">
              {items.map((m: any) => (
                <li key={m.id}><button className={`row-hover w-full px-5 py-3 text-left ${open?.id === m.id ? "bg-ink-800/60" : ""}`} onClick={() => setOpen(m)}>
                  <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-white">{m.customer_name}</span><DeliveryBadge s={m.delivery_status} /></div>
                  <div className="truncate text-xs text-ink-300">{m.subject}</div>
                  <div className="mt-0.5 text-[11px] text-ink-500">{titleCase(m.channel)} · {titleCase(m.tone)} · {m.generated_by === "llm" ? "AI" : "template"} · {dateTime(m.created_at)}{m.clicked_at && <span className="text-mint-300"> · link opened</span>}</div>
                </button></li>))}
            </ul>)}
        </Card>
        <Card className="xl:col-span-3" title={open ? open.subject : "Message preview"} sub={open ? `To ${open.customer_name} <${open.customer_email}>` : undefined}
          action={open?.case_id && <button className="btn-ghost btn-sm" onClick={() => setCaseId(open.case_id)}>Open case</button>}>
          {!open ? <Empty title="Select a message" /> : (
            <div className="p-5">
              <div className="mb-3 flex flex-wrap gap-2"><DeliveryBadge s={open.delivery_status} /><Badge>{titleCase(open.channel)}</Badge>{open.provider && <Badge tone="blue">{open.provider}</Badge>}</div>
              <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
                {[["To", open.to_address ?? open.customer_email ?? "—"], ["Accepted by provider", open.sent_at ? dateTime(open.sent_at) : "—"],
                  ["Delivery confirmed", open.delivered_at ? dateTime(open.delivered_at) : open.channel === "sms" ? "Not yet" : "Not tracked for email"], ["Link opened", open.clicked_at ? dateTime(open.clicked_at) : "Not yet"]].map(([k, v]) =>
                  <div key={k}><dt className="text-ink-500">{k}</dt><dd className="truncate text-ink-200">{v}</dd></div>)}
              </dl>
              <pre className="whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-4 font-sans text-sm leading-relaxed text-ink-100">{open.body}</pre>
              {open.error && <p className="mt-2 text-xs text-amberx-400">{open.error}</p>}
              <div className="mt-3 flex gap-2"><button className="btn-ghost btn-sm" onClick={() => navigator.clipboard?.writeText(`${open.subject}\n\n${open.body}`)}>Copy message</button>
                {open.recovery_link && <a className="btn-ghost btn-sm" href={open.recovery_link} target="_blank" rel="noreferrer">Open checkout link</a>}</div>
            </div>)}
        </Card>
      </div>
      <CaseDrawer caseId={caseId} onClose={() => setCaseId(null)} />
    </div>
  );
}

function CustomerDrawer({ id, onClose, onCase }: { id: string | null; onClose: () => void; onCase: (id: string) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["customer", id], queryFn: () => api(`/api/customers/${id}`), enabled: !!id });
  const [phone, setPhone] = useState("");
  useEffect(() => { setPhone(q.data?.customer.phone ?? ""); }, [q.data]);
  const save = useMutation({ mutationFn: () => api(`/api/customers/${id}`, { method: "PUT", body: { phone: phone.trim() || null } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["customer", id] }); qc.invalidateQueries({ queryKey: ["customers"] }); } });
  const d = q.data;
  return (
    <Drawer open={!!id} onClose={onClose} title={d ? d.customer.name : "Customer"}>
      {!d ? <Loading /> : (
        <div className="space-y-5 p-5">
          <div className="text-sm text-ink-300">{d.customer.email} · {d.customer.country ?? "—"} {d.customer.is_demo && <Badge tone="violet">DEMO</Badge>} <Badge tone={d.customer.consent ? "green" : "gray"}>{d.customer.consent ? "Contact allowed" : "No consent"}</Badge></div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Attempts" value={d.stats.attempts} /><Stat label="Completed" value={d.stats.completed} tone="green" />
            <Stat label="Failed" value={d.stats.failed} tone="red" /><Stat label="Recovered" value={money(d.stats.recovered_minor)} tone="green" />
          </div>
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
            <label className="block"><span className="label">Phone (E.164, for SMS)</span><input className="input mt-1 w-56" placeholder="+15551234567" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
            <button className="btn-ghost btn-sm" disabled={save.isPending}>{save.isPending && <Spinner />} Save phone</button>
            {save.isSuccess && <span className="text-xs text-mint-300">Saved</span>}
          </form>
          <ErrorBox error={save.error} />
          <section><h3 className="label mb-2">Payments</h3>
            {!d.payments.length ? <p className="text-sm text-ink-400">None</p> : <ul className="divide-y divide-ink-800 rounded-lg border border-ink-800">{d.payments.map((p: any) => (
              <li key={p.id} className="flex items-center justify-between gap-2 px-3 py-2 text-xs"><Link to={`/payments/${p.id}`} className="font-mono text-volt-400 hover:underline">{p.reference}</Link>
                <span className="num text-ink-200">{money(p.amount_minor, p.currency)}</span><PaymentStatus s={p.status} /><Category c={p.failure_category} /><span className="text-ink-500">{ago(p.created_at)}</span></li>))}</ul>}</section>
          <section><h3 className="label mb-2">Recovery cases</h3>
            {!d.cases.length ? <p className="text-sm text-ink-400">None</p> : <ul className="divide-y divide-ink-800 rounded-lg border border-ink-800">{d.cases.map((c: any) => (
              <li key={c.id}><button className="row-hover flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs" onClick={() => onCase(c.id)}>
                <span className="num text-ink-200">{money(c.amount_minor, c.currency)}</span><CaseStatus s={c.status} /><span className="text-ink-500">{ago(c.created_at)}</span></button></li>))}</ul>}</section>
          <section><h3 className="label mb-2">Messages</h3>
            {!d.communications.length ? <p className="text-sm text-ink-400">None</p> : <ul className="divide-y divide-ink-800 rounded-lg border border-ink-800">{d.communications.map((m: any) => (
              <li key={m.id} className="flex items-center justify-between gap-2 px-3 py-2 text-xs"><span className="truncate text-ink-200">{m.subject}</span><DeliveryBadge s={m.delivery_status} /><span className="text-ink-500">{ago(m.created_at)}</span></li>))}</ul>}</section>
        </div>)}
    </Drawer>);
}

export function Customers() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [params, setParams] = useSearchParams();
  const openId = params.get("open");
  const [caseId, setCaseId] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["customers"], queryFn: () => api("/api/customers") });
  const consent = useMutation({ mutationFn: ({ id, v }: { id: string; v: boolean }) => api(`/api/customers/${id}/consent`, { body: { consent: v } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["customers"] }) });
  const items = (list.data?.items ?? []).filter((c: any) => !q || `${c.name} ${c.email}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <PageHeader title="Customers" sub="Payment behaviour and recovery-message consent. RecoverAI never contacts a customer without consent." />
      <div className="relative mb-4 max-w-sm"><Search className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" /><input className="input pl-9" placeholder="Search customers" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <Card>
        {list.isLoading ? <Loading /> : !items.length ? <Empty title="No customers" /> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-sm">
            <thead><tr className="border-b border-ink-700 text-left">{["Customer", "Country", "Completed", "Failed", "Pending", "Abandoned", "Recovery messages"].map((h) => <th key={h} className="label px-5 py-3">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-ink-800">{items.map((c: any) => (
              <tr key={c.id} className="row-hover">
                <td className="px-5 py-3"><button className="text-left" onClick={() => setParams({ open: c.id })}><div className="flex items-center gap-2 text-white hover:text-volt-300">{c.name}{c.is_demo && <Badge tone="violet">DEMO</Badge>}</div><div className="text-[11px] text-ink-500">{c.email}</div></button></td>
                <td className="px-5 text-ink-300">{c.country ?? "—"}</td><td className="num px-5 text-mint-300">{c.completed}</td><td className="num px-5 text-rose-400">{c.failed}</td>
                <td className="num px-5 text-amberx-400">{c.pending}</td><td className="num px-5 text-ink-200">{c.abandoned}</td>
                <td className="px-5"><span className="flex items-center gap-2"><Toggle checked={c.consent} onChange={(v) => consent.mutate({ id: c.id, v })} label={`Consent for ${c.name}`} /><span className="text-xs text-ink-400">{c.consent ? "Allowed" : "No consent"}</span></span></td>
              </tr>))}</tbody></table></div>)}
      </Card>
      <CustomerDrawer id={openId} onClose={() => setParams({})} onCase={setCaseId} />
      <CaseDrawer caseId={caseId} onClose={() => setCaseId(null)} />
    </div>
  );
}

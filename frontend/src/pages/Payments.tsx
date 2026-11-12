import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Copy, ExternalLink, FlaskConical, Plus, RefreshCw, Search, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AnalysisPanel } from "../components/Analysis";
import { PaymentsGrid } from "../components/PaymentsGrid";
import { Badge, Card, CaseStatus, Confirm, Empty, ErrorBox, Loading, Notice, PageHeader, PaymentStatus, SourceBadge, Spinner, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { actLabel, ago, dateTime, money, titleCase } from "../lib/format";

const FILTERS = [
  ["all", "All"], ["successful", "Successful"], ["failed", "Failed"], ["pending", "Pending"], ["abandoned", "Abandoned"],
  ["incomplete", "Not approved"], ["under_recovery", "Under recovery"], ["recovered", "Recovered"], ["attention", "Requires attention"],
] as const;

export function NewCheckoutButton() {
  const [res, setRes] = useState<any>(null);
  const m = useMutation({ mutationFn: () => api("/api/paypal/test-checkout", { body: {} }), onSuccess: setRes });
  return (
    <>
      <button className="btn-ghost btn-sm" onClick={() => m.mutate()} disabled={m.isPending}>{m.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />} New test checkout</button>
      {m.error && <ErrorBox error={m.error} />}
      {res && (
        <div className="fixed bottom-4 right-4 z-50 w-[360px] rounded-xl border border-ink-600 bg-ink-900 p-4 shadow-2xl fade-up">
          <div className="flex items-center justify-between"><span className="text-sm font-semibold text-white">Checkout {res.order_number} created</span><SourceBadge s={res.data_source} /></div>
          <p className="mt-1 text-xs text-ink-400">{res.data_source === "DEMO_SIMULATED" ? "PayPal isn't configured, so this checkout uses the labelled demo simulator." : "Open it and pay with a PayPal Sandbox buyer account."}</p>
          <div className="mt-3 flex gap-2">
            <a className="btn-primary btn-sm flex-1" href={res.checkout_url} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" /> Open checkout</a>
            <button className="btn-ghost btn-sm" onClick={() => navigator.clipboard?.writeText(res.checkout_url)}><Copy className="h-3.5 w-3.5" /></button>
            <button className="btn-ghost btn-sm" onClick={() => setRes(null)}>Close</button>
          </div>
        </div>)}
    </>
  );
}

export function Payments() {
  const [sp, setSp] = useSearchParams();
  const filter = (sp.get("filter") ?? "all") as string;
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const list = useQuery({
    queryKey: ["payments", filter, q, source],
    queryFn: () => api(`/api/payments?filter=${filter}&limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}${source ? `&source=${source}` : ""}`),
    refetchInterval: 5000,
  });
  const items = list.data?.items ?? [];
  const [grid, setGrid] = useState<any>(null);
  return (
    <div>
      <PageHeader title="Live payments" sub={<span className="inline-flex items-center gap-2"><span className="live-dot h-2 w-2 rounded-full bg-mint-400" /> Every PayPal payment attempt, refreshed every 5 seconds</span>}
        actions={<><button className="btn-ghost btn-sm" onClick={() => grid?.exportDataAsCsv({ fileName: "recoverai-payments.csv" })} disabled={!grid}>Export view</button><NewCheckoutButton /></>} />
      <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="overflow-x-auto"><Tabs value={filter} onChange={(v) => setSp(v === "all" ? {} : { filter: v })} items={FILTERS.map(([value, label]) => ({ value, label }))} /></div>
        <div className="flex gap-2">
          <div className="relative flex-1 xl:w-72"><Search className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" /><input className="input pl-9" placeholder="Search TXN, customer, order, PayPal ID" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <select className="input w-36" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Data source">
            <option value="">All sources</option><option value="PAYPAL_SANDBOX">Sandbox</option><option value="PAYPAL_LIVE">Live</option><option value="DEMO_SIMULATED">Demo</option>
          </select>
        </div>
      </div>
      <Card>
        {list.isLoading ? <Loading /> : items.length === 0 ? <Empty title="No payments match" body="Try another filter, or create a test checkout." /> : <PaymentsGrid rows={items} onReady={setGrid} />}
      </Card>
      <p className="mt-3 text-xs text-ink-500">{list.data?.total ?? 0} payment attempts · DEMO = simulated, SANDBOX = real PayPal Sandbox API</p>
    </div>
  );
}

export function PaymentDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { me } = useAuth();
  const d = useQuery({ queryKey: ["payment", id], queryFn: () => api(`/api/payments/${id}`), refetchInterval: 5000 });
  const analyse = useMutation({ mutationFn: () => api(`/api/payments/${id}/analyse`, { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const sync = useMutation({ mutationFn: () => api(`/api/payments/${id}/sync`, { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const sim = useMutation({ mutationFn: (event_type: string) => api("/api/demo/simulate-webhook", { body: { payment_id: id, event_type } }), onSuccess: () => qc.invalidateQueries() });
  const recon = useMutation({ mutationFn: () => api(`/api/payments/${id}/reconcile`, { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const cancel = useMutation({ mutationFn: (order_id: string) => api(`/api/orders/${order_id}/cancel`, { body: { reason: "Cancelled by merchant from payment detail" } }),
    onSuccess: () => { setAskCancel(false); qc.invalidateQueries(); } });
  const [askCancel, setAskCancel] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  if (d.isLoading) return <Loading />;
  if (d.error) return <ErrorBox error={d.error} />;
  const { payment: p, events, analyses, case: c, order_attempts, duplicate_warning, checkout_session } = d.data;
  const latest = analyses[0];
  const canRecover = !["COMPLETED", "REFUNDED", "PARTIALLY_REFUNDED", "CANCELLED"].includes(p.status);
  const canCancel = !["COMPLETED", "PARTIALLY_REFUNDED", "REFUNDED", "PENDING"].includes(p.status) && p.order_status !== "CANCELLED" && p.order_id;

  return (
    <div>
      <Link to="/payments" className="mb-4 inline-flex items-center gap-1 text-xs text-ink-400 hover:text-white"><ArrowLeft className="h-3.5 w-3.5" /> Payments</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2"><h1 className="font-mono text-2xl font-bold text-white">{p.reference}</h1><PaymentStatus s={p.status} /><SourceBadge s={p.data_source} /></div>
          <p className="mt-1 text-sm text-ink-300">{p.customer?.name} · {p.description} · {p.order_number}</p>
        </div>
        <div className="text-right"><div className="num text-3xl font-semibold text-white">{money(p.amount_minor, p.currency)}</div><div className="text-xs text-ink-400">{p.currency} · {titleCase(p.payment_method)}</div></div>
      </div>
      {p.reconcile_required && <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
        <TriangleAlert className="h-4 w-4" /><span className="flex-1"><b>Outcome uncertain.</b> The capture call timed out or PayPal returned a server error. A timeout is not proof the payment failed — RecoverAI will not retry until it reconciles with PayPal.</span>
        <button className="btn-ghost btn-sm" onClick={() => recon.mutate()} disabled={recon.isPending}>{recon.isPending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />} Reconcile now</button></div>}
      {recon.data && <div className="mb-4"><Notice tone={recon.data.result.changed ? "green" : "blue"}>PayPal says: <b>{recon.data.result.paypal_status}</b>{recon.data.result.capture_status ? ` · capture ${recon.data.result.capture_status}` : ""}. {recon.data.result.note ?? ""}</Notice></div>}
      <ErrorBox error={recon.error || cancel.error} />
      {p.refunded_minor > 0 && <div className="mb-4"><Notice tone="violet">{money(p.refunded_minor, p.currency)} refunded in PayPal. Net paid: {money(p.amount_minor - p.refunded_minor, p.currency)}.</Notice></div>}
      {duplicate_warning && <div className="mb-4 flex items-center gap-2 rounded-lg border border-amberx-500/40 bg-amberx-500/10 px-4 py-3 text-sm text-amberx-400"><TriangleAlert className="h-4 w-4" /> {duplicate_warning}</div>}

      <div className="grid gap-4 xl:grid-cols-5">
        <div className="space-y-4 xl:col-span-3">
          <Card title={<span className="flex items-center gap-2"><Bot className="h-4 w-4 text-volt-400" /> AI failure analysis</span>}
            action={canRecover && <button className="btn-primary btn-sm" onClick={() => analyse.mutate()} disabled={analyse.isPending}>{analyse.isPending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />} {latest ? "Re-run analysis" : "Run AI analysis"}</button>}>
            <div className="p-5">
              <ErrorBox error={analyse.error} />
              {!canRecover ? <Empty title="Payment completed" body={p.status_verified_by ? `Confirmed by ${titleCase(p.status_verified_by)}. Nothing to recover.` : "Nothing to recover."} />
                : latest ? <AnalysisPanel a={latest} /> : <Empty title="Not analysed yet" body="Run AI analysis to classify the failure, score recovery probability and get a recommended action." />}
            </div>
          </Card>
          {c && (
            <Card title="Recovery case" action={<Link to={`/recovery?case=${c.id}`} className="btn-ghost btn-sm">Open case</Link>}>
              <div className="flex flex-wrap items-center gap-3 px-5 py-4 text-sm">
                <CaseStatus s={c.status} /><span className="text-ink-300">{titleCase(c.case_type)}</span><span className="text-ink-500">·</span>
                <span className="text-ink-300">{c.reminders_sent} message(s) sent</span>
                {c.status === "RECOVERED" && <span className="num font-semibold text-mint-300">+{money(c.recovered_amount_minor, c.currency)} recovered</span>}
              </div>
              <ul className="divide-y divide-ink-800 border-t border-ink-800">
                {(c.actions ?? []).map((a: any) => (
                  <li key={a.id} className="flex items-center justify-between gap-3 px-5 py-2.5 text-xs">
                    <span className="text-ink-200">{actLabel(a.action_type)}{a.payload?.sequence ? ` #${a.payload.sequence}` : ""}</span>
                    <span className="flex items-center gap-2 text-ink-400">{a.blocked_reason && <span className="hidden md:inline">{a.blocked_reason}</span>}
                      <Badge tone={a.status === "EXECUTED" ? "green" : a.status === "PROPOSED" ? "amber" : a.status === "BLOCKED" ? "red" : "gray"}>{titleCase(a.status)}</Badge>
                      {a.scheduled_for && a.status === "SCHEDULED" && <span>{ago(a.scheduled_for)}</span>}</span>
                  </li>))}
              </ul>
            </Card>)}
        </div>

        <div className="space-y-4 xl:col-span-2">
          <Card title="Payment lifecycle" sub="Every state change and its source">
            <ol className="relative space-y-4 px-5 py-4">
              {events.map((e: any) => (
                <li key={e.id} className="relative pl-5">
                  <span className={`absolute left-0 top-1.5 h-2 w-2 rounded-full ${e.event_type.includes("rejected") ? "bg-amberx-400" : e.to_status === "COMPLETED" ? "bg-mint-400" : ["DECLINED", "FAILED"].includes(e.to_status) ? "bg-rose-400" : "bg-volt-400"}`} />
                  <div className="text-xs text-ink-100">{e.event_type === "status.changed" ? <>{e.from_status ? `${titleCase(e.from_status)} → ` : ""}<b>{titleCase(e.to_status)}</b></> : titleCase(e.event_type.replace(".", "_"))}</div>
                  <div className="text-[11px] text-ink-500">{titleCase(e.source)} · {dateTime(e.created_at)}{e.detail?.issue ? ` · ${e.detail.issue}` : ""}{e.detail?.reason ? ` · ${e.detail.reason}` : ""}</div>
                </li>))}
            </ol>
          </Card>
          <Card title="PayPal details">
            <dl className="grid grid-cols-[120px_1fr] gap-y-2 px-5 py-4 text-xs">
              {[["Order ID", p.paypal_order_id], ["Capture ID", p.paypal_capture_id], ["Status verified by", titleCase(p.status_verified_by)], ["PayPal issue", p.paypal_issue], ["Debug ID", p.paypal_debug_id],
                ["Checkout", checkout_session ? `${titleCase(checkout_session.status)} · order ${checkout_session.paypal_order_created ? "created" : "not created"}, ${checkout_session.paypal_order_approved ? "approved" : "not approved"}` : null]].map(([k, v]) =>
                <div key={k} className="contents"><dt className="text-ink-400">{k}</dt><dd className="break-all font-mono text-ink-100">{v || "—"}</dd></div>)}
            </dl>
            <div className="flex flex-wrap gap-2 border-t border-ink-800 px-5 py-3">
              {p.paypal_order_id && <button className="btn-ghost btn-sm" onClick={() => recon.mutate()} disabled={recon.isPending}>{recon.isPending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />} Reconcile with PayPal</button>}
              {p.data_source !== "DEMO_SIMULATED" && <button className="btn-ghost btn-sm" onClick={() => sync.mutate()} disabled={sync.isPending}>{sync.isPending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />} Refresh from PayPal</button>}
              {canCancel && <button className="btn-danger btn-sm" onClick={() => setAskCancel(true)}>Cancel order</button>}
              <button className="btn-ghost btn-sm" onClick={() => setShowRaw(!showRaw)}>{showRaw ? "Hide" : "Show"} raw response</button>
            </div>
            <div className="px-5"><ErrorBox error={sync.error} /></div>
            {showRaw && <pre className="max-h-64 overflow-auto border-t border-ink-800 bg-ink-950 p-4 font-mono text-[10px] text-ink-300">{JSON.stringify(d.data.raw_response, null, 2)}</pre>}
          </Card>
          {me?.merchant.is_demo && p.data_source === "DEMO_SIMULATED" && !["COMPLETED", "REFUNDED", "DECLINED", "FAILED"].includes(p.status) && (
            <Card title={<span className="flex items-center gap-2"><FlaskConical className="h-4 w-4 text-violet-300" /> Demo event simulator</span>}>
              <div className="space-y-2 p-5">
                <Notice tone="violet">Simulated events are stored as <b>is_simulated</b>, labelled DEMO and never treated as genuine PayPal events.</Notice>
                <div className="flex flex-wrap gap-2">
                  {["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.PENDING", "PAYMENT.CAPTURE.DENIED"].map((t) =>
                    <button key={t} className="btn-ghost btn-sm font-mono text-[10px]" onClick={() => sim.mutate(t)} disabled={sim.isPending}>{t}</button>)}
                </div>
                <ErrorBox error={sim.error} />
              </div>
            </Card>)}
          <Confirm open={askCancel} title={`Cancel order ${p.order_number}?`} danger confirmLabel="Cancel order" busy={cancel.isPending}
            body="The order is marked cancelled, open recovery cases are closed and every scheduled customer message is stopped. Paid orders can't be cancelled here — refund them in PayPal."
            onConfirm={() => cancel.mutate(p.order_id)} onCancel={() => setAskCancel(false)} />
          <Card title="Attempts for this order">
            <ul className="divide-y divide-ink-800">
              {order_attempts.map((a: any) => (
                <li key={a.id}><Link to={`/payments/${a.id}`} className={`flex items-center justify-between px-5 py-2.5 text-xs row-hover ${a.id === p.id ? "bg-ink-800/50" : ""}`}>
                  <span className="font-mono text-ink-100">{a.reference}</span><span className="text-ink-500">{dateTime(a.created_at)}</span><PaymentStatus s={a.status} />
                </Link></li>))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

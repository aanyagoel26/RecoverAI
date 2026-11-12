import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowRight, Beaker, Bot, ChevronDown, Radio, ShieldCheck, ShieldX, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, Empty, ErrorBox, Loading, Notice, PageHeader, Spinner, Tabs } from "../components/ui";
import { Firewall, OUTCOME, RailCard, RunHeader, TraceSteps } from "../components/Trace";
import { api } from "../lib/api";
import { actLabel, ago, money } from "../lib/format";
import { CaseDrawer } from "./Recovery";

const RAIL_SHORT: Record<string, string> = { PAY_LATER: "Pay Later", PAYPAL_INVOICE: "PayPal Invoice", VENMO: "Venmo", PAYPAL_CHECKOUT: "PayPal Checkout" };

export function Mission() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [animateId, setAnimateId] = useState<string | null>(null);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<any>(null);
  const [showBuyer, setShowBuyer] = useState(false);
  const [buyer, setBuyer] = useState({ customer_name: "", customer_email: "" });
  const scenarios = useQuery({ queryKey: ["lab"], queryFn: () => api("/api/lab/scenarios") });
  const [stream, setStream] = useState<"decisions" | "blocked">("decisions");
  const blocked = useQuery({ queryKey: ["blocked"], queryFn: () => api("/api/firewall/blocked"), refetchInterval: 8000 });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api("/api/agent/runs?limit=40"), refetchInterval: 5000 });
  const lab = useMutation({
    mutationFn: (scenario: string) => api("/api/lab/run", { body: { scenario, ...(buyer.customer_name ? { customer_name: buyer.customer_name } : {}),
      ...(buyer.customer_email ? { customer_email: buyer.customer_email } : {}) } }),
    onSuccess: async (r: any) => { setEvidence(r); await qc.invalidateQueries(); if (r.run_id) { setSelected(r.run_id); setAnimateId(r.run_id); } },
  });
  const items = runs.data?.items ?? [];
  useEffect(() => { if (!selected && items.length) setSelected(items[0].id); }, [items, selected]);
  const run = items.find((r: any) => r.id === selected) ?? items[0];
  const real = scenarios.data?.real_paypal;

  return (
    <div>
      <PageHeader title="Mission Control" sub="Watch the agent think. Every decision, every PayPal signal, every guardrail — as it happens."
        actions={<span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${real ? "border-mint-500/40 bg-mint-500/10 text-mint-300" : "border-violet-500/40 bg-violet-500/10 text-violet-200"}`}>
          <Radio className="h-3.5 w-3.5" /> {real ? "Live PayPal Sandbox" : "Demo mode · PayPal not configured"}</span>} />

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <div className="space-y-4">
          <Card title={<span className="flex items-center gap-2"><Beaker className="h-4 w-4 text-volt-400" /> Failure Lab</span>}
            sub={real ? "Creates a real PayPal Sandbox order and asks PayPal for a genuine decline (negative testing)." : "PayPal not configured: scenarios run as labelled DEMO simulations."}>
            <div className="space-y-2 p-3">
              {(scenarios.data?.items ?? []).map((s: any) => (
                <button key={s.key} onClick={() => lab.mutate(s.key)} disabled={lab.isPending}
                  className="group w-full rounded-lg border border-ink-700 bg-ink-850 p-3 text-left transition hover:border-volt-500/60 disabled:opacity-60">
                  <div className="flex items-center justify-between"><span className="text-sm font-semibold text-white">{s.label}</span><span className="num text-xs text-ink-300">{money(s.amount_minor)}</span></div>
                  <div className="mt-1 flex items-center gap-1.5 text-[11px] text-ink-400">
                    {s.issue ? <code className="rounded bg-ink-800 px-1 font-mono text-[10px] text-rose-300">{s.issue}</code> : <code className="rounded bg-ink-800 px-1 font-mono text-[10px] text-amberx-400">never approved</code>}
                    <ArrowRight className="h-3 w-3" /> <span className="text-volt-300">{RAIL_SHORT[s.expect_rail]}</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-snug text-ink-400">{s.story}</p>
                </button>))}
              <button className="flex w-full items-center justify-between px-1 pt-1 text-[11px] text-ink-400 hover:text-ink-200" onClick={() => setShowBuyer(!showBuyer)}>
                Use your sandbox buyer's name/email <ChevronDown className={`h-3 w-3 transition ${showBuyer ? "rotate-180" : ""}`} /></button>
              {showBuyer && <div className="space-y-2">
                <input className="input py-1.5 text-xs" placeholder="Buyer name" value={buyer.customer_name} onChange={(e) => setBuyer({ ...buyer, customer_name: e.target.value })} />
                <input className="input py-1.5 text-xs" placeholder="Sandbox buyer email (for invoices)" value={buyer.customer_email} onChange={(e) => setBuyer({ ...buyer, customer_email: e.target.value })} />
              </div>}
              {lab.isPending && <div className="flex items-center gap-2 px-1 text-xs text-ink-300"><Spinner className="h-3.5 w-3.5" /> {real ? "Calling PayPal Sandbox…" : "Simulating…"}</div>}
              <ErrorBox error={lab.error} />
            </div>
          </Card>

          <Card title={<span className="flex items-center gap-2"><Activity className="h-4 w-4 text-mint-400" /> {stream === "blocked" ? "Blocked by firewall" : "Decision stream"}</span>}
>
            <div className="border-b border-ink-800 px-4 py-2"><Tabs value={stream} onChange={setStream} items={[{ value: "decisions", label: "All decisions" }, { value: "blocked", label: "Blocked", count: blocked.data?.items?.length }]} /></div>
            {stream === "blocked" ? (blocked.isLoading ? <Loading /> : !blocked.data?.items?.length ? <Empty icon={<ShieldCheck className="h-5 w-5" />} title="Nothing blocked" body="The Trust Firewall hasn't had to stop any action." /> : (
              <ul className="max-h-[520px] divide-y divide-ink-800 overflow-y-auto">{blocked.data.items.map((a: any) => (
                <li key={a.id}><button onClick={() => setOpenCase(a.case_id)} className="row-hover w-full px-4 py-2.5 text-left">
                  <span className="flex items-center gap-2 text-xs font-semibold text-white"><ShieldX className="h-3.5 w-3.5 shrink-0 text-rose-400" /><span className="truncate">{actLabel(a.action_type)} · {a.customer}</span></span>
                  <span className="mt-0.5 block text-[11px] text-rose-300/90">{a.blocked_reason}</span>
                  <span className="block text-[11px] text-ink-500">{a.order_number} · {money(a.amount_minor, a.currency)} · {ago(a.created_at)}</span>
                </button></li>))}</ul>)) : runs.isLoading ? <Loading /> : !items.length ? <Empty title="No agent decisions yet" body="Trigger a Failure Lab scenario." /> : (
              <ul className="max-h-[520px] divide-y divide-ink-800 overflow-y-auto">
                {items.map((r: any) => {
                  const [label, tone] = OUTCOME[r.outcome] ?? [r.outcome, "gray"];
                  const dot = { green: "bg-mint-400", amber: "bg-amberx-400", red: "bg-rose-400", blue: "bg-volt-400", gray: "bg-ink-500" }[tone];
                  return (
                    <li key={r.id}><button onClick={() => { setSelected(r.id); setAnimateId(r.id); }} className={`row-hover flex w-full gap-2.5 px-4 py-2.5 text-left ${r.id === run?.id ? "bg-ink-800/70" : ""}`}>
                      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} />
                      <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-white">{r.customer ?? "—"} · {r.amount_minor != null ? money(r.amount_minor) : ""}</span>
                        <span className="block truncate text-[11px] text-ink-400">{label} · {ago(r.created_at)}</span></span>
                    </button></li>);
                })}
              </ul>)}
          </Card>
        </div>

        <div className="space-y-4">
          {evidence?.paypal && evidence.run_id === run?.id && (
            <div className="fade-up rounded-xl border border-volt-500/30 bg-gradient-to-r from-volt-500/10 to-transparent p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-white"><Zap className="h-4 w-4 text-volt-400" />
                {evidence.paypal.simulated ? "Demo simulation (not a PayPal call)" : evidence.paypal.issue ? "PayPal Sandbox responded" : "Real PayPal order created"}</div>
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[11px] text-ink-200">
                {evidence.paypal.http_status && <span>HTTP {evidence.paypal.http_status}</span>}
                {evidence.paypal.issue && <span>issue: <b className="text-rose-300">{evidence.paypal.issue}</b></span>}
                {evidence.paypal.debug_id && <span>debug_id: {evidence.paypal.debug_id}</span>}
                {evidence.paypal.paypal_order_id && <span>order: {evidence.paypal.paypal_order_id}</span>}
                <span>order #{evidence.order_number}</span>
              </div>
              {evidence.paypal.requested && <p className="mt-1.5 text-[10px] text-ink-500">Induced with PayPal's sandbox negative-testing header — the response above is PayPal's own.</p>}
            </div>)}

          {!run ? <Card><Empty icon={<Bot className="h-5 w-5" />} title="Pick or trigger a decision" body="Use the Failure Lab to create a real PayPal failure and watch the agent respond." /></Card> : (
            <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
              <Card title={<span className="flex items-center gap-2"><Bot className="h-4 w-4 text-volt-400" /> Agent reasoning</span>} sub={run.summary}
                action={run.case_id && <button className="btn-primary btn-sm shrink-0 whitespace-nowrap" onClick={() => setOpenCase(run.case_id)}>{run.outcome === "AWAITING_APPROVAL" ? "Review & approve" : "Open case"}</button>}>
                <div className="p-5">
                  <div className="mb-4"><RunHeader run={run} /><p className="mt-2 text-xs text-ink-400"><span className="font-semibold text-ink-200">Trigger:</span> {run.trigger}</p></div>
                  <TraceSteps run={run} animate={animateId === run.id} />
                </div>
              </Card>
              <div className="space-y-4">
                <RailCard rail={run.rail} />
                <Firewall checks={run.guardrails} />
                {run.outcome === "AWAITING_APPROVAL" && <Notice tone="amber">The agent prepared the action. Nothing reaches the customer until you approve it.</Notice>}
              </div>
            </div>)}
        </div>
      </div>
      <CaseDrawer caseId={openCase} onClose={() => setOpenCase(null)} />
    </div>
  );
}

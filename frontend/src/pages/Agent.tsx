import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Hand, Play, ShieldCheck, Zap } from "lucide-react";
import { useState } from "react";
import { Badge, Card, Category, Empty, ErrorBox, Loading, Notice, PageHeader, Prob, Spinner, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { actLabel, ago, money, titleCase } from "../lib/format";
import { ActivityFeed } from "./Dashboard";
import { CaseDrawer } from "./Recovery";

const MODES = [
  { v: "AUTOMATIC", t: "Automatic", d: "Executes safe, non-financial actions (reminders, status checks) on schedule, within consent and message limits.", I: Zap },
  { v: "APPROVAL", t: "Approval", d: "Prepares every customer-facing action. You approve before anything is sent.", I: ShieldCheck },
  { v: "MANUAL", t: "Manual", d: "Recommendations only. You decide and execute each step yourself.", I: Hand },
];
const PIPELINE = ["Detect event", "Retrieve order & session", "Analyse transaction", "Score probability", "Choose safest action", "Execute or request approval", "Monitor outcome", "Update analytics"];

export function Agent() {
  const qc = useQueryClient();
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [tab, setTab] = useState("approvals");
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api("/api/merchant/settings") });
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api("/api/agent/approvals"), refetchInterval: 6000 });
  const actions = useQuery({ queryKey: ["actions", tab], queryFn: () => api(`/api/agent/actions${tab === "executed" ? "?status=EXECUTED" : tab === "blocked" ? "?status=BLOCKED" : tab === "scheduled" ? "?status=SCHEDULED" : ""}`), enabled: tab !== "approvals", refetchInterval: 8000 });
  const save = useMutation({ mutationFn: (body: any) => api("/api/merchant/settings", { method: "PUT", body }), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const run = useMutation({ mutationFn: () => api("/api/agent/run", { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const s = settings.data;

  return (
    <div>
      <PageHeader title="Autonomous Agent Control Centre" sub="RecoverAI Autonomous Recovery Engine — the AI analyses and coordinates; a deterministic policy engine authorises every action."
        actions={<button className="btn-primary" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? <Spinner /> : <Play className="h-4 w-4" />} Run monitoring pass</button>} />
      {run.data && <div className="mb-4"><Notice tone="green">Monitoring pass complete — {Object.entries(run.data.summary).map(([k, v]) => `${titleCase(k)}: ${v}`).join(" · ")}</Notice></div>}
      <ErrorBox error={run.error || save.error} />

      <div className="mb-4 overflow-x-auto">
        <ol className="flex min-w-[900px] items-center gap-2">
          {PIPELINE.map((p, i) => (
            <li key={p} className="flex flex-1 items-center gap-2">
              <div className="card flex-1 px-3 py-2.5"><div className="num text-[10px] text-volt-400">STEP {i + 1}</div><div className="text-xs font-semibold text-white">{p}</div></div>
              {i < PIPELINE.length - 1 && <span className="text-ink-600">→</span>}
            </li>))}
        </ol>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card title="Recovery mode" sub="Applies to newly planned actions">
            {!s ? <Loading /> : (
              <div className="grid gap-3 p-5 md:grid-cols-3">
                {MODES.map(({ v, t, d, I }) => (
                  <button key={v} onClick={() => save.mutate({ recovery_mode: v })} className={`rounded-xl border p-4 text-left transition ${s.recovery_mode === v ? "border-volt-500 bg-volt-500/10 ring-1 ring-volt-500/40" : "border-ink-700 hover:border-ink-500"}`}>
                    <div className="flex items-center justify-between"><I className={`h-5 w-5 ${s.recovery_mode === v ? "text-volt-400" : "text-ink-400"}`} />{s.recovery_mode === v && <Badge tone="blue">Active</Badge>}</div>
                    <div className="mt-3 font-semibold text-white">{t}</div><p className="mt-1 text-xs leading-relaxed text-ink-400">{d}</p>
                  </button>))}
              </div>)}
            {s && <div className="grid gap-4 border-t border-ink-800 px-5 py-4 sm:grid-cols-3">
              <div><div className="label">Reminder schedule</div><div className="mt-1 text-sm text-white">{s.reminder_intervals_minutes.map((m: number) => (m < 60 ? `${m}m` : m < 1440 ? `${m / 60}h` : `${m / 1440}d`)).join(" → ")}</div></div>
              <div><div className="label">Message limit</div><div className="mt-1 text-sm text-white">{s.max_reminders_per_case} per case · ≥{s.min_minutes_between_messages}m apart</div></div>
              <div><div className="label">Abandonment after</div><div className="mt-1 text-sm text-white">{s.abandonment_threshold_minutes} min inactive</div></div>
              <p className="text-[11px] text-ink-500 sm:col-span-3">Edit these in Settings. Hard rules in every mode: never capture without customer approval, never charge an already-captured order again, never refund or change amounts, never contact customers without consent.</p>
            </div>}
          </Card>

          <Card title="Agent actions" action={<Tabs value={tab} onChange={setTab} items={[{ value: "approvals", label: "Awaiting approval", count: approvals.data?.items?.length }, { value: "scheduled", label: "Scheduled" }, { value: "executed", label: "Executed" }, { value: "blocked", label: "Blocked" }]} />}>
            {tab === "approvals" ? (approvals.isLoading ? <Loading /> : !approvals.data?.items?.length ? <Empty icon={<Bot className="h-5 w-5" />} title="Nothing awaiting approval" body="The agent will queue customer-facing actions here in Approval mode." /> : (
              <ul className="divide-y divide-ink-800">
                {approvals.data.items.map((a: any) => (
                  <li key={a.id}><button className="row-hover flex w-full flex-wrap items-center gap-4 px-5 py-3 text-left" onClick={() => setOpenCase(a.case_id)}>
                    <div className="min-w-0 flex-1"><div className="text-sm font-semibold text-white">{actLabel(a.action_type)} → {a.case.customer.name}</div><div className="truncate text-xs text-ink-400">{a.reason}</div></div>
                    <Category c={a.case.failure_category} /><Prob p={a.case.recovery_probability} /><span className="num w-24 text-right text-white">{money(a.case.amount_minor, a.case.currency)}</span>
                    <span className="btn-primary btn-sm">Review</span>
                  </button></li>))}
              </ul>)) : actions.isLoading ? <Loading /> : !actions.data?.items?.length ? <Empty title="No actions" /> : (
              <ul className="divide-y divide-ink-800">
                {actions.data.items.map((a: any) => (
                  <li key={a.id}><button className="row-hover flex w-full items-center gap-3 px-5 py-2.5 text-left text-xs" onClick={() => setOpenCase(a.case_id)}>
                    <span className="w-28 font-mono text-ink-300">{a.order_number}</span><span className="flex-1 text-ink-100">{actLabel(a.action_type)}{a.blocked_reason ? <span className="text-ink-500"> — {a.blocked_reason}</span> : ""}</span>
                    <span className="text-ink-500">{ago(a.executed_at ?? a.scheduled_for ?? a.created_at)}</span>
                    <Badge tone={a.status === "EXECUTED" ? "green" : a.status === "BLOCKED" ? "red" : "gray"}>{titleCase(a.status)}</Badge>
                  </button></li>))}
              </ul>)}
          </Card>
        </div>
        <Card title="Live agent activity" sub="Agent, PayPal and system events"><div className="max-h-[720px] overflow-y-auto"><ActivityFeed limit={40} /></div></Card>
      </div>
      <CaseDrawer caseId={openCase} onClose={() => setOpenCase(null)} />
    </div>
  );
}

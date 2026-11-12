import { AlertCircle, Bot, CheckCircle2, CircleHelp, ShieldCheck, Sparkles, User, Store } from "lucide-react";
import { Badge, Category, Prob } from "./ui";
import { actLabel, ago } from "../lib/format";

export function AnalysisPanel({ a }: { a: any }) {
  const r = a.recommendation;
  const sev = a.severity >= 70 ? ["High", "red"] : a.severity >= 45 ? ["Medium", "amber"] : ["Low", "green"];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Category c={a.failure_category} />
        <Badge tone={sev[1] as any}>Severity {sev[0]} · {a.severity}</Badge>
        <Badge tone={a.generated_by === "llm" ? "blue" : "gray"} title={a.generated_by === "rules" ? "Explanation from the deterministic rules engine (no LLM key configured)" : `Explanation written by ${a.model}`}>
          {a.generated_by === "llm" ? <><Sparkles className="h-3 w-3" /> LLM explanation</> : "Rules-based explanation"}
        </Badge>
        <span className="text-[11px] text-ink-500">{ago(a.created_at)}</span>
      </div>

      <div>
        <div className="label mb-1">What happened</div>
        <p className="text-sm leading-relaxed text-ink-100">{a.explanation}</p>
      </div>
      <div>
        <div className="label mb-1">Root cause</div>
        <p className="text-sm leading-relaxed text-ink-300">{a.root_cause}</p>
        <p className="mt-1 text-[11px] text-ink-500">Classification basis: {a.classification_basis}</p>
      </div>

      <div className="rounded-lg border border-ink-700 bg-ink-850 p-4">
        <div className="flex items-center justify-between"><span className="label">Recovery probability</span><Prob p={a.recovery_probability} w="w-32" /></div>
        <ul className="mt-3 space-y-1.5">
          {a.probability_factors.map((f: any, i: number) => (
            <li key={i} className="flex items-start justify-between gap-3 text-xs">
              <span className="text-ink-300"><span className="font-semibold text-ink-100">{f.factor}</span> — {f.detail}</span>
              <span className={`num shrink-0 ${i === 0 ? "text-ink-200" : f.effect > 0 ? "text-mint-300" : "text-rose-400"}`}>{i === 0 ? `${Math.round(f.effect * 100)}%` : `${f.effect > 0 ? "+" : ""}${Math.round(f.effect * 100)}`}</span>
            </li>))}
        </ul>
        <p className="mt-3 text-[10px] text-ink-500">Explainable weighted model with stated priors, adjusted by this merchant's closed-case history. Not a trained ML model.</p>
      </div>

      <div className="rounded-lg border border-volt-500/30 bg-gradient-to-br from-volt-500/10 to-transparent p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-white"><Bot className="h-4 w-4 text-volt-400" /> Recommended: {actLabel(r.action)}</div>
        <p className="mt-2 text-sm text-ink-200">{r.reason}</p>
        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
          <div><dt className="text-ink-400">Expected benefit</dt><dd className="text-mint-300">{r.expected_benefit}</dd></div>
          <div><dt className="text-ink-400">Potential risk</dt><dd className="text-amberx-400">{r.risk}</dd></div>
          <div><dt className="text-ink-400">Confidence</dt><dd className="num text-ink-100">{Math.round(r.confidence * 100)}%</dd></div>
          <div><dt className="text-ink-400">Human approval</dt><dd className="text-ink-100">{r.requires_approval ? "Required before sending" : "Not required (internal action)"}</dd></div>
        </dl>
        <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-ink-300">
          <span className="inline-flex items-center gap-1">{a.customer_action_required ? <User className="h-3 w-3 text-amberx-400" /> : <CheckCircle2 className="h-3 w-3 text-ink-500" />} Customer involvement {a.customer_action_required ? "needed" : "not needed"}</span>
          <span className="inline-flex items-center gap-1">{a.merchant_action_required ? <Store className="h-3 w-3 text-amberx-400" /> : <CheckCircle2 className="h-3 w-3 text-ink-500" />} Merchant involvement {a.merchant_action_required ? "needed" : "not needed"}</span>
        </div>
      </div>

      <div>
        <div className="label mb-1.5">Transaction evidence</div>
        <ul className="space-y-1">
          {(r.evidence ?? []).map((e: string, i: number) => <li key={i} className="flex gap-2 font-mono text-[11px] text-ink-300"><CircleHelp className="mt-0.5 h-3 w-3 shrink-0 text-ink-500" />{e}</li>)}
        </ul>
        {a.failure_category === "UNKNOWN" && <p className="mt-2 flex gap-1.5 text-[11px] text-amberx-400"><AlertCircle className="h-3.5 w-3.5 shrink-0" /> PayPal did not provide enough information to determine the cause, so RecoverAI does not guess one.</p>}
      </div>
      <p className="flex items-center gap-1.5 text-[10px] text-ink-500"><ShieldCheck className="h-3 w-3" /> The action was chosen by RecoverAI's deterministic policy engine. AI cannot move money.</p>
    </div>
  );
}

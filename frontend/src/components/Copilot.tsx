import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Send, Sparkles, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { actLabel } from "../lib/format";
import { Badge, Drawer, Spinner } from "./ui";

type Msg = { role: "user" | "assistant"; content: string; tools?: string[]; by?: string; confirmation?: any; links?: { label: string; to: string }[] };

const SUGGESTIONS = [
  "Why are my payments failing?", "How much revenue did I recover this week?", "Which payments need immediate attention?",
  "Show customers whose checkout was abandoned.", "Which recovery strategy is working best?", "Prepare a recovery plan for today's pending payments.", "Which actions require my approval?", "Why was this payment not retried?",
  "Explain TXN-2048",
];

/** Minimal, safe markdown: **bold**, _italic_, line breaks, lists. No HTML injection. */
function Rich({ text }: { text: string }) {
  return (
    <div className="space-y-1">
      {text.split("\n").map((line, i) => {
        const parts = line.split(/(\*\*[^*]+\*\*|_[^_]+_)/g).map((p, j) =>
          p.startsWith("**") ? <b key={j} className="text-white">{p.slice(2, -2)}</b> : p.startsWith("_") && p.endsWith("_") && p.length > 2 ? <i key={j} className="text-ink-300">{p.slice(1, -1)}</i> : <span key={j}>{p}</span>);
        return line.trim() === "" ? <div key={i} className="h-1" /> : <p key={i} className={line.match(/^(\d+\.|-)\s/) ? "pl-2" : ""}>{parts}</p>;
      })}
    </div>
  );
}

function linksFrom(toolCalls: any[]): { label: string; to: string }[] {
  const out: { label: string; to: string }[] = [];
  for (const t of toolCalls ?? []) {
    const o = t.output ?? {};
    for (const c of (o.cases ?? []).slice(0, 3)) out.push({ label: `Open ${c.customer}'s case`, to: `/recovery?case=${c.case_id}` });
    if (o.case_id) out.push({ label: `Open case ${o.reference ?? ""}`.trim(), to: `/recovery?case=${o.case_id}` });
  }
  return out.slice(0, 4);
}

export function Copilot({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Drawer open={open} onClose={onClose} width="max-w-lg" title={<span className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-volt-400" /> RecoverAI Copilot</span>}>
      <CopilotPanel onNavigate={onClose} />
    </Drawer>
  );
}

export function CopilotPanel({ onNavigate }: { onNavigate?: () => void }) {
  const nav = useNavigate();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const ask = useMutation({
    mutationFn: (message: string) => api("/api/ai/chat", { body: { message, history: msgs.map(({ role, content }) => ({ role, content })) } }),
    onSuccess: (r: any) => setMsgs((m) => [...m, { role: "assistant", content: r.answer, tools: r.tool_calls.map((t: any) => t.tool), by: r.generated_by,
      confirmation: r.confirmation, links: linksFrom(r.tool_calls) }]),
    onError: (e: Error) => setMsgs((m) => [...m, { role: "assistant", content: `Sorry — ${e.message}` }]),
  });
  const approve = useMutation({
    mutationFn: (c: any) => api(`/api/recovery/${c.case_id}/approve`, { body: { action_id: c.action_id } }),
    onSuccess: (r: any) => { qc.invalidateQueries(); setMsgs((m) => [...m, { role: "assistant", content: `Approved. Action status: **${r.action.status}**${r.action.blocked_reason ? ` — ${r.action.blocked_reason}` : ""}.` }]); },
  });
  const send = (text: string) => {
    const t = text.trim();
    if (!t || ask.isPending) return;
    setMsgs((m) => [...m, { role: "user", content: t }]);
    setInput("");
    ask.mutate(t);
  };

  return (
      <div className="flex h-full flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {msgs.length === 0 && (
            <div>
              <div className="rounded-xl border border-ink-700 bg-ink-850 p-4 text-sm text-ink-200">
                <div className="mb-1 flex items-center gap-2 font-semibold text-white"><Bot className="h-4 w-4 text-volt-400" /> Ask about your payments</div>
                Every number comes from your RecoverAI database through tool calls. I can draft messages and prepare approvals, but I never send messages or move money on my own.
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => <button key={s} className="rounded-full border border-ink-600 bg-ink-850 px-3 py-1.5 text-xs text-ink-200 hover:border-volt-500 hover:text-white" onClick={() => send(s)}>{s}</button>)}
              </div>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              <div className={m.role === "user" ? "max-w-[85%] rounded-2xl rounded-br-sm bg-volt-500 px-3.5 py-2 text-sm text-white keep-white" : "max-w-full text-sm leading-relaxed text-ink-200"}>
                {m.role === "user" ? m.content : <Rich text={m.content} />}
                {m.tools && m.tools.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Wrench className="h-3 w-3 text-ink-400" />
                    {m.tools.map((t, j) => <code key={j} className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-ink-300">{t}()</code>)}
                    {m.by && <Badge tone={m.by === "llm" ? "blue" : "gray"} title={m.by === "rules" ? "No LLM key configured: deterministic answer from the same tools" : "Answer written by the LLM from tool results"}>{m.by === "llm" ? "LLM" : "Rules engine"}</Badge>}
                  </div>
                )}
                {m.links && m.links.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {m.links.map((l) => <button key={l.to} className="btn-ghost btn-sm" onClick={() => { onNavigate?.(); nav(l.to); }}>{l.label} →</button>)}
                  </div>)}
                {m.confirmation && (
                  <div className="mt-3 rounded-lg border border-amberx-500/30 bg-amberx-500/10 p-3 text-xs">
                    <div className="font-semibold text-amberx-400">Approval required: {actLabel(m.confirmation.action_type)}</div>
                    <button className="btn-success btn-sm mt-2" disabled={approve.isPending} onClick={() => approve.mutate(m.confirmation)}><Check className="h-3.5 w-3.5" /> Approve & send</button>
                  </div>
                )}
              </div>
            </div>
          ))}
          {ask.isPending && <div className="flex items-center gap-2 text-xs text-ink-400"><Spinner className="h-3.5 w-3.5" /> Querying your data…</div>}
          <div ref={end} />
        </div>
        <form className="border-t border-ink-700 p-3" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <div className="flex gap-2">
            <input className="input" placeholder="Ask about payments, recovery, revenue…" value={input} onChange={(e) => setInput(e.target.value)} maxLength={1000} />
            <button className="btn-primary" disabled={!input.trim() || ask.isPending} aria-label="Send"><Send className="h-4 w-4" /></button>
          </div>
        </form>
      </div>
  );
}

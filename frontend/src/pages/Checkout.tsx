import { PayPalButtons, PayPalMessages, PayPalScriptProvider } from "@paypal/react-paypal-js";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, FlaskConical, Lock, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ErrorBox, Loading, Notice, Spinner } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { money } from "../lib/format";

export function Checkout() {
  const { token } = useParams();
  const [sp] = useSearchParams();
  const rail = sp.get("rail");
  const qc = useQueryClient();
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const page = useQuery({ queryKey: ["checkout", token], queryFn: () => api(`/api/public/checkout/${token}`, { auth: false }), refetchInterval: 5000, retry: false });
  const commId = sp.get("c");
  // Records that the customer opened the link; `c` attributes the click to the message that carried it.
  useEffect(() => { if (token) api(`/api/public/checkout/${token}/activity`, { body: commId ? { c: commId } : {}, auth: false }).catch(() => undefined); }, [token, commId]);
  const sim = useMutation({
    mutationFn: (outcome: string) => api(`/api/public/checkout/${token}/simulate`, { body: { outcome }, auth: false }),
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["checkout", token] }); },
  });

  if (page.isLoading) return <Shell><Loading label="Loading your order" /></Shell>;
  if (page.error) return <Shell><div className="py-10 text-center"><p className="text-lg font-semibold text-white">{(page.error as ApiError).status === 410 ? "This link has expired" : "Checkout link not found"}</p><p className="mt-2 text-sm text-ink-400">{(page.error as Error).message}</p></div></Shell>;
  const d = page.data;
  const o = d.order;
  const buttonStyle = { layout: "vertical" as const, shape: "rect" as const, label: "pay" as const, height: 44 };
  const buttonProps = {
    createOrder: async () => { setErr(null); const r = await api(`/api/public/checkout/${token}/create-order`, { method: "POST", auth: false }); return r.id as string; },
    onApprove: async (data: any, actions: any) => {
      try {
        const r = await api(`/api/public/checkout/${token}/capture`, { body: { paypal_order_id: data.orderID }, auth: false });
        setResult(r); qc.invalidateQueries({ queryKey: ["checkout", token] });
      } catch (e) {
        const issue = ((e as ApiError).data as any)?.detail?.issue;
        if (issue === "INSTRUMENT_DECLINED" && actions.restart) return actions.restart(); // PayPal-recommended: let the buyer pick another funding source
        setErr(e);
      }
    },
    onError: (e: any) => setErr(new Error(String(e?.message ?? "PayPal could not start checkout"))),
  };

  return (
    <Shell merchant={d.merchant.name} demo={o.data_source === "DEMO_SIMULATED"}>
      <div className="flex items-start justify-between gap-4 border-b border-ink-700 pb-5">
        <div><div className="text-xs text-ink-400">Order {o.order_number}</div><div className="mt-1 text-lg font-semibold text-white">{o.description}</div></div>
        <div className="text-right"><div className="num text-2xl font-semibold text-white">{money(o.amount_minor, o.currency)}</div><div className="text-[11px] text-ink-400">{o.currency}</div></div>
      </div>

      {d.paid ? (
        <div className="py-8 text-center">
          <CheckCircle2 className="mx-auto h-12 w-12 text-mint-400" />
          <p className="mt-3 text-lg font-semibold text-white">Payment complete</p>
          <p className="mt-1 text-sm text-ink-300">Thank you{d.customer_first_name ? `, ${d.customer_first_name}` : ""}! {result?.verified_by === "PAYPAL_API" ? "PayPal confirmed your payment." : result?.simulated ? "This was a simulated demo payment." : "This order has been paid. No further action is needed."}</p>
        </div>
      ) : d.pending ? (
        <div className="py-8 text-center"><Clock className="mx-auto h-10 w-10 text-amberx-400" /><p className="mt-3 font-semibold text-white">Payment pending at PayPal</p>
          <p className="mt-1 text-sm text-ink-300">PayPal is processing your payment. Please don't pay again — you'll be notified once it completes.</p></div>
      ) : (
        <div className="space-y-5 pt-5">
          <p className="text-sm text-ink-300">Hi {d.customer_first_name}, your order is saved. Complete your payment securely with PayPal — you can choose any funding source in your PayPal account.</p>
          {rail === "paylater" && (
            <div className="rounded-lg border border-volt-500/30 bg-volt-500/5 p-3">
              <div className="text-sm font-semibold text-white">Spread the cost with PayPal Pay Later</div>
              <p className="mt-0.5 text-xs text-ink-300">If you're eligible, PayPal lets you pay over time instead of all at once. PayPal decides eligibility at checkout.</p>
            </div>)}
          {rail === "venmo" && (
            <div className="rounded-lg border border-[#008CFF]/40 bg-[#008CFF]/10 p-3">
              <div className="text-sm font-semibold text-white">Pay in one tap with Venmo</div>
              <p className="mt-0.5 text-xs text-ink-300">Venmo is available for eligible US buyers, alongside PayPal and card.</p>
            </div>)}
          {d.paypal.enabled ? (
            <PayPalScriptProvider options={{ clientId: d.paypal.client_id, currency: o.currency, intent: "capture", components: "buttons,messages", enableFunding: "paylater,venmo" }}>
              {rail === "paylater" && <div className="mb-3 rounded bg-white p-2"><PayPalMessages amount={o.amount_minor / 100} placement="payment" style={{ layout: "text" }} /></div>}
              {rail === "paylater" && <PayPalButtons fundingSource="paylater" {...buttonProps} style={{ ...buttonStyle, color: "gold" }} />}
              {rail === "venmo" && <PayPalButtons fundingSource="venmo" {...buttonProps} style={{ ...buttonStyle, color: "blue" }} />}
              {(rail === "paylater" || rail === "venmo") && <div className="my-2 text-center text-[11px] text-ink-500">or pay another way</div>}
              <PayPalButtons {...buttonProps} style={buttonStyle} />
            </PayPalScriptProvider>
          ) : <Notice tone="amber">PayPal checkout is not configured on this server.</Notice>}
          <ErrorBox error={err} />
          {result && !d.paid && <Notice tone={result.status === "COMPLETED" ? "green" : "amber"}>{result.message ?? `Status: ${result.status}`}</Notice>}
          {d.simulator_allowed && (
            <div className="rounded-xl border border-dashed border-violet-500/40 bg-violet-500/5 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-violet-200"><FlaskConical className="h-4 w-4" /> Demo payment simulator</div>
              <p className="mt-1 text-xs text-ink-400">This order is demo data. These buttons create a <b>simulated</b> payment labelled DEMO — not a PayPal transaction.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="btn-success btn-sm" onClick={() => sim.mutate("complete")} disabled={sim.isPending}>{sim.isPending && <Spinner />} Simulate successful payment</button>
                <button className="btn-ghost btn-sm" onClick={() => sim.mutate("pending")} disabled={sim.isPending}>Simulate pending</button>
                <button className="btn-ghost btn-sm" onClick={() => sim.mutate("decline")} disabled={sim.isPending}>Simulate decline</button>
              </div>
              <ErrorBox error={sim.error} />
            </div>)}
        </div>)}
      <div className="mt-6 flex items-center justify-center gap-4 border-t border-ink-700 pt-4 text-[11px] text-ink-500">
        <span className="inline-flex items-center gap-1"><Lock className="h-3 w-3" /> Secure PayPal checkout</span>
        <span className="inline-flex items-center gap-1"><ShieldCheck className="h-3 w-3" /> No card details stored by {d.merchant.name}</span>
      </div>
      {d.merchant.support_email && <p className="mt-2 text-center text-[11px] text-ink-500">Questions? {d.merchant.support_email}</p>}
    </Shell>
  );
}

function Shell({ children, merchant, demo }: { children: React.ReactNode; merchant?: string; demo?: boolean }) {
  return (
    <div className="flex min-h-screen items-start justify-center bg-gradient-to-b from-ink-900 to-ink-950 px-4 py-10 sm:items-center">
      <div className="w-full max-w-md">
        <div className="mb-4 flex items-center justify-between"><span className="text-sm font-bold tracking-wide text-white">{merchant ?? "Secure checkout"}</span>{demo && <span className="rounded bg-violet-500/15 px-2 py-0.5 text-[10px] font-bold text-violet-300">DEMO ORDER</span>}</div>
        <div className="card p-6">{children}</div>
        <p className="mt-4 text-center text-[10px] text-ink-600">Recovery checkout powered by RecoverAI</p>
      </div>
    </div>
  );
}

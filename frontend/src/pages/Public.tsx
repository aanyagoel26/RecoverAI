import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, GitBranch, Radar, ShieldCheck, Sparkles, Zap } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/Logo";
import { TraceSteps } from "../components/Trace";
import { ErrorBox, Loading, Notice, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useAuth, type Me } from "../lib/auth";
import { CredentialForm } from "./Integrations";

function useDemoLogin() {
  const { signIn } = useAuth();
  const nav = useNavigate();
  return useMutation({
    mutationFn: () => api<{ token: string } & Me>("/api/auth/demo", { method: "POST", auth: false }),
    onSuccess: (r) => { signIn(r.token, r); nav("/mission"); },
  });
}

const EXAMPLE_RUN = {
  id: "example", outcome: "AWAITING_APPROVAL", data_source: "PAYPAL_SANDBOX", created_at: new Date().toISOString(),
  steps: [
    { key: "detect", label: "Detect event", status: "ok", detail: "PayPal API response: capture → 422 INSTRUMENT_DECLINED" },
    { key: "analyse", label: "Analyse transaction", status: "ok", detail: "Card / instrument declined — mapped from PayPal code" },
    { key: "score", label: "Score recovery probability", status: "ok", detail: "57% · customer history +4%" },
    { key: "decide", label: "Choose safest action & PayPal rail", status: "ok", detail: "Invite another funding source · rail: PayPal Pay Later" },
    { key: "guardrails", label: "Trust Firewall", status: "hold", detail: "8/9 checks passed · waiting for merchant approval" },
  ],
};

export function Landing() {
  const demo = useDemoLogin();
  const { me } = useAuth();
  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="grid-bg pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_at_top,black,transparent_70%)]" />
      <div className="pointer-events-none absolute -top-40 left-1/3 h-[520px] w-[900px] -translate-x-1/2 rounded-full bg-volt-500/20 blur-[120px]" />
      <header className="relative mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Logo />
        <nav className="flex items-center gap-2">
          {me ? <Link to="/mission" className="btn-primary">Open Mission Control</Link> : <>
            <Link to="/login" className="btn-ghost">Sign in</Link>
            <Link to="/signup" className="btn-primary">Get started</Link></>}
        </nav>
      </header>

      <section className="relative mx-auto grid max-w-6xl items-center gap-10 px-6 pb-14 pt-6 lg:grid-cols-[1.1fr_0.9fr] lg:pt-14">
        <div>
          <span className="inline-flex flex-wrap items-center gap-2 rounded-full border border-volt-500/30 bg-volt-500/10 px-3 py-1 text-[11px] font-semibold text-volt-300">
            <Sparkles className="h-3.5 w-3.5" /> Orders v2 · Invoicing · Pay Later · Venmo · Webhooks · MCP
          </span>
          <h1 className="mt-6 text-4xl font-extrabold leading-[1.04] tracking-tight text-white sm:text-6xl">
            Failed payments,<br /><span className="bg-gradient-to-r from-volt-400 via-volt-300 to-mint-300 bg-clip-text text-transparent">recovered by an AI you can trust.</span>
          </h1>
          <p className="mt-5 max-w-xl text-base text-ink-300 sm:text-lg">
            RecoverAI is an autonomous recovery agent for PayPal merchants. It reads PayPal's own failure signals, routes each lost sale to the PayPal product most likely to win it back, and proves every decision through a Trust Firewall — revenue only counts once PayPal confirms it.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <button className="btn-primary px-5 py-3 text-base" onClick={() => demo.mutate()} disabled={demo.isPending}>
              {demo.isPending ? <Spinner /> : <Zap className="h-4 w-4" />} Launch live demo
            </button>
            <Link to="/signup" className="btn-ghost px-5 py-3 text-base">Create merchant account <ArrowRight className="h-4 w-4" /></Link>
          </div>
          <div className="mt-4 max-w-md"><ErrorBox error={demo.error} /></div>
        </div>
        <div className="card relative p-5 shadow-[0_30px_80px_-30px_rgba(59,123,255,0.45)]">
          <div className="mb-3 flex items-center justify-between"><span className="label">Agent decision · illustration</span><span className="num text-sm text-white">$249.00</span></div>
          <TraceSteps run={EXAMPLE_RUN} />
          <div className="rounded-lg border border-amberx-500/30 bg-amberx-500/10 px-3 py-2 text-xs text-amberx-400">Prepared. Nothing reaches the customer until the merchant approves.</div>
        </div>
      </section>

      <section className="relative mx-auto max-w-6xl px-6 pb-14">
        <div className="grid gap-4 md:grid-cols-3">
          {[[Radar, "Real PayPal signals", "Classifies failures from PayPal's own issue codes, capture states and verified webhooks. No signal? It says Unknown — it never invents a reason."],
            [GitBranch, "PayPal-native recovery rails", "Pay Later when funds are the problem, Venmo for mobile buyers, a PayPal Invoice for high-value orders, or a fresh checkout."],
            [ShieldCheck, "Trust Firewall", "Consent, message limits, already-paid and pending checks run before every action. AI recommends; deterministic rules decide; money never moves without the buyer."]].map(([I, t, d]: any) => (
            <div key={t} className="card card-pad"><I className="h-5 w-5 text-volt-400" /><h3 className="mt-3 font-bold text-white">{t}</h3><p className="mt-1.5 text-sm leading-relaxed text-ink-300">{d}</p></div>))}
        </div>
      </section>

      <section className="relative mx-auto max-w-6xl px-6 pb-14">
        <h2 className="text-center text-2xl font-bold text-white">One agent. Four ways back to a completed PayPal payment.</h2>
        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[["PayPal Pay Later", "Declined or insufficient funds", "from-[#003087] to-[#0070E0]"], ["Venmo", "US buyer abandoned on mobile", "from-[#008CFF] to-[#3D95CE]"],
            ["PayPal Invoice", "High-value or repeated failures", "from-[#1A2847] to-[#3B7BFF]"], ["PayPal Checkout", "Everything else — a fresh, buyer-approved order", "from-[#003087] to-[#009CDE]"]].map(([t, w, g]) => (
            <div key={t} className={`rounded-xl bg-gradient-to-br p-[1px] ${g}`}><div className="h-full rounded-[11px] bg-ink-900/90 p-4">
              <div className="font-bold text-white">{t}</div><div className="mt-1 text-xs text-ink-300">{w}</div></div></div>))}
        </div>
      </section>

      <section className="relative mx-auto grid max-w-6xl items-center gap-8 px-6 pb-20 lg:grid-cols-2">
        <div>
          <h2 className="text-2xl font-bold text-white">Run recovery from any AI assistant.</h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-300">RecoverAI ships an MCP server. Ask Claude "which PayPal payments should I chase today?" and get ranked cases, explanations and drafts — while approvals stay with you.</p>
          <div className="mt-4 flex flex-wrap gap-2 text-[11px]">{["list_priority_cases", "explain_payment", "get_agent_trace", "draft_recovery_message", "request_recovery_approval", "run_failure_lab"].map((t) =>
            <code key={t} className="rounded bg-ink-800 px-2 py-1 font-mono text-ink-200">{t}()</code>)}</div>
        </div>
        <div className="card p-4 font-mono text-[12px] leading-relaxed">
          <div className="text-ink-500">you › </div><div className="text-white">Which failed PayPal payments should I chase today, and why?</div>
          <div className="mt-3 text-ink-500">assistant › <span className="text-volt-300">list_priority_cases()</span></div>
          <div className="text-ink-200">1. Noah Williams — $1,500 · card declined → PayPal Invoice rail · awaiting your approval</div>
          <div className="text-ink-200">2. Alex Morgan — $249 · buyer action required · 78% likely</div>
          <div className="mt-2 text-[10px] text-ink-500">Illustrative transcript — numbers come from your RecoverAI data.</div>
        </div>
      </section>
      <footer className="relative border-t border-ink-800 py-6 text-center text-xs text-ink-500">RecoverAI · Built for the PayPal AI Hackathon 2026 · Recover Revenue. Automatically.</footer>
    </div>
  );
}

function AuthShell({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col justify-center px-6 py-12 sm:px-16">
        <Link to="/" className="mb-10"><Logo /></Link>
        <h1 className="text-2xl font-bold text-white">{title}</h1>
        <p className="mt-1 text-sm text-ink-300">{sub}</p>
        <div className="mt-8 max-w-sm">{children}</div>
      </div>
      <div className="relative hidden overflow-hidden border-l border-ink-800 bg-ink-900 lg:block">
        <div className="grid-bg absolute inset-0 opacity-60" />
        <div className="absolute inset-0 flex items-center justify-center p-16">
          <div className="card w-full max-w-md p-6">
            <div className="label">AI analysis · TXN-2048</div>
            <p className="mt-3 text-sm leading-relaxed text-ink-200">PayPal indicated that the payer must complete an additional step before the payment can be captured.</p>
            <div className="mt-4 flex items-center justify-between"><span className="text-xs text-ink-400">Recovery probability</span><span className="num text-lg font-semibold text-mint-300">78%</span></div>
            <div className="mt-2 h-1.5 rounded-full bg-ink-700"><div className="h-full w-[78%] rounded-full bg-mint-400" /></div>
            <div className="mt-4 rounded-lg border border-volt-500/30 bg-volt-500/10 p-3 text-xs text-volt-200">Recommended: invite the customer to a secure PayPal checkout. Do not charge again automatically.</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Login() {
  const { signIn, me } = useAuth();
  const nav = useNavigate();
  const demo = useDemoLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const m = useMutation({
    mutationFn: () => api<{ token: string } & Me>("/api/auth/login", { body: { email, password }, auth: false }),
    onSuccess: (r) => { signIn(r.token, r); nav(r.merchant.onboarding_complete ? "/dashboard" : "/onboarding"); },
  });
  if (me) return <Navigate to="/dashboard" replace />;
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <AuthShell title="Sign in" sub="Welcome back to your recovery command centre.">
      <form className="space-y-3" onSubmit={submit}>
        <div><label className="label" htmlFor="email">Email</label><input id="email" className="input mt-1" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div><div className="flex items-center justify-between"><label className="label" htmlFor="pw">Password</label><Link to="/forgot-password" className="text-[11px] font-semibold text-volt-400 hover:text-volt-300">Forgot password?</Link></div>
          <input id="pw" className="input mt-1" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <ErrorBox error={m.error} />
        <button className="btn-primary w-full" disabled={m.isPending}>{m.isPending && <Spinner />} Sign in</button>
      </form>
      <div className="my-5 flex items-center gap-3 text-[11px] text-ink-500"><div className="h-px flex-1 bg-ink-700" />OR<div className="h-px flex-1 bg-ink-700" /></div>
      <button className="btn-ghost w-full" onClick={() => demo.mutate()} disabled={demo.isPending}>{demo.isPending ? <Spinner /> : <Zap className="h-4 w-4 text-volt-400" />} Explore the demo workspace</button>
      <ErrorBox error={demo.error} />
      <p className="mt-6 text-sm text-ink-400">New to RecoverAI? <Link to="/signup" className="font-semibold text-volt-400 hover:text-volt-300">Create an account</Link></p>
    </AuthShell>
  );
}

export function Signup() {
  const { signIn, me } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({ full_name: "", business_name: "", email: "", password: "" });
  const m = useMutation({
    mutationFn: () => api<{ token: string } & Me>("/api/auth/signup", { body: f, auth: false }),
    onSuccess: (r) => { signIn(r.token, r); nav("/onboarding"); },
  });
  if (me) return <Navigate to="/dashboard" replace />;
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <AuthShell title="Create your merchant account" sub="Start recovering failed and abandoned PayPal payments.">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div><label className="label" htmlFor="fn">Your name</label><input id="fn" className="input mt-1" required value={f.full_name} onChange={set("full_name")} /></div>
        <div><label className="label" htmlFor="bn">Business name</label><input id="bn" className="input mt-1" required value={f.business_name} onChange={set("business_name")} /></div>
        <div><label className="label" htmlFor="em">Work email</label><input id="em" className="input mt-1" type="email" required value={f.email} onChange={set("email")} /></div>
        <div><label className="label" htmlFor="pw">Password</label><input id="pw" className="input mt-1" type="password" minLength={10} required value={f.password} onChange={set("password")} />
          <p className="mt-1 text-[11px] text-ink-400">At least 10 characters.</p></div>
        <ErrorBox error={m.error} />
        <button className="btn-primary w-full" disabled={m.isPending}>{m.isPending && <Spinner />} Create account</button>
      </form>
      <p className="mt-6 text-sm text-ink-400">Already have an account? <Link to="/login" className="font-semibold text-volt-400 hover:text-volt-300">Sign in</Link></p>
    </AuthShell>
  );
}

export function Onboarding() {
  const { me, refresh } = useAuth();
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [profile, setProfile] = useState({ name: me?.merchant.name ?? "", website: me?.merchant.website ?? "", industry: me?.merchant.industry ?? "" });
  const [mode, setMode] = useState("APPROVAL");
  const [product, setProduct] = useState({ name: "", price: "" });
  const integ = useQuery({ queryKey: ["integrations"], queryFn: () => api("/api/integrations"), enabled: step === 1 });
  const finish = useMutation({
    mutationFn: async () => {
      await api("/api/merchant/profile", { method: "PUT", body: { ...profile, onboarding_complete: true } });
      await api("/api/merchant/settings", { method: "PUT", body: { recovery_mode: mode, brand_name: profile.name } });
      if (product.name && Number(product.price) > 0) await api("/api/merchant/products", { body: { name: product.name, price_minor: Math.round(Number(product.price) * 100) } });
    },
    onSuccess: async () => { await refresh(); nav("/dashboard"); },
  });
  const steps = ["Business", "PayPal", "Recovery mode", "First product"];
  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col px-6 py-10">
      <Logo />
      <div className="mt-10 flex gap-2">{steps.map((s, i) => <div key={s} className={`h-1 flex-1 rounded-full ${i <= step ? "bg-volt-500" : "bg-ink-700"}`} />)}</div>
      <p className="label mt-3">Step {step + 1} of {steps.length} · {steps[step]}</p>
      <div className="card card-pad mt-4">
        {step === 0 && <div className="space-y-3">
          <h2 className="text-lg font-bold text-white">Tell us about your business</h2>
          <input className="input" placeholder="Business name" value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} />
          <input className="input" placeholder="Website (optional)" value={profile.website} onChange={(e) => setProfile({ ...profile, website: e.target.value })} />
          <input className="input" placeholder="Industry (optional)" value={profile.industry} onChange={(e) => setProfile({ ...profile, industry: e.target.value })} />
        </div>}
        {step === 1 && <div className="space-y-3">
          <h2 className="text-lg font-bold text-white">Connect PayPal Sandbox</h2>
          <p className="text-sm text-ink-300">Paste the Client ID and Secret from <a className="text-volt-400 underline" href="https://developer.paypal.com/dashboard/applications/sandbox" target="_blank" rel="noreferrer">PayPal Developer → Apps &amp; Credentials</a> (Sandbox tab). They're encrypted on the server and never shown again; the browser only ever sees a masked value.</p>
          {integ.data ? <CredentialForm provider="paypal_sandbox" p={integ.data.providers.paypal_sandbox} /> : <Spinner />}
          {integ.data && <p className="text-xs text-ink-400">Webhook URL for your PayPal app: <code className="font-mono text-ink-200">{integ.data.webhook_url}</code></p>}
          <p className="text-xs text-ink-400">You can skip this and finish later in Integrations. The demo workspace works without PayPal.</p>
        </div>}
        {step === 2 && <div className="space-y-3">
          <h2 className="text-lg font-bold text-white">How autonomous should the agent be?</h2>
          {[["AUTOMATIC", "Automatic", "Sends reminders and status checks on its own, within your limits and customer consent."],
            ["APPROVAL", "Approval (recommended)", "The agent prepares each customer message; you approve it with one click."],
            ["MANUAL", "Manual", "The agent only recommends. You execute everything yourself."]].map(([v, t, d]) => (
            <label key={v} className={`flex cursor-pointer gap-3 rounded-lg border p-3 ${mode === v ? "border-volt-500 bg-volt-500/10" : "border-ink-700"}`}>
              <input type="radio" name="mode" checked={mode === v} onChange={() => setMode(v)} className="mt-1 accent-[#3B7BFF]" />
              <span><span className="block text-sm font-semibold text-white">{t}</span><span className="text-xs text-ink-400">{d}</span></span>
            </label>))}
          <p className="text-xs text-ink-400">In every mode the agent can never capture, refund or change an amount.</p>
        </div>}
        {step === 3 && <div className="space-y-3">
          <h2 className="text-lg font-bold text-white">Add a product for test checkouts</h2>
          <input className="input" placeholder="Product name" value={product.name} onChange={(e) => setProduct({ ...product, name: e.target.value })} />
          <input className="input" placeholder="Price (USD)" type="number" min="0.5" step="0.01" value={product.price} onChange={(e) => setProduct({ ...product, price: e.target.value })} />
          <ErrorBox error={finish.error} />
        </div>}
        <div className="mt-6 flex justify-between">
          <button className="btn-ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
          {step < steps.length - 1 ? <button className="btn-primary" onClick={() => setStep(step + 1)} disabled={step === 0 && !profile.name.trim()}>Continue</button>
            : <button className="btn-primary" onClick={() => finish.mutate()} disabled={finish.isPending}>{finish.isPending && <Spinner />} Finish setup</button>}
        </div>
      </div>
    </div>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const m = useMutation({ mutationFn: () => api<{ ok: boolean; email_delivery: string }>("/api/auth/forgot-password", { body: { email }, auth: false }) });
  return (
    <AuthShell title="Reset your password" sub="We'll email you a link that's valid for one hour.">
      {m.isSuccess ? (
        <div className="space-y-3">
          <Notice tone="green">If an account exists for <b>{email}</b>, a reset link is on its way.</Notice>
          {m.data.email_delivery === "not_configured" && <Notice tone="amber">This server has no default email provider. Unless your account has its own email connection, no email was sent — in local development the reset link is printed in the backend terminal.</Notice>}
        </div>
      ) : (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <div><label className="label" htmlFor="email">Email</label><input id="email" className="input mt-1" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          <ErrorBox error={m.error} />
          <button className="btn-primary w-full" disabled={m.isPending}>{m.isPending && <Spinner />} Send reset link</button>
        </form>)}
      <p className="mt-6 text-sm text-ink-400"><Link to="/login" className="font-semibold text-volt-400 hover:text-volt-300">Back to sign in</Link></p>
    </AuthShell>);
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [pw, setPw] = useState({ a: "", b: "" });
  const m = useMutation({ mutationFn: () => {
    if (pw.a !== pw.b) throw new Error("Passwords don't match");
    return api("/api/auth/reset-password", { body: { token, password: pw.a }, auth: false });
  } });
  return (
    <AuthShell title="Choose a new password" sub="Resetting signs you out of every other session.">
      {!token ? <Notice tone="amber">This link is missing its token. Request a new reset link.</Notice> : m.isSuccess ? (
        <Notice tone="green">Password updated. <Link to="/login" className="font-semibold underline">Sign in</Link> with your new password.</Notice>
      ) : (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <div><label className="label" htmlFor="pa">New password</label><input id="pa" className="input mt-1" type="password" autoComplete="new-password" minLength={10} required value={pw.a} onChange={(e) => setPw({ ...pw, a: e.target.value })} />
            <p className="mt-1 text-[11px] text-ink-400">At least 10 characters.</p></div>
          <div><label className="label" htmlFor="pb">Confirm new password</label><input id="pb" className="input mt-1" type="password" autoComplete="new-password" required value={pw.b} onChange={(e) => setPw({ ...pw, b: e.target.value })} /></div>
          <ErrorBox error={m.error} />
          <button className="btn-primary w-full" disabled={m.isPending}>{m.isPending && <Spinner />} Update password</button>
        </form>)}
      <p className="mt-6 text-sm text-ink-400"><Link to="/forgot-password" className="font-semibold text-volt-400 hover:text-volt-300">Request a new link</Link></p>
    </AuthShell>);
}

export function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { me, refresh } = useAuth();
  const m = useMutation({ mutationFn: () => api("/api/auth/verify-email", { body: { token }, auth: false }), onSuccess: () => { if (me) refresh(); } });
  const started = useRef(false);
  useEffect(() => { if (token && !started.current) { started.current = true; m.mutate(); } }, [token, m]);
  return (
    <AuthShell title="Verify your email" sub="Confirming you control this inbox.">
      {!token ? <Notice tone="amber">This link is missing its token.</Notice> : m.isPending ? <Loading label="Verifying" /> : m.isSuccess ? (
        <Notice tone="green">Email verified. <Link to={me ? "/dashboard" : "/login"} className="font-semibold underline">Continue</Link></Notice>
      ) : <ErrorBox error={m.error} />}
    </AuthShell>);
}

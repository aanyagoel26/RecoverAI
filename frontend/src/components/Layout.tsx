import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BarChart3, Bell, Bot, Calculator, ChevronDown, CreditCard, FileClock, LayoutDashboard, LifeBuoy, LogOut, Mail, Menu, Moon, Plug, Radar,
  ScanSearch, Settings, Sparkles, Sun, Users, Workflow, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import { Copilot } from "./Copilot";
import { Logo } from "./Logo";

const GROUPS: { label: string; items: { to: string; label: string; icon: any; badge?: string }[] }[] = [
  { label: "", items: [{ to: "/dashboard", label: "Overview", icon: LayoutDashboard }, { to: "/mission", label: "Mission Control", icon: Radar, badge: "live" }] },
  { label: "Recover", items: [
    { to: "/payments", label: "Payments", icon: CreditCard }, { to: "/recovery", label: "Recovery Queue", icon: LifeBuoy, badge: "approvals" },
    { to: "/intelligence", label: "Failure Intelligence", icon: ScanSearch }, { to: "/simulator", label: "Revenue Simulator", icon: Calculator }] },
  { label: "Customers", items: [{ to: "/customers", label: "Customers", icon: Users }, { to: "/communications", label: "Messages", icon: Mail }] },
  { label: "Insight", items: [{ to: "/analytics", label: "Analytics", icon: BarChart3 }, { to: "/copilot", label: "AI Copilot", icon: Sparkles }] },
  { label: "Setup", items: [
    { to: "/integrations", label: "Integrations", icon: Plug, badge: "paypal" }, { to: "/agent", label: "Automation", icon: Workflow },
    { to: "/audit", label: "Audit log", icon: FileClock }, { to: "/settings", label: "Settings", icon: Settings }] },
];

export function Layout() {
  const { me, signOut } = useAuth();
  const { theme, toggle } = useTheme();
  const nav = useNavigate();
  const [mobile, setMobile] = useState(false);
  const [copilot, setCopilot] = useState(false);
  const [menu, setMenu] = useState(false);
  const status = useQuery({ queryKey: ["paypal-status"], queryFn: () => api("/api/paypal/status"), refetchInterval: 30000 });
  const notes = useQuery({ queryKey: ["notifications"], queryFn: () => api("/api/notifications"), refetchInterval: 10000 });
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api("/api/agent/approvals"), refetchInterval: 10000 });
  const resend = useMutation({ mutationFn: () => api("/api/auth/resend-verification", { method: "POST" }) });
  const pp = status.data;
  const ppOk = pp?.connection_status === "CONNECTED";

  const sidebar = (
    <nav className="flex h-full flex-col px-3 py-4" aria-label="Main">
      <div className="px-2 pb-4"><Logo /></div>
      <div className="flex-1 space-y-4 overflow-y-auto">
        {GROUPS.map((g) => (
          <div key={g.label || "top"}>
            {g.label && <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-500">{g.label}</div>}
            <div className="space-y-0.5">
              {g.items.map(({ to, label, icon: Icon, badge }) => (
                <NavLink key={to} to={to} onClick={() => setMobile(false)}
                  className={({ isActive }) => clsx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                    isActive ? "bg-volt-500/12 text-white ring-1 ring-inset ring-volt-500/30" : "text-ink-300 hover:bg-ink-800 hover:text-white")}>
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="flex-1">{label}</span>
                  {badge === "live" && <span className="rounded bg-volt-500/20 px-1.5 text-[9px] font-bold uppercase tracking-wide text-volt-300">Live</span>}
                  {badge === "approvals" && approvals.data?.items?.length > 0 && <span className="num keep-white rounded-full bg-amberx-500 px-1.5 text-[10px] font-bold text-ink-950" title="Needs your approval">{approvals.data.items.length}</span>}
                  {badge === "paypal" && <span className={clsx("h-2 w-2 rounded-full", ppOk ? "bg-mint-400" : pp?.credentials_configured ? "bg-amberx-400" : "bg-ink-500")} />}
                </NavLink>))}
            </div>
          </div>))}
      </div>
    </nav>
  );

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-ink-800 bg-ink-950 lg:block">{sidebar}</aside>
      {mobile && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-ink-950/80" onClick={() => setMobile(false)} />
          <aside className="relative h-full w-72 border-r border-ink-800 bg-ink-950">
            <button className="absolute right-3 top-4 p-1 text-ink-400" onClick={() => setMobile(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
            {sidebar}
          </aside>
        </div>)}
      <div className="lg:pl-64">
        {me?.merchant.is_demo && (
          <div className="border-b border-violet-500/20 bg-violet-500/10 px-4 py-1.5 text-center text-[11px] font-medium text-violet-200">
            DEMO workspace · rows marked <b>DEMO</b> are seeded examples; <b>SANDBOX</b> rows are real PayPal Sandbox payments.
          </div>)}
        {me && !me.merchant.is_demo && !me.user.email_verified && (
          <div className="flex flex-wrap items-center justify-center gap-3 border-b border-amberx-500/30 bg-amberx-500/10 px-4 py-1.5 text-[12px] text-amberx-400">
            Please verify {me.user.email}. <button className="font-semibold underline" onClick={() => resend.mutate()} disabled={resend.isPending}>Resend email</button>
            {resend.data && <span>{resend.data.status === "NOT_CONFIGURED" ? "Email provider not configured (see Integrations)." : resend.data.status === "SENT" ? "Sent." : resend.data.status}</span>}
          </div>)}
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-ink-800 bg-ink-950/85 px-4 backdrop-blur sm:px-6">
          <button className="rounded-md p-1.5 text-ink-300 hover:bg-ink-800 lg:hidden" onClick={() => setMobile(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
          <div className="hidden min-w-0 flex-1 truncate text-sm text-ink-300 sm:block">Welcome back, <span className="font-semibold text-white">{me?.user.full_name.split(" ")[0]}</span></div>
          <div className="flex-1 sm:hidden" />
          <button onClick={() => nav("/integrations")} className={clsx("hidden items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold md:inline-flex",
            ppOk ? "border-mint-500/30 bg-mint-500/10 text-mint-300" : "border-ink-600 bg-ink-850 text-ink-300")}>
            <span className={clsx("h-1.5 w-1.5 rounded-full", ppOk ? "live-dot bg-mint-400" : pp?.credentials_configured ? "bg-amberx-400" : "bg-ink-400")} />
            PayPal {pp?.environment === "live" ? "Live" : "Sandbox"} · {ppOk ? "Connected" : pp?.credentials_configured ? "Not tested" : "Not connected"}
          </button>
          <button className="btn-ghost btn-sm" onClick={() => setCopilot(true)}><Sparkles className="h-3.5 w-3.5 text-volt-400" /><span className="hidden sm:inline">Ask Copilot</span></button>
          <button className="rounded-lg p-2 text-ink-300 hover:bg-ink-800 hover:text-white" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme">
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <button className="relative rounded-lg p-2 text-ink-300 hover:bg-ink-800 hover:text-white" onClick={() => nav("/notifications")} aria-label="Notifications">
            <Bell className="h-4 w-4" />
            {notes.data?.unread > 0 && <span className="num keep-white absolute -right-0.5 -top-0.5 rounded-full bg-rose-500 px-1 text-[9px] font-bold text-white">{notes.data.unread}</span>}
          </button>
          <div className="relative">
            <button className="flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-ink-800" onClick={() => setMenu((m) => !m)} aria-haspopup="menu" aria-expanded={menu}>
              <span className="keep-white flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-volt-500 to-mint-500 text-xs font-bold text-white">
                {me?.user.full_name.split(" ").map((x) => x[0]).slice(0, 2).join("")}
              </span>
              <span className="hidden text-left leading-tight md:block"><span className="block text-xs font-semibold text-white">{me?.merchant.name}</span><span className="block text-[10px] text-ink-400">{me?.user.role}</span></span>
              <ChevronDown className="h-3.5 w-3.5 text-ink-400" />
            </button>
            {menu && (
              <div className="absolute right-0 mt-2 w-52 rounded-lg border border-ink-700 bg-ink-900 p-1 shadow-xl" role="menu" onMouseLeave={() => setMenu(false)}>
                <button role="menuitem" className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-ink-200 hover:bg-ink-800" onClick={() => { setMenu(false); nav("/settings"); }}><Settings className="h-4 w-4" /> Settings</button>
                <button role="menuitem" className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-ink-200 hover:bg-ink-800" onClick={() => { setMenu(false); nav("/agent"); }}><Bot className="h-4 w-4" /> Automation</button>
                <button role="menuitem" className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-rose-400 hover:bg-ink-800" onClick={() => { signOut(); nav("/"); }}><LogOut className="h-4 w-4" /> Sign out</button>
              </div>)}
          </div>
        </header>
        <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 lg:py-8"><Outlet key={theme} /></main>
      </div>
      <Copilot open={copilot} onClose={() => setCopilot(false)} />
    </div>
  );
}

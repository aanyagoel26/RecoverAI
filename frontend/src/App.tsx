import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import { Audit, Notifications, Profile, SettingsPage } from "./pages/Admin";
import { Integrations } from "./pages/Integrations";
import { Agent } from "./pages/Agent";
import { Analytics } from "./pages/Analytics";
import { Checkout } from "./pages/Checkout";
import { Dashboard } from "./pages/Dashboard";
import { Mission } from "./pages/Mission";
import { PaymentDetail, Payments } from "./pages/Payments";
import { Communications, Customers } from "./pages/People";
import { ForgotPassword, Landing, Login, Onboarding, ResetPassword, Signup, VerifyEmail } from "./pages/Public";
import { Intelligence } from "./pages/Intelligence";
import { CopilotPage } from "./pages/CopilotPage";
import { Recovery } from "./pages/Recovery";
import { Simulator } from "./pages/Simulator";

function Protected({ children }: { children: JSX.Element }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Loading label="Signing in" />;
  if (!me) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return children;
}

function NotFound() {
  return <div className="flex min-h-[60vh] flex-col items-center justify-center text-center"><p className="num text-5xl text-ink-600">404</p><p className="mt-2 text-white">Page not found</p><a href="/" className="btn-ghost mt-4">Go home</a></div>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/pay/:token" element={<Checkout />} />
      <Route path="/onboarding" element={<Protected><Onboarding /></Protected>} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route path="/mission" element={<Mission />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/payments" element={<Payments />} />
        <Route path="/payments/:id" element={<PaymentDetail />} />
        <Route path="/recovery" element={<Recovery />} />
        <Route path="/agent" element={<Agent />} />
        <Route path="/intelligence" element={<Intelligence />} />
        <Route path="/copilot" element={<CopilotPage />} />
        <Route path="/simulator" element={<Simulator />} />
        <Route path="/customers" element={<Customers />} />
        <Route path="/communications" element={<Communications />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/audit" element={<Audit />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

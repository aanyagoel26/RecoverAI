import { CopilotPanel } from "../components/Copilot";
import { Card, PageHeader } from "../components/ui";

export function CopilotPage() {
  return (
    <div className="max-w-4xl">
      <PageHeader title="Copilot" sub="Ask about your payments, failures and recovery work. Answers come from your data through read-only tools; any action needs your confirmation." />
      <Card className="h-[70vh] overflow-hidden"><CopilotPanel /></Card>
    </div>);
}

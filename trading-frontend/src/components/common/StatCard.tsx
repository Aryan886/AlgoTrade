import type { ReactNode } from "react";

interface StatCardProps {
  label: string;
  value: ReactNode;
  tone?: "positive" | "negative";
  detail?: string;
}

export default function StatCard({ label, value, tone, detail }: StatCardProps) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value${tone ? ` ${tone}` : ""}`}>{value}</div>
      {detail ? <div className="helper-text">{detail}</div> : null}
    </div>
  );
}

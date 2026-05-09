interface StatusBadgeProps {
  label: string;
  tone?: "running" | "stopped" | "warning" | "neutral" | "profit" | "loss" | "open";
}

export default function StatusBadge({ label, tone = "neutral" }: StatusBadgeProps) {
  return <span className={`status-badge status-${tone}`}>{label}</span>;
}

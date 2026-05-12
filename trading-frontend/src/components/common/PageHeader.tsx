import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  subtitle: string;
  actions?: ReactNode;
}

export default function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div>
        <h2 className="page-title">{title}</h2>
        <p className="page-subtitle">{subtitle}</p>
      </div>
      {actions ? <div>{actions}</div> : null}
    </div>
  );
}

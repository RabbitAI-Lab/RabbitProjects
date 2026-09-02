const COLORS: Record<string, string> = {
  backlog: "#9ca3af", unstarted: "#9ca3af", started: "#3b82f6",
  completed: "#10b981", cancelled: "#6b7280",
};

export function StateBadge({ group, name }: { group: string; name: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-neutral-700">
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: COLORS[group] ?? "#9ca3af" }} />
      {name}
    </span>
  );
}

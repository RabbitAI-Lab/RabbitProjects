/** 依赖关系图（PROJ-004 §3.3——冻结原型 V-GRAPH/O2，Sprint-9）。
 *
 *  按项目分列的分层布局；连线按节点实测位置动态生成（评审修复同款思路：
 *  端点贴合卡片边缘，跨项目虚线 + 「外部」徽标；同项目实线）。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { PortfolioAPI, unwrap } from "../services/api";
import { Sidebar } from "../components/Sidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type GNode = { id: string; issue_key: string; name: string; project_id: string; identifier: string; state_group: string };
type GEdge = { source: string; target: string; relation_type: string; cross_project: boolean };

export default function PortfolioGraphPage() {
  const { workspaceSlug: ws, portfolioId } = useParams();
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [onlyCross, setOnlyCross] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const load = useCallback(async () => {
    if (!ws || !portfolioId) return;
    try {
      const data = unwrap<{ nodes: GNode[]; edges: GEdge[] }>(
        await PortfolioAPI.dependencyGraph(ws, portfolioId)) ?? { nodes: [], edges: [] };
      setNodes(data.nodes);
      setEdges(data.edges);
    } catch {
      toast("加载依赖图失败", "error");
    }
  }, [ws, portfolioId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader
  useEffect(() => { load(); }, [load]);

  // 连线重画：节点挂载/窗口变化后按实测位置生成（数据变更亦触发）
  useEffect(() => {
    const draw = () => {
      const wrap = wrapRef.current, svg = svgRef.current;
      if (!wrap || !svg) return;
      const wrapRect = wrap.getBoundingClientRect();
      const pos: Record<string, { x: number; y: number; w: number; h: number }> = {};
      wrap.querySelectorAll<HTMLElement>("[data-gnode]").forEach((el) => {
        const r = el.getBoundingClientRect();
        pos[el.dataset.gnode!] = { x: r.left - wrapRect.left, y: r.top - wrapRect.top, w: r.width, h: r.height };
      });
      const g = svg.querySelector("#g-edges");
      if (!g) return;
      g.innerHTML = "";
      const NS = "http://www.w3.org/2000/svg";
      for (const e of edges) {
        if (onlyCross && !e.cross_project) continue;
        const a = pos[e.source], b = pos[e.target];
        if (!a || !b) continue;
        let d: string;
        const sameColumn = Math.abs((a.x + a.w / 2) - (b.x + b.w / 2)) < 8;
        if (sameColumn) {          // 列内垂直
          const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y - 5.5;
          const cy = (y2 - y1) / 2;
          d = `M${x1},${y1} C${x1},${y1 + cy} ${x2},${y2 - cy} ${x2},${y2}`;
        } else {                    // 跨列水平
          const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x - 5.5, y2 = b.y + b.h / 2;
          const cx = (x2 - x1) / 2;
          d = `M${x1},${y1} C${x1 + cx},${y1} ${x2 - cx},${y2} ${x2},${y2}`;
        }
        const path = document.createElementNS(NS, "path");
        path.setAttribute("d", d);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", e.cross_project ? "#b45309" : "#8c8c8c");
        path.setAttribute("stroke-width", e.cross_project ? "1.6" : "1.4");
        if (e.cross_project) path.setAttribute("stroke-dasharray", "6 4");
        path.setAttribute("marker-end", e.cross_project ? "url(#garrx)" : "url(#garr)");
        g.appendChild(path);
        if (e.cross_project) {
          const pa = pos[e.source]!, pb = pos[e.target]!;
          const mx = (pa.x + pa.w + pb.x) / 2;
          const my = (pa.y + pb.y + pa.h / 2) / 2;
          const t = document.createElementNS(NS, "text");
          t.setAttribute("x", String(mx)); t.setAttribute("y", String(my + 3.5));
          t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "10.5");
          t.setAttribute("fill", "#b45309");
          t.setAttribute("style", "paint-order:stroke;stroke:#fff;stroke-width:8px");
          t.textContent = "外部";
          g.appendChild(t);
        }
      }
    };
    const id = requestAnimationFrame(() => requestAnimationFrame(draw));
    window.addEventListener("resize", draw);
    return () => { cancelAnimationFrame(id); window.removeEventListener("resize", draw); };
  }, [edges, onlyCross, nodes.length]);  // nodes 仅作重挂载触发（length 变化即重画）

  const byProject = new Map<string, GNode[]>();
  for (const n of nodes) {
    const list = byProject.get(n.identifier) ?? [];
    list.push(n);
    byProject.set(n.identifier, list);
  }
  const colors = ["#3f76ff", "#8b5cf6", "#f59e0b", "#10b981", "#ec4899"];

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-pf-graph">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">依赖关系图</div>
                <div className="text-[12.5px] text-neutral-400">
                  仅跨项目边 + 关键同项目边 · {nodes.length} 节点 / {edges.length} 边（门禁 ≤100/500）
                </div>
              </div>
              <div className="ml-auto flex gap-2">
                <Link to={`/${ws}/portfolios/${portfolioId}`} className="btn-ghost h-[30px] px-3 text-[12px]">汇总面板</Link>
                <button className={`btn-ghost h-[30px] px-3 text-[12px] ${onlyCross ? "!border-amber-300 !text-amber-700" : ""}`}
                  onClick={() => setOnlyCross((v) => !v)}>仅看跨项目边</button>
              </div>
            </div>

            <div className="card relative overflow-auto" ref={wrapRef} data-sb-scope="pf-graph-wrap">
              {nodes.length === 0 && (
                <div className="p-10 text-center text-[12.5px] text-neutral-400" data-sb-scope="pf-graph-empty">
                  暂无依赖边——在任务详情建立跨项目关联后展示
                </div>
              )}
              <div className="grid gap-10 p-4" style={{ gridTemplateColumns: `repeat(${Math.max(byProject.size, 1)}, 250px)` }}>
                {[...byProject.entries()].map(([identifier, list], ci) => (
                  <div key={identifier}>
                    <div className="mb-2.5 flex items-center gap-1.5 text-[12.5px] font-semibold">
                      <span className="h-2 w-2 rounded-full" style={{ background: colors[ci % colors.length] }} />
                      {identifier}
                    </div>
                    <div className="flex flex-col gap-4">
                      {list.map((n) => (
                        <div key={n.id} data-gnode={n.id} className="relative rounded-lg border border-neutral-300 bg-white p-2 shadow-sm">
                          <span className="absolute -top-2 right-2 rounded-full px-1.5 text-[10.5px] text-white" style={{ background: colors[ci % colors.length] }}>
                            {n.identifier}
                          </span>
                          <div className="font-mono text-[11px] text-neutral-500">{n.issue_key}</div>
                          <div className="mt-0.5 text-[12.5px]">{n.name}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <svg ref={svgRef} className="pointer-events-none absolute inset-0 h-full w-full">
                <defs>
                  <marker id="garr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#8c8c8c" /></marker>
                  <marker id="garrx" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#b45309" /></marker>
                </defs>
                <g id="g-edges" />
              </svg>
              {nodes.length > 0 && (
                <div className="flex flex-wrap gap-4 border-t border-neutral-200 px-4 py-2.5 text-[12px] text-neutral-500">
                  <span className="flex items-center gap-1.5">
                    <svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="#b45309" strokeWidth="1.6" strokeDasharray="6 4" /></svg>
                    跨项目边（虚线 + 外部）
                  </span>
                  <span className="flex items-center gap-1.5">
                    <svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="#8c8c8c" strokeWidth="1.4" /></svg>
                    同项目边
                  </span>
                  <span className="text-neutral-400">跨项目边不拦截完成（BR-07 软策略）</span>
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

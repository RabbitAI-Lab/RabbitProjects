/** 依赖关系图（PROJ-004 §3.3——冻结原型 V-GRAPH/O2；走查修复 2026-09-10 布局美化）。
 *
 *  按项目分列的分层布局；列内节点按拓扑层排序（被依赖者靠上，减少连线交叉）；
 *  连线按节点实测位置动态生成（跨项目虚线 + 「外部」胶囊徽标；同项目实线）——
 *  控制点水平分量加大使曲线扁平化，降低穿越中列的视觉密度。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { PortfolioAPI, ProjectAPI, unwrap } from "../services/api";
import { Sidebar } from "../components/Sidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type GNode = { id: string; issue_key: string; name: string; project_id: string; identifier: string; state_group: string };
type GEdge = { source: string; target: string; relation_type: string; cross_project: boolean };
type ProjLite = { id: string; name: string; identifier: string };

export default function PortfolioGraphPage() {
  const { workspaceSlug: ws, portfolioId } = useParams();
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [projects, setProjects] = useState<ProjLite[]>([]);
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
      const p = unwrap<ProjLite[]>(await ProjectAPI.listByWs(ws, { status: "all" }).catch(() => null));
      setProjects(p ?? []);
    } catch {
      toast("加载依赖图失败", "error");
    }
  }, [ws, portfolioId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
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
        let d: string, mx: number, my: number;
        const sameColumn = Math.abs((a.x + a.w / 2) - (b.x + b.w / 2)) < 8;
        if (sameColumn) {          // 列内垂直
          const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y - 5.5;
          const cy = (y2 - y1) / 2;
          d = `M${x1},${y1} C${x1},${y1 + cy} ${x2},${y2 - cy} ${x2},${y2}`;
          mx = x1; my = (y1 + y2) / 2;
        } else {                    // 跨列水平：扁平 S 弯（控制点水平分量 0.72——降低中段摆幅）
          const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x - 5.5, y2 = b.y + b.h / 2;
          const cx = (x2 - x1) * 0.72;
          d = `M${x1},${y1} C${x1 + cx},${y1} ${x2 - cx},${y2} ${x2},${y2}`;
          mx = (x1 + x2) / 2; my = (y1 + y2) / 2;
        }
        const path = document.createElementNS(NS, "path");
        path.setAttribute("d", d);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", e.cross_project ? "#b45309" : "#8c8c8c");
        path.setAttribute("stroke-width", e.cross_project ? "1.6" : "1.4");
        if (e.cross_project) path.setAttribute("stroke-dasharray", "6 4");
        path.setAttribute("marker-end", e.cross_project ? "url(#garrx)" : "url(#garr)");
        g.appendChild(path);
        if (e.cross_project) {     // 「外部」胶囊徽标（白底 + amber 描边，盖住线段中点）
          const box = document.createElementNS(NS, "rect");
          box.setAttribute("x", String(mx - 20)); box.setAttribute("y", String(my - 10));
          box.setAttribute("width", "40"); box.setAttribute("height", "18");
          box.setAttribute("rx", "9");
          box.setAttribute("fill", "#fffbeb");
          box.setAttribute("stroke", "#fde68a");
          box.setAttribute("stroke-width", "1");
          g.appendChild(box);
          const t = document.createElementNS(NS, "text");
          t.setAttribute("x", String(mx)); t.setAttribute("y", String(my + 3.5));
          t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "10.5");
          t.setAttribute("fill", "#b45309");
          t.textContent = "外部";
          g.appendChild(t);
        }
      }
    };
    const id = requestAnimationFrame(() => requestAnimationFrame(draw));
    window.addEventListener("resize", draw);
    return () => { cancelAnimationFrame(id); window.removeEventListener("resize", draw); };
  }, [nodes, edges, onlyCross]);

  // 列内排序：按拓扑层（被依赖次数多者靠上），减少连线交叉
  const ordered = (list: GNode[], es: GEdge[]): GNode[] => {
    const asSource = new Map<string, number>();
    const asTarget = new Map<string, number>();
    for (const e of es) {
      asSource.set(e.source, (asSource.get(e.source) ?? 0) + 1);
      asTarget.set(e.target, (asTarget.get(e.target) ?? 0) + 1);
    }
    return [...list].sort((a, b) => {
      const layerA = (asTarget.get(a.id) ?? 0) * 2 - (asSource.get(a.id) ?? 0);
      const layerB = (asTarget.get(b.id) ?? 0) * 2 - (asSource.get(b.id) ?? 0);
      return layerA - layerB;
    });
  };

  const byProject = new Map<string, { proj: ProjLite | undefined; list: GNode[] }>();
  for (const n of nodes) {
    const entry = byProject.get(n.identifier) ?? { proj: projects.find((p) => p.id === n.project_id), list: [] };
    entry.list.push(n);
    byProject.set(n.identifier, entry);
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
              <div className="grid gap-14 p-5" style={{ gridTemplateColumns: `repeat(${Math.max(byProject.size, 1)}, minmax(200px, 240px))` }}>
                {[...byProject.entries()].map(([identifier, { proj, list }], ci) => (
                  <div key={identifier}>
                    {/* 列头：色条 + 项目名 + identifier 徽标（走查修复：原只有编号） */}
                    <div className="mb-3 flex items-center gap-2 border-b-2 pb-1.5" style={{ borderColor: colors[ci % colors.length] }}>
                      <span className="h-2.5 w-2.5 rounded-full" style={{ background: colors[ci % colors.length] }} />
                      <span className="truncate text-[13px] font-semibold">{proj?.name ?? identifier}</span>
                      <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[10.5px] text-neutral-500">{identifier}</span>
                    </div>
                    <div className="flex min-h-[120px] flex-col justify-start gap-6">
                      {ordered(list, edges).map((n) => (
                        <div key={n.id} data-gnode={n.id} className="relative rounded-lg border border-neutral-300 bg-white p-2.5 shadow-sm transition-shadow hover:shadow-md">
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
                <div className="flex flex-wrap gap-4 border-t border-neutral-200 px-5 py-2.5 text-[12px] text-neutral-500">
                  <span className="flex items-center gap-1.5">
                    <svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="#b45309" strokeWidth="1.6" strokeDasharray="6 4" /></svg>
                    跨项目边（虚线 + 外部）
                  </span>
                  <span className="flex items-center gap-1.5">
                    <svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="#8c8c8c" strokeWidth="1.4" /></svg>
                    同项目边
                  </span>
                  <span className="ml-auto text-neutral-400">跨项目边不拦截完成（BR-07 软策略）</span>
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

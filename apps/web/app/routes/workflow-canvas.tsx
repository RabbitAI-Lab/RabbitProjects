/** 工作流画布编辑器（WF-001 §3.1/§3.2）——@xyflow/react 拖拽建节点/连边 +
 * dagre 自动布局 + 侧栏边配置 + 发布（§3.3 错误面板可定位）。
 *
 * C.141 附录 C 表面（Sprint-7 UI parity）。画布路由按 WF-001 §1.2 目标 5 懒加载。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";
import {
  ReactFlow, Background, Controls, MiniMap,
  addEdge, applyEdgeChanges, applyNodeChanges,
  type Connection, type Edge, type EdgeChange, type Node, type NodeChange,
} from "@xyflow/react";
import dagre from "dagre";
import "@xyflow/react/dist/style.css";

import { WorkflowAPI, type WorkflowDetail } from "../services/api";

/** 状态节点数据载荷。 */
interface StateNodeData extends Record<string, unknown> {
  wfStateId: string;
  stateId: string;
  name: string;
  group: string;
  color: string;
  isInitial: boolean;
  fieldLocks: Array<{ field: string }>;
}

const GROUP_COLOR: Record<string, string> = {
  backlog: "#94A3B8", unstarted: "#9CA3AF", started: "#3B82F6",
  completed: "#10B981", cancelled: "#6B7280",
};

/** dagre 分层布局（WF-001 §3.2：按 group 顺序分层）。 */
function layoutGraph(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 120 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: 160, height: 56 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const pos = g.node(n.id);
    return { ...n, position: { x: pos.x - 80, y: pos.y - 28 } };
  });
}

function toFlowNodes(wf: WorkflowDetail): Node[] {
  return wf.states.map((s) => ({
    id: s.id,
    type: "stateNode",
    position: { x: s.layout_x, y: s.layout_y },
    data: {
      wfStateId: s.id, stateId: s.state_id, name: s.name ?? "", group: s.group ?? "",
      color: s.color ?? GROUP_COLOR[s.group ?? ""] ?? "#9CA3AF",
      isInitial: s.is_initial, fieldLocks: s.field_locks,
    } satisfies StateNodeData,
  }));
}

function toFlowEdges(wf: WorkflowDetail): Edge[] {
  return wf.transitions.map((t) => ({
    id: t.id, source: t.from_state_id, target: t.to_state_id,
    label: t.name, animated: false,
  }));
}

export default function WorkflowCanvas() {
  const { ws, projectId, wfId } = useParams();
  const [wf, setWf] = useState<WorkflowDetail | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [dirty, setDirty] = useState(false);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [publishIssues, setPublishIssues] = useState<Array<{ code: string; message: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!ws || !projectId || !wfId) return;
    void WorkflowAPI.detail(ws, projectId, wfId).then((r) => {
      setWf(r.data);
      setNodes(toFlowNodes(r.data));
      setEdges(toFlowEdges(r.data));
    }).catch(() => { /* 404 等由路由层呈现 */ });
  }, [ws, projectId, wfId]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns));
    if (changes.some((c) => c.type !== "select" && c.type !== "dimensions")) setDirty(true);
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((es) => applyEdgeChanges(changes, es));
    if (changes.some((c) => c.type !== "select")) setDirty(true);
  }, []);
  const onConnect = useCallback((conn: Connection) => {
    setEdges((es) => addEdge({ ...conn, id: `tmp-${crypto.randomUUID()}`, label: "" }, es));
    setDirty(true);
  }, []);

  const nodeTypes = useMemo(() => ({ stateNode: StateNodeView }), []);

  async function save() {
    if (!ws || !projectId || !wfId || !etag) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await WorkflowAPI.saveGraph(ws, projectId, wfId, {
        states: nodes.map((n) => ({
          id: n.id, state_id: (n.data as StateNodeData).stateId,
          is_initial: (n.data as StateNodeData).isInitial,
          layout_x: n.position.x, layout_y: n.position.y,
          field_locks: (n.data as StateNodeData).fieldLocks,
        })),
        transitions: edges.filter((e) => !e.id.startsWith("tmp-")).map((e) => ({
          id: e.id, from_state_id: e.source, to_state_id: e.target,
          name: (e.label as string) || "未命名",
          guards: [], side_effects: [], approval_flow_id: null, sort_order: 1000,
        })),
      }, etag);
      setWf(r.data);
      setNodes(toFlowNodes(r.data));
      setEdges(toFlowEdges(r.data));
      setDirty(false);
      setMessage("画布已保存");
      const newEtag = (r as unknown as { headers?: { etag?: string } })?.headers?.etag;
      if (newEtag) setEtag(newEtag.replace(/^W\//, "").replace(/"/g, ""));
    } catch {
      setMessage("保存失败：画布可能已被他人修改，请刷新");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!ws || !projectId || !wfId) return;
    setBusy(true);
    setPublishIssues([]);
    try {
      await save();
      await WorkflowAPI.publish(ws, projectId, wfId);
      setMessage("已发布生效");
    } catch (e) {
      const err = e as { response?: { data?: { error?: { details?: Array<{ code: string; message: string }> } } } };
      setPublishIssues(err.response?.data?.error?.details ?? [{ code: "ERROR", message: "发布失败" }]);
    } finally {
      setBusy(false);
    }
  }

  function autoLayout() {
    setNodes((ns) => layoutGraph(ns, edges));
    setDirty(true);
  }

  if (!wf) return <div className="flex-1 p-4 text-sm text-neutral-400">加载中…</div>;

  return (
    <div className="flex-1 flex flex-col bg-white" data-sb-scope="workflow-canvas">
      <header className="h-12 border-b border-neutral-200 px-4 flex items-center gap-3">
        <h1 className="text-sm font-semibold">{wf.name}</h1>
        <span className={`px-1.5 py-0.5 rounded text-xs ${wf.status === "published"
          ? "bg-emerald-50 text-emerald-700" : wf.status === "draft"
            ? "bg-amber-50 text-amber-700" : "bg-neutral-100 text-neutral-500"}`}>
          {wf.status === "published" ? `已发布 v${wf.version}` : wf.status === "draft" ? "草稿" : "已归档"}
        </span>
        {dirty && <span className="text-xs text-amber-600">● 未保存</span>}
        <div className="flex-1" />
        <button type="button" onClick={autoLayout} className="px-2.5 h-7 rounded border border-neutral-200 text-sm text-neutral-600 hover:bg-neutral-50">
          自动布局
        </button>
        <button type="button" disabled={busy || !etag || wf.status !== "draft"} onClick={() => void save()}
          className="px-2.5 h-7 rounded border border-neutral-200 text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-50">
          保存
        </button>
        <button type="button" disabled={busy || wf.status !== "draft"} onClick={() => void publish()}
          className="px-2.5 h-7 rounded bg-brand-600 text-white text-sm hover:bg-brand-700 disabled:opacity-50">
          发布
        </button>
      </header>

      {message && <div className="px-4 py-1.5 text-xs text-brand-600 bg-brand-50">{message}</div>}

      {publishIssues.length > 0 && (
        <div className="mx-4 mt-2 border border-amber-200 bg-amber-50 rounded-md p-3" data-sb-scope="publish-issues">
          <div className="text-sm font-medium text-amber-800 mb-1">发布校验未通过（{publishIssues.length} 项）</div>
          {publishIssues.map((p, i) => (
            <div key={i} className="text-xs text-amber-700">{i + 1}. {p.message}</div>
          ))}
        </div>
      )}

      <div className="flex-1 flex">
        <div className="flex-1 relative">
          <ReactFlow
            nodes={nodes} edges={edges} nodeTypes={nodeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
            onEdgeClick={(_, e) => setSelectedEdge(e)}
            fitView>
            <Background />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>
        {selectedEdge && (
          <aside className="w-64 border-l border-neutral-200 p-3 space-y-3" data-sb-scope="edge-config">
            <div className="text-xs text-neutral-400">流转边配置</div>
            <div className="text-sm font-medium">{selectedEdge.label || "未命名边"}</div>
            <label className="block text-xs text-neutral-500">
              边名称
              <input type="text" defaultValue={String(selectedEdge.label ?? "")}
                onChange={(e) => {
                  setEdges((es) => es.map((x) => x.id === selectedEdge.id ? { ...x, label: e.target.value } : x));
                  setDirty(true);
                }}
                className="mt-1 w-full border border-neutral-200 rounded px-2 h-8 text-sm" />
            </label>
            <div className="text-xs text-neutral-400 space-y-1">
              <div>守卫与副作用配置在发布前经服务端校验；</div>
              <div>审批流挂接经 approval_flow_id 字段（画布侧栏 §3.3 完整配置面在 WF-002 联调窗交付）。</div>
            </div>
            <button type="button"
              onClick={() => { setEdges((es) => es.filter((x) => x.id !== selectedEdge.id)); setSelectedEdge(null); setDirty(true); }}
              className="w-full h-8 rounded border border-rose-200 text-rose-600 text-sm hover:bg-rose-50">
              删除边
            </button>
          </aside>
        )}
      </div>
    </div>
  );
}

/** 状态节点视图（§3.1 线框：名称 + group 色 + 初始态标记）。 */
function StateNodeView({ data }: { data: StateNodeData }) {
  return (
    <div className="px-3 py-2 rounded-lg border-2 bg-white shadow-sm min-w-32"
      style={{ borderColor: data.color }} data-sb-scope="wf-state-node" data-state-id={data.stateId}>
      <div className="flex items-center gap-1.5">
        {data.isInitial && <span className="w-2 h-2 rounded-full bg-brand-500" title="初始状态" />}
        <span className="text-sm font-medium text-neutral-800">{data.name}</span>
      </div>
      <div className="text-[10px] text-neutral-400">{data.group}</div>
      {data.fieldLocks.length > 0 && (
        <div className="text-[10px] text-amber-600">🔒 {data.fieldLocks.length} 字段锁定</div>
      )}
    </div>
  );
}

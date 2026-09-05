import { useEffect, useRef, useState } from "react";
import { ProjectAPI, IssueAPI } from "../services/api";
import { useStores } from "../stores";

interface StateOpt { id: string; name: string; color: string; group: string; is_default: boolean }

/** 创建任务 Modal（高保真 TASK-001 §3.2.2）—— 看板与任务列表共用。
 *  规格：640px；标题「创建任务 · 项目名」；无边框大字号标题输入；
 *  描述编辑器（工具条 + contenteditable，工具条为装饰外壳——真实 TipTap 命令 P0 后续接入，见 sprint-overview §1.1 注意项 2）；
 *  状态下拉（圆点+名称，含已取消）；负责人下拉（指派给我）；截止时间（今天/明天/下周快捷）；
 *  「创建后继续创建下一个」；⌘/Ctrl+Enter 提交；Esc 关闭。 */
export function NewTaskModal({ slug, projectId, projectName, onClose, onCreated }: {
  slug: string; projectId: string; projectName: string; onClose: () => void; onCreated?: () => void;
}) {
  const { session } = useStores();
  const [states, setStates] = useState<StateOpt[]>([]);
  const [stateId, setStateId] = useState<string>("");
  const [name, setName] = useState("");
  const [descHtml, setDescHtml] = useState("");
  const [assignMe, setAssignMe] = useState(false);
  const [targetDate, setTargetDate] = useState("");
  const [keepOpen, setKeepOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openMenu, setOpenMenu] = useState<"state" | "assignee" | null>(null);
  const descRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ProjectAPI.states(slug, projectId, { include_cancelled: "1" }).then((r) => {
      const list = ((r as any).data ?? []) as StateOpt[];
      setStates(list);
      const def = list.find((x) => x.is_default) ?? list[0];
      if (def) setStateId(def.id);
    });
  }, [slug, projectId]);

  const cur = states.find((x) => x.id === stateId);
  const fmt = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const quick = {
    今天: fmt(new Date()),
    明天: fmt(new Date(Date.now() + 86400000)),
    下周: fmt(new Date(Date.now() + 7 * 86400000)),
  };

  async function submit() {
    if (loading) return;
    setErr(null);
    if (!name.trim()) { setErr("请填写任务标题"); return; }
    setLoading(true);
    try {
      await IssueAPI.create(slug, projectId, {
        name,
        ...(descHtml ? { description_html: descHtml } : {}),
        ...(stateId ? { state_id: stateId } : {}),
        ...(assignMe && session.user ? { assignee_ids: [session.user.id] } : {}),
        ...(targetDate ? { target_date: targetDate } : {}),
      });
      onCreated?.();
      if (keepOpen) { setName(""); setDescHtml(""); if (descRef.current) descRef.current.innerHTML = ""; setTargetDate(""); setAssignMe(false); setLoading(false); }
      else onClose();
    } catch (e: any) { setErr(e?.message ?? "创建失败"); setLoading(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.stopPropagation(); onClose(); }
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); submit(); }
      }}>
      <div className="bg-white rounded-xl shadow-lg w-[640px] max-w-full max-h-[88vh] overflow-y-auto p-6" role="dialog" aria-modal="true">
        <div className="flex items-center justify-between mb-[18px]">
          <div className="text-base font-semibold">创建任务 · {projectName}</div>
          <button onClick={onClose} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        {err && <div className="mb-3.5 px-3 py-2 bg-red-50 text-red-700 rounded-md text-[13px]">{err}</div>}
        <input
          className="w-full h-10 text-[17px] font-medium border-0 border-b-2 border-transparent focus:border-brand-500 focus:outline-none bg-transparent px-0 mb-3 disabled:opacity-50"
          placeholder="任务标题" autoFocus value={name} onChange={(e) => setName(e.target.value)}
          // 续创建模式下，请求返回后 submit() 会 setName("") 清空标题。若在请求飞行期间
          // 允许输入，用户刚敲进去的下一个标题会被静默清掉（表现：按钮变灰、点了没反应）。
          // 与提交按钮一致地在发送期间禁用，语义才可预测。
          disabled={loading}
        />
        <div className="border border-neutral-200 rounded-lg">
          <div className="flex gap-0.5 px-2 py-1.5 border-b border-neutral-200 bg-neutral-50 rounded-t-lg text-[13px] text-neutral-500 select-none" aria-hidden>
            {["B", "I", "U", "≡", "☰", "⌗", "</>", "🔗"].map((t, i) => (
              <span key={i} className="min-w-[26px] h-6 inline-flex items-center justify-center rounded px-1"
                style={t === "B" ? { fontWeight: 700 } : t === "I" ? { fontStyle: "italic" } : t === "U" ? { textDecoration: "underline" } : undefined}>{t}</span>
            ))}
          </div>
          <div ref={descRef} contentEditable suppressContentEditableWarning
            className="min-h-[110px] p-2.5 text-[13px] leading-relaxed outline-none empty:before:content-[attr(data-ph)] before:text-neutral-400"
            data-ph="添加描述…" onInput={(e) => setDescHtml((e.target as HTMLDivElement).innerHTML)} />
        </div>

        <div className="flex gap-2.5 mt-4 flex-wrap">
          {/* 状态下拉（圆点+名称，含已取消） */}
          <div className="relative">
            <button type="button" onClick={() => setOpenMenu(openMenu === "state" ? null : "state")}
              className="h-8 border border-neutral-300 rounded-md px-2.5 inline-flex items-center gap-2 text-[13px] bg-white hover:bg-neutral-50 min-w-[110px]">
              {cur ? <><span className="w-1.5 h-1.5 rounded-full" style={{ background: cur.color }} />{cur.name}</> : "待办"}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
            </button>
            {openMenu === "state" && (
              <div className="absolute top-9 left-0 z-10 min-w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                {states.map((st) => (
                  <button key={st.id} type="button"
                    className={`w-full text-left px-3 h-8 inline-flex items-center gap-2 text-[13px] hover:bg-neutral-50 ${st.id === stateId ? "bg-brand-50 text-brand-600" : ""}`}
                    onClick={() => { setStateId(st.id); setOpenMenu(null); }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: st.color }} />{st.name}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* 负责人下拉（P0 单成员：指派给我 / 未分配） */}
          <div className="relative">
            <button type="button" onClick={() => setOpenMenu(openMenu === "assignee" ? null : "assignee")}
              className="h-8 border border-neutral-300 rounded-md px-2.5 inline-flex items-center gap-2 text-[13px] bg-white hover:bg-neutral-50 min-w-[110px]">
              {assignMe ? `👤 ${session.user?.display_name ?? "我"}` : "👤 未分配"}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
            </button>
            {openMenu === "assignee" && (
              <div className="absolute top-9 left-0 z-10 min-w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                <button type="button" className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50"
                  onClick={() => { setAssignMe(true); setOpenMenu(null); }}>指派给我（{session.user?.display_name ?? "我"}）</button>
                <button type="button" className="w-full text-left px-3 h-8 text-[13px] text-neutral-400 hover:bg-neutral-50"
                  onClick={() => { setAssignMe(false); setOpenMenu(null); }}>未分配</button>
              </div>
            )}
          </div>
          {/* 截止时间 + 快捷项 */}
          <div className="inline-flex items-center gap-1.5">
            <input type="date" className="h-8 border border-neutral-300 rounded-md px-2 text-[13px] bg-white" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />
            {(Object.keys(quick) as Array<keyof typeof quick>).map((k) => (
              <button key={k} type="button" onClick={() => setTargetDate(quick[k])}
                className={`h-7 px-2 rounded-md text-xs border ${targetDate === quick[k] ? "bg-brand-50 text-brand-600 border-brand-100" : "border-neutral-200 text-neutral-500 hover:bg-neutral-50"}`}>{k}</button>
            ))}
          </div>
        </div>

        <label className="flex items-center gap-2 text-[13px] text-neutral-700 cursor-pointer mt-4">
          <input type="checkbox" checked={keepOpen} onChange={(e) => setKeepOpen(e.target.checked)} className="accent-brand-500" />
          创建后继续创建下一个
        </label>

        <div className="flex justify-end gap-2.5 mt-5">
          <button type="button" onClick={onClose} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">取消</button>
          <button type="button" onClick={submit} disabled={loading || !name.trim()} data-testid="create-task-submit"
            className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50 inline-flex items-center gap-2">
            {loading ? "创建中…" : <>创建任务 <span className="opacity-70 text-[11px]">⌘↵</span></>}
          </button>
        </div>
      </div>
    </div>
  );
}

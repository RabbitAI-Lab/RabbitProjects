/** Wiki 回收站（FILE-005 §3.4——软删整树 30 天倒计时 + 整树恢复，BR-09）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { WikiAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type TrashRow = { id: string; title: string; space_id: string; deleted_by: string | null; deleted_at: string };

export default function WikiTrashPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [rows, setRows] = useState<TrashRow[]>([]);
  const [loadedAt, setLoadedAt] = useState(() => Date.now());

  const load = useCallback(async () => {
    if (!ws) return;
    setRows(unwrap<TrashRow[]>(await WikiAPI.trash(ws).catch(() => null)) ?? []);
    setLoadedAt(Date.now());
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => { load(); }, [load]);

  const restore = async (r: TrashRow) => {
    if (!ws) return;
    try {
      await WikiAPI.restorePage(ws, r.id);
      toast(`「${r.title}」已整树恢复`, "ok");
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "恢复失败", "error"); }
  };

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-wiki-trash">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="WIKI" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">Wiki 回收站</div>
                <div className="text-[12.5px] text-neutral-400">软删整树 · 30 天倒计时（期满由每日任务硬删）· 非管理员仅见本人删除项（BR-09）</div>
              </div>
              <Link to={`/${ws}/projects/${projectId}/wiki`} className="btn-ghost ml-auto h-[30px] px-3 text-[12px]">返回 Wiki</Link>
            </div>
            <div className="card" data-sb-scope="wiki-trash-list">
              {rows.length === 0 && <div className="p-10 text-center text-[12.5px] text-neutral-400">回收站为空</div>}
              {rows.map((r) => {
                const daysLeft = 30 - Math.floor((loadedAt - Date.parse(r.deleted_at)) / 86400000);
                return (
                  <div key={r.id} className="flex items-center gap-3 border-b border-neutral-200 px-4 py-3 text-[13px] last:border-b-0">
                    <span className="font-medium">{r.title}</span>
                    <span className="font-mono text-[12px] text-neutral-400">{r.deleted_at.slice(0, 16).replace("T", " ")}</span>
                    <span className={`text-[12px] ${daysLeft <= 7 ? "text-red-500" : "text-neutral-400"}`}>剩 {Math.max(daysLeft, 0)} 天</span>
                    <button className="btn-ghost ml-auto h-[26px] px-2 text-[11.5px]" onClick={() => restore(r)}>整树恢复</button>
                  </div>
                );
              })}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

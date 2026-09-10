import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { FileLibrary, useProjectHeader } from "../components/files/FileLibrary";
import { useStores } from "../stores";
import { usePermissionSync } from "../components/PermissionGate";

/** Sprint-4（FILE-002 §3.1 / C.112）：项目文件库页——左树右表双视图 + 预签名直传。
 *  权限差异演出：canUpload（file.upload CONTRIBUTOR+）/ isAdmin（file.permission.manage
 *  仅 ADMIN）——快照未 hydrate 时 fail-open（写边界在后端，gantt.tsx 同口径）。 */
export default function Files() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const stores = useStores();
  usePermissionSync(); // hydrated/角色随快照晚到翻正（见 PermissionGate.tsx）
  const myRole = stores.permission.effectiveProjectRole(projectId, workspaceSlug);
  const hydrated = stores.permission.snapshot !== null;
  const canUpload = !hydrated || myRole >= 15; // ProjectRole.CONTRIBUTOR
  const isAdmin = !hydrated || myRole >= 20;   // ProjectRole.ADMIN
  const { name, identifier } = useProjectHeader(workspaceSlug, projectId);
  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={name} identifier={identifier} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <FileLibrary slug={workspaceSlug!} projectId={projectId!} canUpload={canUpload} isAdmin={isAdmin} />
        </main>
      </div>
    </div>
  );
}

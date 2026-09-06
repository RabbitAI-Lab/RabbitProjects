import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { FileTrash, useProjectHeader } from "../components/files/FileLibrary";
import { useStores } from "../stores";

/** Sprint-4（FILE-002 §3.1 / C.117）：回收站页——R1 口径列表 + 还原 + 彻底删除（仅 ADMIN）。 */
export default function FilesTrash() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const stores = useStores();
  const myRole = stores.permission.effectiveProjectRole(projectId, workspaceSlug);
  const hydrated = stores.permission.snapshot !== null;
  const canUpload = !hydrated || myRole >= 15; // file.delete CONTRIBUTOR+（还原入口）
  const isAdmin = !hydrated || myRole >= 20;   // 彻底删除仅 ADMIN（§4.2 #13）
  const { name, identifier } = useProjectHeader(workspaceSlug, projectId);
  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={name} identifier={identifier} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <FileTrash slug={workspaceSlug!} projectId={projectId!} canUpload={canUpload} isAdmin={isAdmin} />
        </main>
      </div>
    </div>
  );
}

import { useEffect } from "react";
import { useNavigate, useParams } from "react-router";

export default function ProjectHome() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const nav = useNavigate();
  useEffect(() => { nav(`/${workspaceSlug}/projects/${projectId}/board`); }, [workspaceSlug, projectId]);
  return null;
}

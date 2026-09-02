import { useEffect } from "react";
import { useNavigate, useParams } from "react-router";

export default function ProjectNew() {
  const { workspaceSlug } = useParams<{ workspaceSlug: string }>();
  const nav = useNavigate();
  useEffect(() => { nav(`/${workspaceSlug}/projects`); }, [workspaceSlug]);
  return null;
}

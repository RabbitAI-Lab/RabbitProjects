import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import Link from "@tiptap/extension-link";
import type { Extensions } from "@tiptap/core";

/**
 * P0 基础模式：不启用 Collaboration / CollaborationCursor（无 Yjs、无 WebSocket）。
 * 扩展集合刻意与 P2 协同模式保持同一份 schema，
 * 使 P2 接入 Hocuspocus 时不需要迁移已有文档结构。
 */
export const BASIC_EXTENSIONS: Extensions = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    codeBlock: {},
    // P2 协同模式需关闭 history（由 Yjs UndoManager 接管），P0 保留
    history: {},
  }),
  Placeholder.configure({ placeholder: "添加描述…" }),
  Link.configure({ openOnClick: false, autolink: true }),
];

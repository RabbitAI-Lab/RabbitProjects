-- PROJ-003 内置 3 模板种子（幂等，逐行 NOT EXISTS 守卫；dev 库重放无副作用）
INSERT INTO project_templates (id, created_at, updated_at, workspace_id,
                               name, description, is_builtin,
                               states_snapshot, labels_snapshot, fields_snapshot, folders_snapshot)
SELECT gen_random_uuid(), now(), now(), NULL::uuid,
       '基础模板', '空白项目：默认状态四件 + 常用标签', TRUE,
       '[
 {
  "name": "待办",
  "color": "#9CA3AF",
  "group": "unstarted",
  "is_default": true,
  "sort_order": 65535.0
 },
 {
  "name": "进行中",
  "color": "#3B82F6",
  "group": "started",
  "sort_order": 131070.0
 },
 {
  "name": "已完成",
  "color": "#10B981",
  "group": "completed",
  "sort_order": 196605.0
 },
 {
  "name": "已取消",
  "color": "#6B7280",
  "group": "cancelled",
  "sort_order": 262140.0
 }
]'::jsonb, '[
 {
  "name": "bug",
  "color": "#EF4444"
 },
 {
  "name": "feature",
  "color": "#3B82F6"
 },
 {
  "name": "docs",
  "color": "#8B5CF6"
 }
]'::jsonb,
       '[]'::jsonb, '[]'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM project_templates
                  WHERE is_builtin AND workspace_id IS NULL AND name = '基础模板')UNION ALL
SELECT gen_random_uuid(), now(), now(), NULL::uuid,
       '敏捷研发模板', 'Scrum 迭代流：backlog 列 + 研发标签', TRUE,
       '[
 {
  "name": "Backlog",
  "color": "#94A3B8",
  "group": "backlog",
  "sort_order": 100.0
 },
 {
  "name": "待办",
  "color": "#9CA3AF",
  "group": "unstarted",
  "is_default": true,
  "sort_order": 65535.0
 },
 {
  "name": "进行中",
  "color": "#3B82F6",
  "group": "started",
  "sort_order": 131070.0
 },
 {
  "name": "已完成",
  "color": "#10B981",
  "group": "completed",
  "sort_order": 196605.0
 },
 {
  "name": "已取消",
  "color": "#6B7280",
  "group": "cancelled",
  "sort_order": 262140.0
 }
]'::jsonb, '[
 {
  "name": "frontend",
  "color": "#22D3EE"
 },
 {
  "name": "backend",
  "color": "#A78BFA"
 },
 {
  "name": "bug",
  "color": "#EF4444"
 },
 {
  "name": "tech-debt",
  "color": "#F59E0B"
 }
]'::jsonb,
       '[]'::jsonb, '[]'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM project_templates
                  WHERE is_builtin AND workspace_id IS NULL AND name = '敏捷研发模板')UNION ALL
SELECT gen_random_uuid(), now(), now(), NULL::uuid,
       '产品设计模板', '设计评审流：需求/设计/评审目录 + 交付标签', TRUE,
       '[
 {
  "name": "待办",
  "color": "#9CA3AF",
  "group": "unstarted",
  "is_default": true,
  "sort_order": 65535.0
 },
 {
  "name": "设计中",
  "color": "#3B82F6",
  "group": "started",
  "sort_order": 131070.0
 },
 {
  "name": "评审中",
  "color": "#F59E0B",
  "group": "started",
  "sort_order": 140000.0
 },
 {
  "name": "已完成",
  "color": "#10B981",
  "group": "completed",
  "sort_order": 196605.0
 }
]'::jsonb, '[
 {
  "name": "视觉",
  "color": "#EC4899"
 },
 {
  "name": "交互",
  "color": "#06B6D4"
 },
 {
  "name": "用研",
  "color": "#84CC16"
 }
]'::jsonb,
       '[]'::jsonb, '[
 {
  "name": "需求文档"
 },
 {
  "name": "设计稿"
 },
 {
  "name": "评审记录",
  "parent_path": "设计稿"
 }
]'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM project_templates
                  WHERE is_builtin AND workspace_id IS NULL AND name = '产品设计模板');

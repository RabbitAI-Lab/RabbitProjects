export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "scope-enum": [
      2,
      "always",
      [
        "web",
        "admin",
        "space",
        "live",
        "api",
        "proxy",
        "ui",
        "editor",
        "types",
        "shared-state",
        "tailwind-config",
        "deps",
        "ci",
        "docs",
        "compose",
      ],
    ],
  },
};

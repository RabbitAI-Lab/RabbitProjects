"""空转断言扫描（TC-INF4-016 v2）：假资源 goto 只允许出现在错误分支测试。

v1 按括号深度提取 test 块，字符串里的 `{` 会打断计数 → 漏报。
v2 按 goto 调用点向上找最近 `test(` 标题，判断是否错误分支。
退出码 0=干净；1=有违规（打印清单）。
"""
import pathlib
import re
import sys

FAKE = re.compile(r"__(?:no_such\w*|bogus\w*|definitely\w*)__")
ERR_BRANCH = re.compile(r"错误|失效|invalid|不白屏|越权")

bad: list[str] = []
for f in sorted(pathlib.Path(__file__).parent.glob("*.spec.ts")):
    lines = f.read_text(encoding="utf-8").splitlines()
    # 预计算每个 test( 标题的起始行
    title_starts: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = re.search(r'test\((["\'])(.+?)\1', ln)
        if m:
            title_starts.append((i, m.group(2)))
    for i, ln in enumerate(lines):
        m = re.search(r"page\.goto\([\"'`](.+?)[\"'`]", ln)
        if not m or not FAKE.search(m.group(1)):
            continue
        # 向上找最近的 test 标题（同文件内goto 必在某个 test 里）
        title = next((t for start, t in reversed(title_starts) if start <= i), "")
        if not ERR_BRANCH.search(title):
            bad.append(f"{f.name}:{i + 1} [{title[:36]}] goto({m.group(1)[:44]})")

for b in bad:
    print(f"  ✗ {b}")
sys.exit(1 if bad else 0)

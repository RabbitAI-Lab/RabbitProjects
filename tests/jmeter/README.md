# JMeter 接口测试

Sprint 0 接口测试交付两套互补脚本：

## 1. `sprint-0-flow.jmx`（JMeter 5.6+ 性能压测脚本）

JMeter 官方模板结构（HTTPArgument + use_equals），可被 `jmeter -n -t ...` 加载。
用于性能压测（多线程 / 持续时间 / 吞吐量），不在 CI 单线程端到端流程里跑。

### 安装
```bash
# 1. 安装 JDK 17（JMeter 5.6 要求）
curl -s "https://get.sdkman.io" | bash
sdk install java 17.0.20-kona

# 2. 下载 JMeter 5.6.3
curl -fL -o ~/apache-jmeter-5.6.3.zip "https://dlcdn.apache.org//jmeter/binaries/apache-jmeter-5.6.3.zip"
unzip -q ~/apache-jmeter-5.6.3.zip -d ~/
ln -sf ~/apache-jmeter-5.6.3/bin/jmeter /opt/homebrew/bin/jmeter
```

### 跑（性能压测）
```bash
export JAVA_HOME=~/.sdkman/candidates/java/17.0.20-kona
export PATH=$JAVA_HOME/bin:/opt/homebrew/bin:$PATH
jmeter -n -t tests/jmeter/sprint-0-flow.jmx \
       -l result.jtl \
       -e -o report \
       -Jhost=localhost -Jport=8000
```

## 2. `sprint-0-flow.py`（CI 单线程端到端断言）

Python 等价版（10 步流程）。**解决 JMeter + Django CSRF 跨 sampler 流转**的边界问题
（登录后 token rotation）—— 每步状态变更前**重新拉 csrf token**，避免 403。

为什么需要这个：JMeter 5.6 与 Django 5 CSRF 头部 + 登录后 rotate 的组合在 sampler 间
传递有兼容性 bug，**用 Python urllib 直接走**对端到端断言最稳。

### 跑
```bash
# 默认连 http://localhost:8000
python3 tests/jmeter/sprint-0-flow.py

# 或显式指定 base
python3 tests/jmeter/sprint-0-flow.py http://api.example.com
```

### 覆盖（10 步端到端）
1. `GET /api/v1/health/` — DB 健康
2. `GET /api/v1/auth/csrf-token/` — CSRF 令牌
3. `POST /api/v1/auth/sign-up/` — 注册（带 X-CSRFToken + Origin）
4. **重新拉 csrf**（登录后 token rotation）→ `GET /api/v1/users/me/` — 当前用户
5. `POST /api/v1/workspaces/{slug}/projects/` — 建项目（identifier 大写 PYT）
6. `GET /api/v1/workspaces/{slug}/projects/{id}/states/` — 4 态种子（断言 待办/进行中/已完成）
7. `POST .../issues/` — 建任务
8. `PATCH .../issues/{iid}/` — 拖拽改状态（state_id + sort_order）
9. `GET .../issues/?ordering=sort_order` — 验证刷新一致（断言 state_group=started）
10. 清 cookie 后越权访问 → HTTP 403（无认证）

### 期望结果
```
🎉 ALL 10 STEPS PASSED — 接口测试通过
```

## JMeter jmx 与 Python 脚本的关系

- **jmx**：性能压测（100 并发 / 持续 60s / 报告输出）—— 已在 JMeter 5.6.3 加载校验
- **py**：CI 端到端断言（单线程 / 状态精确 / 失败即非零退出）

两者覆盖同一业务流但侧重点不同：jmx 跑吞吐与稳定性、py 跑业务正确性。

## 前置
- API 在 http://localhost:8000 运行（连接真实 PG 容器 `postgres:17-alpine`）
- PG schema 已按 `tests/e2e/PG_README.md` 建好（26 张业务表 + btree_gin + pg_trgm 扩展）

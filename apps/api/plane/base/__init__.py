"""plane.base —— 框架层（INFRA-004）。

承载错误码注册表、AppException、信封处理器、成功信封装配、六件套中间件
与跨中间件 contextvar；不依赖任何业务模型，可被 plane.app / plane.api /
plane.space 三个 API 分组无差别复用（api-conventions.md §2.1）。
"""

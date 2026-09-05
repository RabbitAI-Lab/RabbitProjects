"""按功能域拆分的路由片段（sprint-1）。

sprint-0 把全部 path() 平铺在 plane/app/urls.py。sprint-1 一次性新增
9 个功能域的端点，若继续平铺会让并行开发在同一文件上互相踩踏，
故每个域独占一个模块导出 ``urlpatterns``，由 plane/app/urls.py 汇总拼接。

新增域：在本目录加 <域>.py，导出 urlpatterns，再到 urls.py 的 FEATURE_MODULES 登记。
"""

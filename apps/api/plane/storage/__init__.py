"""对象存储客户端封装（INFRA-002 / FILE-001 / AUTH-004 共用）。

P1 落点：头像直传 presign（boto3 S3v4 → MinIO）；完整 `MinIO` 客户端（mc）
由 deploy/compose/init 建桶，API 进程只持有 SDK 凭证（AWS_S3_* 环境变量）。
"""

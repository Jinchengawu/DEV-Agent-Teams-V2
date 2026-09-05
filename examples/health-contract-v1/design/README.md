# 健康状态交付合同

`health-contract-v1` 定义三个可显示的服务状态：`ok`、`degraded`、`unavailable`。
响应必须含 `status` 与固定 `version`，不接受额外字段。
JSON Schema 与正反例在 `design/`；下游只接收经产品封装的 Artifact，不读取本仓路径。

后端 `GET /health?status=<状态>` 对三个有效值返回 HTTP 200；默认 `ok`。
未知、空或重复状态参数返回 HTTP 400，未知路由返回 HTTP 404。
前端通过同源 `/api/health` 读取后端响应；收到非法响应或网络失败时显示 `unavailable`
以及可见的错误提示，不把无效数据当成健康状态。

QA 通过产品提供的 loopback URL 操作真实页面，覆盖三个有效状态与非法响应。
这个小型合同用于验证类型、业务测试、构建、HTTP 和浏览器闭环，不代表真实生产监控。

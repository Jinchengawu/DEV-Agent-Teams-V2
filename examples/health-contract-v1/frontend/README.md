# 健康状态前端

Vanilla TypeScript + Vite 页面。显式安装锁定依赖后，运行 `pnpm typecheck`、
`pnpm test:ci` 与 `pnpm build`；产品验证器使用已资格化的工具和固定配置运行相同职责。
`?status=ok|degraded|unavailable` 选择状态，页面经同源 `/api/health` 读取真实后端。
跨仓服务编排与代理由产品负责，本仓不读取后端或 Design 的文件路径。

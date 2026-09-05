# 健康状态 QA

产品先物化经过验证的前后端 Artifact、启动真实后端和前端页面，
再注入 `ATOS_QA_BASE_URL` 与资格化的 `ATOS_CHROMIUM_EXECUTABLE`。
运行 `python -I -B -m unittest discover -s tests -v`。
四个固定用例只读取 HTTP 页面，缺少端点或浏览器时失败，不跳过测试。
测试不启动服务、不读取其他仓库目录，也不保存浏览器状态。

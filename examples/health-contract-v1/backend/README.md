# 健康状态后端

运行 `python -I -B src/server.py --port 8000`，仅监听 `127.0.0.1`。
运行 `python -I -B -m unittest discover -s tests -v` 验证真实 HTTP 合同。
三个有效状态均返回 HTTP 200；非法状态返回 400。服务不需要第三方 Python 依赖。

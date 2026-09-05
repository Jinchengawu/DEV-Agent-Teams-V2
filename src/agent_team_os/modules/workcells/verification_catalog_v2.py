"""完整四仓的产品固定政策。命令模板中的路径仅由产品执行器填充。"""

from ...shared.verification import VerificationProfileV2


def fullstack_profiles() -> tuple[VerificationProfileV2, ...]:
    python = ("python", "-I", "-B", "{runner}/verify.py")
    return (
        VerificationProfileV2(
            revision=1,
            timeout_seconds=120,
            environment={"CI": "1"},
            id="design-contract-v1",
            name="Design 合同与正反向量",
            workcell_key="design",
            tool_names=("python",),
            commands=((*python, "design", "{workspace}", "{result}", "{inputs}"),),
            config_paths=("verification.json",),
            dependency_names=("runner", "jsonschema"),
            output_contract="health-design-v1",
        ),
        VerificationProfileV2(
            revision=1,
            timeout_seconds=120,
            environment={"CI": "1"},
            id="frontend-ts-vite-vitest-v1",
            name="Frontend TypeScript / Vitest / Vite",
            workcell_key="frontend",
            tool_names=("node",),
            commands=(
                (
                    "node",
                    "{node_modules}/typescript/bin/tsc",
                    "--noEmit",
                    "-p",
                    "{workspace}/tsconfig.json",
                ),
                (
                    "node",
                    "{node_modules}/vitest/vitest.mjs",
                    "run",
                    "--config",
                    "{config}/vitest.config.mjs",
                    "--reporter=json",
                    "--outputFile={result}",
                ),
                (
                    "node",
                    "{node_modules}/vite/bin/vite.js",
                    "build",
                    "--config",
                    "{config}/vite.config.mjs",
                    "--outDir",
                    "{build}",
                ),
            ),
            config_paths=(
                "verification.json",
                "package.json",
                "pnpm-lock.yaml",
                "tsconfig.json",
                "vite.config.ts",
                "index.html",
            ),
            dependency_names=("runner", "node_modules"),
            input_contracts=("health-design-v1",),
            output_contract="health-frontend-dist-v1",
        ),
        VerificationProfileV2(
            revision=1,
            timeout_seconds=120,
            environment={"CI": "1"},
            id="backend-python-http-v1",
            name="Backend Python 测试与真实 HTTP 合同",
            workcell_key="backend",
            tool_names=("python",),
            commands=(
                (*python, "unittest", "{workspace}", "{result}", "{inputs}"),
                (*python, "backend-http", "{workspace}", "{result}", "{inputs}"),
            ),
            config_paths=("verification.json",),
            dependency_names=("runner", "jsonschema"),
            input_contracts=("health-design-v1",),
            output_contract="health-backend-runtime-v1",
        ),
        VerificationProfileV2(
            revision=1,
            timeout_seconds=120,
            environment={"CI": "1"},
            id="qa-playwright-artifacts-v1",
            name="QA 真实浏览器与上游产物集成",
            workcell_key="qa",
            tool_names=("python",),
            commands=((*python, "qa", "{workspace}", "{result}", "{inputs}"),),
            config_paths=("verification.json",),
            dependency_names=("runner", "playwright", "chromium"),
            input_contracts=(
                "health-design-v1",
                "health-frontend-dist-v1",
                "health-backend-runtime-v1",
            ),
        ),
    )
